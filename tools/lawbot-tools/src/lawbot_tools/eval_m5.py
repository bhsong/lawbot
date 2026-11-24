"""Run the two M5 conditions or score saved runs without model access."""

import argparse
import fcntl
import hashlib
import json
import logging
import signal
import subprocess
import sys
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime, time, timedelta
from functools import partial
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from lawbot import service, settings
from lawbot.answer_format import UNVERIFIED, UNVERIFIED_GROUNDS_HEADER, review_answer
from lawbot.api_client import REQUEST_ID
from lawbot.calc_gate import is_calculation_question
from lawbot.citation import OK, article_keys, check_spans
from lawbot.llm import CLARIFY_MAX_TOKENS, CLARIFY_SYSTEM_FILE
from lawbot.paths import DATA
from lawbot.web import answer_display
from lawbot_tools import eval_m5_score as scoring
from lawbot_tools.eval_reasoning import QUESTIONS, comparable, evaluation_env, redact, report_failure, snapshot

SET_DIR = DATA / 'eval' / 'm5'
SETS = ('reasoning', 'fact_missing', 'out_of_scope', 'general')
KST = ZoneInfo('Asia/Seoul')
ANSWERS = 'answers.jsonl'
MANIFEST = 'run.json'


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def load_items():
    rows = []
    for name in SETS:
        items = read_json(SET_DIR / f'{name}.json')['items']
        by_id = {item['id']: item for item in items}
        if len(by_id) != len(items):
            raise ValueError(f'{name}: 중복 문항 ID')
        if name == 'general':
            sample = read_json(SET_DIR / 'general_sample200.json')['ids']
            if len(set(sample)) != 200 or len(sample) != 200 or not set(sample) <= by_id.keys():
                raise ValueError('일반 표본의 고정 ID 200개를 확인하세요.')
            items = [by_id[ident] for ident in sample]
        rows.extend(item | {'set': name} for item in items)
    facts = read_json(SET_DIR / 'fact_missing_replies.json')['items']
    replies = {item['id']: item['reply'] for item in facts}
    if len(replies) != len(facts) or replies.keys() != {r['id'] for r in rows if r['set'] == 'fact_missing'}:
        raise ValueError('사실 부족 문항의 고정 답 ID가 일치하지 않습니다.')
    return rows, replies


def deadline(now, stop):
    if time(6) <= now.time() < time(7):
        raise ValueError('06:00–07:00 KST에는 M5 평가를 시작할 수 없습니다.')

    def next_at(clock):
        when = datetime.combine(now.date(), clock, tzinfo=KST)
        return when if when >= now else when + timedelta(days=1)

    return min(next_at(time(6)), next_at(stop))


@contextmanager
def stop_on_interrupt():
    interrupted = False

    def receive(_sig, _frame):
        nonlocal interrupted
        interrupted = True

    previous = signal.signal(signal.SIGINT, receive)
    try:
        yield lambda: interrupted
    finally:
        signal.signal(signal.SIGINT, previous)


def provided_articles(hits):
    return [
        {k: hit.get(k) for k in ('law', 'article_id', 'title', 'score', 'rerank_score', 'emb_rank', 'definition_item')}
        | {'rank': rank, 'kind': 'definition' if 'definition_item' in hit else hit.get('kind', '법률')}
        for rank, hit in enumerate(hits, 1)
    ]


def unmarked_exposure(shown, review, hits, articles):
    """Count unmarked occurrences in the actual displayed text, not the model's draft."""
    if not shown:
        return 0
    # Ground warnings contain only excerpts and may have lost their original law context.
    if review.structured and not review.refused:
        shown, _, warnings = shown.partition(UNVERIFIED_GROUNDS_HEADER)
    else:
        warnings = ''
    return sum(
        c.verdict != OK and not shown.startswith(UNVERIFIED, c.end) for c in check_spans(shown, hits, articles)
    ) + sum(not warnings.startswith(UNVERIFIED, c.end) for c in check_spans(warnings, hits, articles))


def citation_rows(text, spans):
    return [
        {
            'text': text[c.start : c.end],
            'start': c.start,
            'end': c.end,
            'law': c.law,
            'article_id': c.article_id,
            'verdict': c.verdict,
        }
        for c in spans
    ]


