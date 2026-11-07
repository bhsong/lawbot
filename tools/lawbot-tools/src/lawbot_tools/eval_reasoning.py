"""Run the fixed legal reasoning validation set separately from the standard eval."""

import argparse
import csv
import fcntl
import hashlib
import json
import logging
import os
import subprocess
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from lawbot import deploy, envfile, llm, manifest, service, settings
from lawbot.api_client import REQUEST_ID, ModelServerError
from lawbot.citation import citations, hit_key, refused
from lawbot.paths import DATA, ROOT

QUESTIONS = DATA / 'v5' / 'qa_reasoning_val.csv'


def load_questions():
    with QUESTIONS.open(encoding='utf-8-sig', newline='') as source:
        return list(csv.DictReader(source))


def run_questions(app, model, fmt, rows, output):
    """Record every response and stop on a request failure without retrying."""
    groups = defaultdict(Counter)
    overall = Counter()
    for row in rows:
        result = {k: row[k] for k in ('id', 'error_type', 'question', 'article_ids')}
        error = 'missing_completion'
        try:
            with closing(service.ask(app, row['question'], model, fmt=fmt)) as stream:
                for stage, hits, raw, stats in stream:
                    if stage == 'load':
                        error = 'chat_model_not_loaded'
                        break
                    if stage == 'done':
                        error = None if raw.strip() else 'empty_answer'
                        break
        except service.AskError as exc:
            error = exc.kind
        if error:
            print(json.dumps(result | {'error': error}, ensure_ascii=False), file=output, flush=True)
            print(f'{row["id"]} 실패 ({error})', flush=True)
            return None

        reference = set(row['article_ids'].split('|'))
        context = [hit_key(hit) for hit in hits]
        cited = [f'{law} {article}' if law else article for law, article in citations(raw)]
        is_refusal = refused(raw)
        scores = {
            'all_reference_retrieved': reference.issubset(context),
            'all_reference_cited': not is_refusal and reference.issubset(cited),
            'refused': is_refusal,
        }
        result.update(
            scores,
            context=context,
            cited=cited,
            answer=raw,
            finish=stats.get('finish'),
            total_seconds=stats.get('total'),
            peak_memory_gb=stats.get('peak_gb'),
            request_id=stats.get('request_id'),
        )
        print(json.dumps(result, ensure_ascii=False), file=output, flush=True)
        counts = {'questions': 1, **{name: int(value) for name, value in scores.items()}}
        groups[row['error_type']].update(counts)
        overall.update(counts)
        print(f'{row["id"]} 완료 ({stats.get("total", 0):.1f}초)', flush=True)
    return {'overall': dict(overall), 'by_error_type': {name: dict(counts) for name, counts in groups.items()}}


def snapshot(cfg, client, *, preflight=False):
    """Read runtime metadata without issuing an inference request."""
    if preflight and time(6, 20) <= datetime.now(ZoneInfo('Asia/Seoul')).time() <= time(6, 50):
        raise SystemExit('조문 자동 갱신 시간대입니다.')
    health = client.get('/health?runtime=1')
    health.raise_for_status()
    health = health.json()
    if not health.get('ready') or not health.get('commit') or health.get('max_concurrent') != 1:
        raise SystemExit('모델 서버 준비 상태, 커밋, 동시 생성 상한 1을 확인하세요.')
    listing = client.get('/v1/models')
    listing.raise_for_status()
    models = {model['id']: model for model in listing.json()['data']}
    profile, rr, dc = cfg.profile, cfg.reranker, cfg.decree
    spec = profile.models[0]
    missing = service.missing_models(models.values(), profile, rr, False)
    if missing:
        raise SystemExit('평가에 필요한 모델 id와 종류를 확인하세요.')
    models = [
        {k: models[mid][k] for k in ('id', 'type', 'loaded', 'revision')}
        for mid in (profile.emb_model, rr.model, spec.model)
    ]
    if any(not model['revision'] or (preflight and not model['loaded']) for model in models):
        raise SystemExit('필요한 모델의 revision과 상주 상태를 확인하세요.')
    law, combined = deploy.article_paths('v5', {})
    data = manifest.load(combined)
    if not data or not data.get('dataset_version'):
        raise SystemExit('v5 조문 매니페스트를 확인하세요.')
    files = (law, combined, spec.system_file or profile.system_file, QUESTIONS, Path(__file__))
    return {
        'webapp_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'model_server_commit': health['commit'],
        'server_max_concurrent': health['max_concurrent'],
        'models': models,
        'dataset_version': data['dataset_version'],
        'sha256': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
        'profile': profile.name,
        'format': spec.fmt,
        'temperature': llm.TEMPERATURE,
        'max_tokens': 1024,
        'read_timeout': cfg.read_timeout,
        'embedding_max_seq': profile.max_seq,
        'law_top_k': service.TOP_K,
        'reranker': asdict(rr),
        'decree': asdict(dc),
    }


