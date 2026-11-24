"""Run the two M5 conditions or score saved runs without model access."""

import argparse
import fcntl
import hashlib
import json
import logging
import os
import signal
import subprocess
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
from lawbot.answer_format import UNVERIFIED, UNVERIFIED_GROUNDS_HEADER, calculator_evidence, review_answer
from lawbot.api_client import REQUEST_ID
from lawbot.calc_gate import is_calculation_question
from lawbot.citation import OK, article_keys, check_spans
from lawbot.inference import OBSERVER, InferenceControlError, InferenceStopped, failure_fields
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
STEPS = 'steps.jsonl'
STOP_STATES = ('interrupted', 'time_limit')


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


def unmarked_exposure(shown, review, hits, articles, *, verified=frozenset()):
    """Count unmarked occurrences in the actual displayed text, not the model's draft."""
    if not shown:
        return 0
    # Ground warnings contain only excerpts and may have lost their original law context.
    if review is not None and review.structured and not review.refused:
        shown, _, warnings = shown.partition(UNVERIFIED_GROUNDS_HEADER)
    else:
        warnings = ''
    return len(
        {
            c.end
            for c in check_spans(shown, hits, articles, verified=verified)
            if c.verdict != OK and not shown.startswith(UNVERIFIED, c.end)
        }
        | {
            len(shown) + 1 + c.end
            for c in check_spans(warnings, hits, articles)
            if not warnings.startswith(UNVERIFIED, c.end)
        }
    )