def run_turn(app, articles, invoke):
    outcome = None

    def report(st, status, error):
        nonlocal outcome
        outcome = st, status, error

    started = perf_counter()
    stage, hits, raw, st = None, [], '', {}
    error = None
    try:
        with closing(invoke(report=report, trace=True)) as stream:
            for stage, hits, raw, st in stream:
                if stage in ('clarify', 'done'):
                    break
    except service.AskError as exc:
        error = exc
    if outcome:
        st, status, kind = outcome
        hits = st.get('_provided_hits', hits)
    else:
        status, kind = 'error', 'missing_completion'

    result = {
        'stage': stage,
        'request_id': st.get('request_id'),
        'provided': provided_articles(hits),
        'writer_messages': st.get('_writer_messages', []),
        'raw': None if stage == 'clarify' else raw,
        'clarification': {'status': 'skipped'}
        | {k.removeprefix('clarify_'): v for k, v in st.get('clarify_metrics', {}).items()}
        | {'attempts': st.get('_clarify_attempts', [])},
        'finish': st.get('finish'),
        'verdict': 'error',
        'shown': '',
        'conclusion_citations': [],
        'ground_citations': [],
        'unmarked_exposure': 0,
    }
    if stage == 'clarify' and not error:
        result.update(verdict='clarify', shown=raw)
    elif stage == 'done' and not error:
        review = review_answer(raw, hits, articles)
        verdict, notice, text, _, _ = answer_display(
            raw, hits, st, calculated=st.get('_calculation', {}).get('status') == 'calculated', review=review
        )
        st['answer_verdict'] = verdict
        result.update(
            verdict=verdict,
            shown=notice or text,
            conclusion_citations=citation_rows(review.conclusion, review.conclusion_cites),
            ground_citations=citation_rows(review.grounds, review.ground_cites),
            unmarked_exposure=unmarked_exposure(text, review, hits, articles),
        )
    elif not error:
        error = RuntimeError('요청이 최종 단계 없이 끝났습니다.')
    complete = perf_counter() - started
    calc = st.get('calculation_metrics', {})
    result['timing'] = {k: st.get(k) for k in service.MS_FIELDS} | {
        'complete': complete,
        'clarify_seconds': seconds(st.get('clarify_metrics', {}).get('clarify_ms')),
        'router_seconds': seconds(calc.get('router_ms')),
    }
    result['calls'] = st.get('call_counts', {}) | {
        'router': calc.get('router_calls', 0),
        'clarify': result['clarification'].get('calls', 0),
    }
    result['tokens'] = {
        'writer_input': st.get('prompt_tokens'),
        'writer_output': st.get('gen_tokens'),
        'router_input': calc.get('router_prompt_tokens'),
        'router_output': calc.get('router_completion_tokens'),
        'clarify_input': result['clarification'].get('prompt_tokens'),
        'clarify_output': result['clarification'].get('completion_tokens'),
    }
    if error:
        result['error'] = {'kind': kind, 'stage': error.stage if isinstance(error, service.AskError) else stage}
    elif result['clarification'].get('failure') == 'call_error':
        result['error'] = {'kind': 'call_error', 'stage': 'clarify'}
        error = RuntimeError('되묻기 판정의 모델 요청이 실패했습니다.')
    elif (reason := calc.get('router_reason')) and (reason == 'model_error' or reason.endswith('timeout')):
        result['error'] = {'kind': reason, 'stage': 'router'}
        error = RuntimeError('계산 라우터의 모델 요청이 실패했습니다.')
    if outcome:
        service.log_request(app, st, 'error' if error else status, result.get('error', {}).get('kind', kind))
    return result, hits, error


def seconds(ms):
    return ms / 1000 if ms is not None else None