def comparable(snapshot):
    return snapshot | {'models': [{k: v for k, v in model.items() if k != 'loaded'} for model in snapshot['models']]}


def main():
    parser = argparse.ArgumentParser(description='v5 법률 추론 회귀 검증 val (공유 모델 서버, 사전 승인 필요)')
    parser.add_argument('--output', type=Path, required=True, help='결과를 쓸 새 디렉터리 (부모 디렉터리 필요)')
    args = parser.parse_args()
    rows = load_questions()
    if [row['id'] for row in rows] != [f'ERR{i:03}' for i in range(1, 31)]:
        raise SystemExit('고정 검증셋 문항 ID와 순서를 확인하세요.')

    envfile.load()
    example = ROOT / '.env.dev.example'
    env = envfile.parse(example.read_text(encoding='utf-8'), example.name)
    env.update({key: os.environ.get(key, '') for key in ('LAWBOT_MODEL_SERVER', 'LAWBOT_MODEL_TOKEN')})
    env.update(LAWBOT_REQUEST_LOG=str(args.output / 'requests.jsonl'), LAWBOT_MODEL_READ_TIMEOUT='300')
    cfg = replace(settings.load(env), embed_cache=args.output / 'embeddings', server_wait=0)
    spec = cfg.profile.models[0]
    if cfg.profile.name != 'v5' or cfg.reranker is None or cfg.decree is None or spec.key not in cfg.decree.models:
        raise SystemExit('개발 설정 예시의 v5 프로필, 리랭커, 하위법령 첨부를 확인하세요.')

    # The web app startup log includes the model server address.
    logging.getLogger('lawbot').setLevel(logging.WARNING)
    try:
        with (
            QUESTIONS.open('rb') as lock,
            httpx.Client(
                base_url=cfg.model_server,
                headers={'Authorization': f'Bearer {cfg.model_token}'},
                timeout=10,
                trust_env=False,
            ) as client,
        ):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            args.output.mkdir()
            before = snapshot(cfg, client, preflight=True)
            (args.output / 'before.json').write_text(json.dumps(before, indent=2), encoding='utf-8')
            print('실행 전 확인 통과 · 30문항 · 동시 1', flush=True)
            try:
                REQUEST_ID.set('reasoning-val-index')
                app = service.load_app(cfg)
                with (args.output / 'answers.jsonl').open('x', encoding='utf-8') as output:
                    summary = run_questions(app, spec.key, spec.fmt, rows, output)
            finally:
                after = snapshot(cfg, client)
                (args.output / 'after.json').write_text(json.dumps(after, indent=2), encoding='utf-8')
    except (httpx.HTTPError, ModelServerError, RuntimeError, OSError) as exc:
        raise SystemExit(f'평가 중단 ({type(exc).__name__})') from None
    if comparable(before) != comparable(after):
        raise SystemExit('실행 구성이 바뀌어 결과가 무효입니다.')
    if summary is None:
        raise SystemExit('요청 오류로 평가를 중단했습니다.')
    report = json.dumps(summary, ensure_ascii=False, indent=2)
    (args.output / 'summary.json').write_text(report + '\n', encoding='utf-8')
    print(report)


if __name__ == '__main__':
    main()