def citation_rows(text, spans):
    return [
        {
            'text': text[c.start : c.end],
            'start': c.start,
            'end': c.end,
            'law': c.law,
            'article_id': c.article_id,
            'verdict': c.verdict,
            'attribution': c.attribution,
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
    stopped = False
    error = None
    try:
        with closing(invoke(report=report, trace=True)) as stream:
            for stage, hits, raw, st in stream:
                if stage in ('clarify', 'done'):
                    break
    except InferenceStopped:
        stopped = True
    except service.AskError as exc:
        error = exc
    if outcome:
        st, status, kind = outcome
        hits = st.get('_provided_hits', hits)
    else:
        status, kind = 'error', 'missing_completion'

    verified = calculator_evidence(st.get('_calculation', {}))
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
    if stopped:
        result['verdict'] = 'interrupted'
    elif stage == 'clarify' and not error:
        original = st['_clarify_attempts'][-1]['parsed']['question']
        result.update(
            verdict='clarify',
            shown=raw,
            clarification_raw=original,
            conclusion_citations=citation_rows(original, check_spans(original, hits, articles)),
            unmarked_exposure=unmarked_exposure(raw, None, hits, articles),
        )
    elif stage == 'done' and not error:
        review = review_answer(raw, hits, articles, verified=verified)
        verdict, notice, text, _, _ = answer_display(
            raw, hits, st, calculated=st.get('_calculation', {}).get('status') == 'calculated', review=review
        )
        st['answer_verdict'] = verdict
        result.update(
            verdict=verdict,
            shown=notice or text,
            conclusion_citations=citation_rows(review.conclusion, review.conclusion_cites),
            ground_citations=citation_rows(review.grounds, review.ground_cites),
            unmarked_exposure=unmarked_exposure(text, review, hits, articles, verified=verified),
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
        error_stage = error.stage if isinstance(error, service.AskError) else (stage or 'generation')
        result['error'] = {'kind': kind} | failure_fields(error_stage, error, st.get('request_id'))
    elif reason := calc.get('router_reason'):
        result['router_reason'] = reason
    if outcome:
        service.log_request(app, st, 'error' if error else status, result.get('error', {}).get('kind', kind))
    return result, hits, error


def seconds(ms):
    return ms / 1000 if ms is not None else None


def run_item(app, row, condition, replies, articles, *, turn=lambda _: None):
    question = row['question']
    turn('initial')
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
    result['clarify_question'] = initial['shown']
    followup = initial['clarification_raw']
    if row['set'] == 'fact_missing':
        turn('resumed')
        reply = result['reply'] = replies[row['id']]
        resumed, _, error = run_turn(
            app, articles, partial(service.answer_after_clarification, app, question, hits, followup, reply)
        )
        result['resumed'] = resumed
        result['system_seconds'] += resumed['timing']['complete']
    else:
        turn('diagnostic')
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


def append_record(stream, row):
    print(json.dumps(row, ensure_ascii=False), file=stream, flush=True)
    if hasattr(stream, 'name'):
        os.fsync(stream.fileno())


def append_step(journal, event):
    try:
        append_record(journal, event)
    except OSError as exc:
        raise InferenceControlError('Cannot persist inference boundary') from exc


def guarded_item(app, row, condition, replies, articles, index, stop, journal):
    active_turn, sent, blocked = 'initial', False, None

    def turn(name):
        nonlocal active_turn
        active_turn = name

    def observe(event, stage, request_id, exc):
        nonlocal sent, blocked
        if event == 'send' and (blocked := stop()):
            raise InferenceStopped(blocked)
        if journal is not None:
            entry = {'index': index, 'turn': active_turn, 'event': event, 'stage': stage, 'request_id': request_id}
            append_step(journal, entry | (failure_fields(stage, exc, request_id) if exc else {}))
            if event == 'send' and (blocked := stop()):
                append_step(journal, entry | {'event': 'blocked'})
                raise InferenceStopped(blocked)
        if event == 'send':
            sent = True

    observer_token = OBSERVER.set(observe)
    try:
        result, error = run_item(app, row, condition, replies, articles, turn=turn)
    finally:
        OBSERVER.reset(observer_token)
    return result, error, sent, blocked


def run_questions(app, rows, condition, replies, output, *, stop=lambda: None, sensitive=(), journal=None, offset=0):
    articles = article_keys(app.retriever)
    failure_streak = 0
    for index, row in enumerate(rows, offset):
        if reason := stop():
            return reason, None
        result, error, sent, blocked = guarded_item(app, row, condition, replies, articles, index, stop, journal)
        if blocked:
            if sent:
                append_record(output, safe_record(result | {'invalid': 'incomplete_item'}, sensitive))
            return blocked, None
        append_record(output, safe_record(result, sensitive))
        if error and (not isinstance(error, service.AskError) or error.stage != 'router'):
            return 'request_error', error
        decision = result['initial']['clarification']
        if decision.get('failure') == 'call_error':
            failure_streak += 1
            if failure_streak == 3:
                return 'clarify_unavailable', None
        elif decision['status'] != 'skipped':
            failure_streak = 0
    return 'complete', None


def read_jsonl(path, *, repair_tail=False):
    """A killed process can leave only the final, unterminated append incomplete."""
    with path.open('r+b' if repair_tail else 'rb') as stream:
        records = []
        while line := stream.readline():
            if not line.endswith(b'\n') and repair_tail:
                stream.truncate(stream.tell() - len(line))
                os.fsync(stream.fileno())
                break
            records.append(json.loads(line))
        return records


def read_answers(output):
    return read_jsonl(output / ANSWERS)


def invalidate_segment(manifest, end, reason):
    start = manifest['segment_start']
    if start < end:
        manifest['invalid_segments'].append({'start': start, 'end': end, 'reason': reason})


def recover_segment(output, rows, manifest):
    answers = read_jsonl(output / ANSWERS, repair_tail=True)
    remaining = remaining_items(rows, answers)
    events = read_jsonl(output / STEPS, repair_tail=True)
    offset = len(answers)
    if any(event['index'] > offset for event in events):
        raise ValueError('단계 기록 순서가 문항 기록과 일치하지 않습니다.')
    pending = sum(
        (event['event'] == 'send') - (event['event'] == 'blocked') for event in events if event['index'] == offset
    )
    if pending and remaining:
        row = remaining[0]
        placeholder = {k: row[k] for k in ('set', 'id', 'question')} | {'invalid': 'unverified_inflight'}
        with (output / ANSWERS).open('a', encoding='utf-8') as stream:
            append_record(stream, placeholder)
        answers.append(placeholder)
    invalidate_segment(manifest, len(answers), 'missing_final_configuration')
    return answers


def valid_records(records, manifest):
    invalid = {i for r in manifest.get('invalid_segments', []) for i in range(r['start'], r['end'])}
    return [row for i, row in enumerate(records) if i not in invalid and not row.get('invalid')]


def remaining_items(rows, done):
    if [(r['set'], r['id']) for r in done] != [(r['set'], r['id']) for r in rows[: len(done)]]:
        raise ValueError('기록된 문항이 고정 세트 순서와 다릅니다.')
    return rows[len(done) :]


def save_manifest(output, manifest):
    tmp = output / '.run.json.tmp'
    with tmp.open('w', encoding='utf-8') as stream:
        append_record(stream, manifest)
    tmp.replace(output / MANIFEST)


def checked_run(output):
    manifest = read_json(output / MANIFEST)
    if manifest['state'] not in (
        *STOP_STATES,
        'complete',
        'request_error',
        'clarify_unavailable',
        'configuration_changed',
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
                manifest = read_json(args.output / MANIFEST)
                if manifest['condition'] != args.condition:
                    raise ValueError('재개 조건이 저장된 평가 조건과 다릅니다.')
                if manifest['state'] not in ('running', 'unverified'):
                    checked_run(args.output)
            else:
                args.output.mkdir(mode=0o700)
                (args.output / ANSWERS).touch(mode=0o600, exist_ok=False)
                (args.output / STEPS).touch(mode=0o600, exist_ok=False)
                manifest = {'condition': args.condition}
            output = args.output
            stage = '실행 전 구성 확인'
            before = safe_record(capture_config(cfg, client, preflight=True), sensitive)
            if args.resume and comparable(manifest['before']) != comparable(before):
                raise ValueError('재개 구성이 최초 실행 구성과 다릅니다.')
            manifest.setdefault('before', before)
            manifest.setdefault('invalid_segments', [])
            if args.resume and manifest['state'] in ('running', 'unverified'):
                done = recover_segment(output, rows, manifest)
            else:
                done = read_answers(output)
            rows = remaining_items(rows, done)
            manifest['segment_start'] = len(done)
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
                    app = service.load_app(cfg, cached_index_only=True)
                    stage = '질문 평가'
                    with (
                        (output / ANSWERS).open('a', encoding='utf-8') as stream,
                        (output / STEPS).open('a', encoding='utf-8') as journal,
                    ):
                        state, error = run_questions(
                            app,
                            rows,
                            args.condition,
                            replies,
                            stream,
                            stop=stop,
                            sensitive=sensitive,
                            journal=journal,
                            offset=len(done),
                        )
                state = state or 'complete'
            finally:
                try:
                    after = safe_record(capture_config(cfg, client), sensitive)
                    if comparable(before) != comparable(after):
                        invalidate_segment(manifest, len(read_answers(output)), 'configuration_changed')
                        state = 'configuration_changed'
                    manifest['after'] = after
                finally:
                    manifest['state'] = state if manifest['after'] is not None and state else 'unverified'
                    save_manifest(output, manifest)
            if error:
                raise error
            if state == 'configuration_changed':
                raise ValueError('실행 전후 구성이 바뀌어 이 구간은 무효입니다.')
            print(f'평가 상태: {state}', flush=True)
            return int(state == 'clarify_unavailable')
    except (
        httpx.HTTPError,
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        subprocess.CalledProcessError,
        service.AskError,
    ) as exc:
        report_failure(
            exc.stage if isinstance(exc, service.AskError) else stage,
            exc,
            output,
            request_id=REQUEST_ID.get() if stage == '질문 평가' else None,
        )
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
            valid = valid_records(records, manifest)
            result, _ = scoring.score(rows, valid)
            result = {
                'condition': manifest['condition'],
                'state': manifest['state'],
                'invalid': len(records) - len(valid),
                'invalid_segments': manifest.get('invalid_segments', []),
            } | result
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
            if len(valid_records(base_rows, base)) != len(rows) or len(valid_records(new_rows, new)) != len(rows):
                raise ValueError('무효 구간이 있는 실행은 채택 기준을 비교할 수 없습니다.')
            result = scoring.compare(rows, base_rows, new_rows)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        report_failure(args.command, exc, None)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