def run_item(app, row, condition, replies, articles):
    question = row['question']
    initial, hits, error = run_turn(
        app,
        articles,
        partial(
            service.ask,
            app,
            question,
            clarify=condition == 'Aprime',
            calculation_candidate=is_calculation_question(question),
        ),
    )
    asked = initial['verdict'] == 'clarify'
    result = {k: row[k] for k in ('set', 'id', 'question')} | {
        'initial': initial,
        'unnecessary_clarification': asked and row['set'] != 'fact_missing',
        'system_seconds': initial['timing']['complete'],
    }
    if not asked or error:
        return result, error
    followup = result['clarify_question'] = initial['shown']
    if row['set'] == 'fact_missing':
        reply = result['reply'] = replies[row['id']]
        resumed, _, error = run_turn(
            app, articles, partial(service.answer_after_clarification, app, question, hits, followup, reply)
        )
        result['resumed'] = resumed
        result['system_seconds'] += resumed['timing']['complete']
    else:
        diagnostic, _, error = run_turn(app, articles, partial(service.write_from_context, app, question, hits))
        result['diagnostic'] = diagnostic
    return result, error


def safe_record(value, sensitive):
    if isinstance(value, str):
        return redact(value, sensitive)
    if isinstance(value, dict):
        return {key: safe_record(item, sensitive) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_record(item, sensitive) for item in value]
    return value


def run_questions(app, rows, condition, replies, output, *, stop=lambda: None, sensitive=()):
    articles = article_keys(app.retriever)
    for row in rows:
        if reason := stop():
            return reason, None
        result, error = run_item(app, row, condition, replies, articles)
        print(json.dumps(safe_record(result, sensitive), ensure_ascii=False), file=output, flush=True)
        print(f'{row["set"]}/{row["id"]} · {"오류" if error else "기록"}', flush=True)
        if error:
            return 'request_error', error
    return 'complete', None


def read_answers(output):
    with (output / ANSWERS).open(encoding='utf-8') as records:
        return [json.loads(line) for line in records]


def remaining_items(rows, done):
    if [(r['set'], r['id']) for r in done] != [(r['set'], r['id']) for r in rows[: len(done)]]:
        raise ValueError('기록된 문항이 고정 세트 순서와 다릅니다.')
    return rows[len(done) :]


def save_manifest(output, manifest):
    tmp = output / '.run.json.tmp'
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(output / MANIFEST)


def checked_run(output):
    manifest = read_json(output / MANIFEST)
    if manifest['state'] not in ('complete', 'interrupted', 'time_limit', 'request_error') or (
        comparable(manifest['before']) != comparable(manifest['after'])
    ):
        raise ValueError('실행 전후 구성이 검증되지 않은 결과입니다.')
    return manifest


def set_fingerprints():
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(SET_DIR.glob('*.json'))}


def check_grading_data(manifest):
    recorded = manifest['before']['sha256']
    if any(recorded.get(name) != digest for name, digest in set_fingerprints().items()):
        raise ValueError('현재 평가 세트가 실행 당시와 다릅니다.')


def capture_config(cfg, client, *, preflight=False):
    files = (CLARIFY_SYSTEM_FILE, *sorted(SET_DIR.glob('*.json')), Path(__file__), Path(scoring.__file__))
    return snapshot(cfg, client, preflight=preflight, extra_files=files) | {'clarify_max_tokens': CLARIFY_MAX_TOKENS}


