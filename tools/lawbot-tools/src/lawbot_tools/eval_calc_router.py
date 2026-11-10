"""Evaluate the calculation gate offline and, when requested, the Pro router on synthetic val."""

import argparse
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, time
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from lawbot import envfile, manifest, service, settings
from lawbot.api_client import ModelServerClient
from lawbot.calc_corpus import calculator_compatibility
from lawbot.calc_gate import is_calculation_question
from lawbot.calc_router import ROUTER_SYSTEM, route_calculation
from lawbot.llm import ROUTER_MAX_TOKENS, ROUTER_TIMEOUT, TEMPERATURE
from lawbot.paths import ROOT
from lawbot_common.api import HEALTH_PATH

VAL = ROOT / 'services/chatbot/tests/fixtures/calc_router_val.json'
SAMPLES = {'CR001', 'CR005', 'CR009', 'CR021', 'CR025', 'CR028', 'CR081', 'CR091'}
SEOUL = ZoneInfo('Asia/Seoul')


def _time_guard():
    if time(6, 20) <= datetime.now(SEOUL).time() < time(6, 50):
        raise SystemExit('06:20–06:50 KST에는 계산 평가를 실행할 수 없습니다.')


def _configuration(cfg, client):
    _time_guard()
    try:
        response = client.http.get(f'{HEALTH_PATH}?runtime=true', timeout=5)
        response.raise_for_status()
        health = response.json()
    except httpx.HTTPError, ValueError:
        raise SystemExit('공유 모델 서버의 준비 상태를 확인할 수 없습니다.') from None
    if not health.get('ready') or type(health.get('max_concurrent')) is not int:
        raise SystemExit('공유 모델 서버의 준비 상태 또는 동시 생성 상한을 확인할 수 없습니다.')
    pro = next(spec for spec in cfg.profile.models if spec.key == 'pro')
    advertised = {m['id']: m for m in client.models()}
    missing = service.missing_models(list(advertised.values()), cfg.profile, cfg.reranker, False)
    if missing or not advertised[pro.model]['loaded']:
        raise SystemExit('평가에 필요한 모델이 없거나 Pro 모델이 상주 중이 아닙니다.')
    return {
        'webapp_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'model_server_commit': health.get('commit'),
        'server_max_concurrent': health['max_concurrent'],
        'profile': cfg.profile.name,
        'dataset_version': (manifest.load(cfg.profile.articles) or {}).get('dataset_version'),
        'articles_sha256': manifest.sha256(cfg.profile.articles),
        'system_sha256': sha256(service.system_text(cfg.profile, pro).encode()).hexdigest(),
        'router_sha256': sha256(ROUTER_SYSTEM.encode()).hexdigest(),
        'temperature': TEMPERATURE,
        'router_max_tokens': ROUTER_MAX_TOKENS,
        'router_timeout_s': ROUTER_TIMEOUT,
        'answer_max_tokens': 1024,
        'embedding_max_seq': cfg.profile.max_seq,
        'models': {key: advertised[key].get('revision') for key in (pro.model, cfg.profile.emb_model)},
        'reranker': {
            'revision': advertised[cfg.reranker.model].get('revision'),
            'candidates': cfg.reranker.candidates,
        }
        if cfg.reranker
        else None,
        'decree': vars(cfg.decree) if cfg.decree else None,
    }


def _live(cases, output):
    _time_guard()
    envfile.load()
    env = {k: v for k, v in os.environ.items() if k not in ('LAWBOT_DB', 'LAWBOT_EXACT_CACHE_DB')}
    cfg = settings.load(env | {'LAWBOT_CORPUS': 'csv', 'LAWBOT_REQUEST_LOG': '', 'LAWBOT_EMBED_CACHE': '0'})
    if cfg.profile.name != 'v5' or cfg.llm_external or [m.key for m in cfg.profile.models] != ['pro']:
        raise SystemExit('개발 CSV v5 프로필과 공유 서버의 Pro 모델을 사용하세요.')
    compatibility = calculator_compatibility(cfg.profile.articles)
    if not compatibility or any(compatibility.values()):
        raise SystemExit('평가 코퍼스에서 두 계산기의 근거 조문이 일치해야 합니다.')
    client = ModelServerClient(cfg.model_server, cfg.model_token)
    before = _configuration(cfg, client)
    app = service.load_app(cfg)
    pro, _ = app.models.get('pro')
    routes, answers = [], []
    for case in cases:
        _time_guard()
        # Also run the negative controls directly to distinguish model errors from gate errors.
        routed = route_calculation(case['question'], pro.route_calculation, compatibility, reference_year=2026)
        calculator = routed.payload['calculator'] or 'none'
        status = routed.payload['status']
        routes.append(
            {
                'id': case['id'],
                'gate': is_calculation_question(case['question']),
                'calculator': calculator,
                'status': status,
                'expected_calculator': case['calculator'],
                'expected_status': case['status'],
                'reason': routed.reason,
                'router_ms': routed.elapsed_ms,
            }
        )
    for case in cases:
        if case['id'] not in SAMPLES:
            continue
        _time_guard()
        for stage, hits, raw, timing in service.ask(app, case['question'], 'pro', calculation_candidate=True):
            if stage == 'done':
                answers.append(
                    {
                        'id': case['id'],
                        'question': case['question'],
                        'answer': raw,
                        'calculation': timing.get('_calculation'),
                        'checks': timing.get('calculation_metrics'),
                        'articles': [f'{hit["law"]} {hit["article_id"]}' for hit in hits],
                    }
                )
    after = _configuration(cfg, client)
    report = {'before': before, 'after': after, 'valid': before == after, 'routes': routes, 'answers': answers}
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return 0 if report['valid'] else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--live', action='store_true', help='Call the shared Pro model on the synthetic val and samples.'
    )
    parser.add_argument('--output', type=Path, help='New JSON output file for the live run.')
    args = parser.parse_args(argv)
    if args.live and (args.output is None or args.output.exists() or not args.output.parent.is_dir()):
        parser.error('--live requires --output pointing to a new file in an existing directory')
    cases = json.loads(VAL.read_text(encoding='utf-8'))
    misses = [row['id'] for row in cases if is_calculation_question(row['question']) != (row['kind'] != 'general')]
    print(json.dumps({'val': len(cases), 'groups': Counter(row['kind'] for row in cases), 'gate_misses': misses}))
    return _live(cases, args.output) if args.live else int(bool(misses))


if __name__ == '__main__':
    raise SystemExit(main())