def run(args, rows, replies):
    stage, output, sensitive = '실행 시각 확인', None, ()
    app_log = logging.getLogger('lawbot')
    level = app_log.level
    app_log.setLevel(logging.WARNING)
    try:
        stop_at = deadline(datetime.now(KST), args.stop)
        stage = '평가 설정 확인'
        env, sensitive = evaluation_env(args.output)
        cfg = replace(settings.load(env), server_wait=0)
        sensitive += tuple(value for url in (cfg.model_server, cfg.llm_url) for value in (url, urlsplit(url).netloc))
        sensitive += (cfg.model_token, cfg.llm_key)
        if cfg.profile.name != 'v5' or cfg.reranker is None or cfg.decree is None or cfg.llm_external:
            raise ValueError('v5, 리랭커, 하위법령 첨부, 상주 생성 서버를 확인하세요.')
        stage = '실행 폴더 준비'
        # Share the reasoning evaluation's lock; never run two approval-gated evaluations together.
        with (
            stop_on_interrupt() as interrupted,
            QUESTIONS.open('rb') as lock,
            httpx.Client(
                base_url=cfg.model_server,
                headers={'Authorization': f'Bearer {cfg.model_token}'},
                timeout=10,
                trust_env=False,
            ) as client,
        ):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.resume:
                manifest = checked_run(args.output)
                if manifest['condition'] != args.condition:
                    raise ValueError('재개 조건이 저장된 평가 조건과 다릅니다.')
                rows = remaining_items(rows, read_answers(args.output))
            else:
                args.output.mkdir(mode=0o700)
                (args.output / ANSWERS).touch(mode=0o600, exist_ok=False)
                manifest = {'condition': args.condition}
            output = args.output
            stage = '실행 전 구성 확인'
            before = safe_record(capture_config(cfg, client, preflight=True), sensitive)
            if args.resume and comparable(manifest['before']) != comparable(before):
                raise ValueError('재개 구성이 최초 실행 구성과 다릅니다.')
            manifest.setdefault('before', before)
            manifest.update(state='running', after=None)
            save_manifest(output, manifest)

            def stop():
                if interrupted():
                    return 'interrupted'
                if datetime.now(KST) >= stop_at:
                    return 'time_limit'
                return None

            error = None
            state = 'invalid'
            try:
                state = stop()
                if state is None and rows:
                    stage = '웹앱 초기화'
                    REQUEST_ID.set('m5-eval-index')
                    app = service.load_app(cfg)
                    stage = '질문 평가'
                    with (output / ANSWERS).open('a', encoding='utf-8') as stream:
                        state, error = run_questions(
                            app, rows, args.condition, replies, stream, stop=stop, sensitive=sensitive
                        )
                state = state or 'complete'
            finally:
                try:
                    after = safe_record(capture_config(cfg, client), sensitive)
                    if comparable(before) != comparable(after):
                        state = 'invalid'
                    manifest['after'] = after
                finally:
                    manifest['state'] = state if manifest['after'] is not None and state else 'invalid'
                    save_manifest(output, manifest)
            if error:
                raise error
            if state == 'invalid':
                raise ValueError('실행 전후 구성이 바뀌어 결과가 무효입니다.')
            print(f'평가 상태: {state}', flush=True)
            return 0
    except (
        httpx.HTTPError,
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        subprocess.CalledProcessError,
        service.AskError,
    ) as exc:
        report_failure(stage, exc, output, sensitive)
        return 1
    finally:
        app_log.setLevel(level)


def stop_time(value):
    try:
        t = datetime.strptime(value, '%H:%M:%S').time()
        if t.isoformat() == value:
            return t
    except ValueError:
        raise argparse.ArgumentTypeError('KST 시각을 HH:MM:SS로 입력하세요.') from None
    raise argparse.ArgumentTypeError('KST 시각을 HH:MM:SS로 입력하세요.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='M5 val 평가 (run은 사전 승인 필요, score/compare는 오프라인)')
    commands = parser.add_subparsers(dest='command', required=True)
    runner = commands.add_parser('run')
    runner.add_argument('--condition', required=True, choices=('A', 'Aprime'))
    runner.add_argument('--output', required=True, type=Path)
    runner.add_argument('--stop', type=stop_time, default=time(6))
    runner.add_argument('--resume', action='store_true')
    scorer = commands.add_parser('score')
    scorer.add_argument('output', type=Path)
    compare = commands.add_parser('compare')
    compare.add_argument('base', type=Path)
    compare.add_argument('new', type=Path)
    args = parser.parse_args(argv)
    try:
        rows, replies = load_items()
        if args.command == 'run':
            return run(args, rows, replies)
        if args.command == 'score':
            manifest = checked_run(args.output)
            check_grading_data(manifest)
            records = read_answers(args.output)
            remaining_items(rows, records)
            result, _ = scoring.score(rows, records)
            result = {'condition': manifest['condition'], 'state': manifest['state']} | result
        else:
            base, new = checked_run(args.base), checked_run(args.new)
            if base['condition'] != 'A' or new['condition'] != 'Aprime':
                raise ValueError('비교 순서는 A, Aprime입니다.')
            if comparable(base['before']) != comparable(new['before']):
                raise ValueError('두 평가 실행의 구성이 다릅니다.')
            check_grading_data(base)
            base_rows, new_rows = read_answers(args.base), read_answers(args.new)
            if remaining_items(rows, base_rows) or remaining_items(rows, new_rows):
                raise ValueError('고정 세트 전체를 기록한 두 평가만 비교할 수 있습니다.')
            result = scoring.compare(rows, base_rows, new_rows)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f'M5: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
