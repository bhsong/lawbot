"""Evaluate the calculation gate offline and, when requested, the Pro router on synthetic val."""

import argparse
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, time
from hashlib import sha256
from pathlib import Path
from statistics import median, quantiles
from time import perf_counter
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx

from lawbot import envfile, manifest, service, settings
from lawbot.api_client import ModelServerClient
from lawbot.calc_corpus import calculator_compatibility
from lawbot.calc_gate import is_calculation_question
from lawbot.calc_router import PR1_ROUTER_SYSTEM, ROUTER_SYSTEM, route_calculation
from lawbot.llm import ROUTER_MAX_TOKENS, TEMPERATURE
from lawbot.paths import ROOT
from lawbot_common.api import HEALTH_PATH

VAL = ROOT / 'services/chatbot/tests/fixtures/calc_router_val.json'
FIXTURES = VAL.parent
SAMPLES = {'CR001', 'CR005', 'CR021', 'CR025', 'CR028', 'CR079', 'CR081', 'CR091'}
UNSUPPORTED_PR1 = {'CR041', 'CR043', 'CR048', 'CR050', 'CR052', 'CR057'}
POST_LIMITS = {'pr1': 190, 'labor-pr2': 383}
SEOUL = ZoneInfo('Asia/Seoul')
ANSWER_TIMINGS = ('ttft', 'first_text')
STAGE_TIMINGS = ('search', 'rerank', 'decree_search', 'decree_rerank', 'total')


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _evaluation_cases(suite):
    expected = _read(FIXTURES / 'calc_router_pr1_money.json')
    pr1 = [row | {'expected_results': expected[row['id']]} if row['id'] in expected else row for row in _read(VAL)]
    groups = {'pr1': pr1}
    samples = [row for row in pr1 if row['id'] in SAMPLES]
    if suite == 'pr1':
        return groups, samples

    labor = [row for path in sorted(FIXTURES.glob('*labor*val.json')) for row in _read(path)]
    for row in labor:
        if 'results' in row:
            row['expected_results'] = {result['label']: result['value'] for result in row['results']}
    old_answer = _read(FIXTURES / 'labor_severance_cr041.json')
    overrides = {
        'CR041': ('severance_pay', 'calculated', old_answer['expected_results']),
        'CR042': ('weekly_holiday_pay', 'guidance', None),
        'CR043': ('monthly_minimum_wage', 'calculated', {'월 최저임금': 2_156_880}),
        'CR045': ('premium_pay', 'guidance', None),
        'CR050': ('annual_leave_days', 'guidance', None),
    }
    bridge_ids = UNSUPPORTED_PR1 | set(overrides) | {'CR023', 'CR030', 'CR088', 'CR092'}
    bridge = []
    for row in pr1:
        if row['id'] in overrides:
            calculator, status, results = overrides[row['id']]
            updated = row | {
                'calculator': calculator,
                'status': status,
                'kind': 'labor_bridge',
                'expected_results': results,
            }
            if row['id'] == 'CR041':
                assert row['question'] == old_answer['question']
                updated['prior_answer'] = old_answer['answer']
            bridge.append(updated)
        elif row['id'] in bridge_ids or row['kind'] == 'lookup':
            bridge.append(row)
    representatives = {
        name: next(row for row in labor if row['calculator'] == name and row['status'] == 'calculated')
        for name in ('annual_leave_days', 'weekly_holiday_pay', 'monthly_minimum_wage', 'premium_pay')
    }
    samples.extend([next(row for row in bridge if row['id'] == 'CR041'), *representatives.values()])
    return groups | {'pr2_regression': bridge, 'labor': labor}, samples


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
    if not health.get('ready') or health.get('max_concurrent') != 1:
        raise SystemExit('공유 모델 서버가 준비되고 동시 생성 상한이 1이어야 합니다.')
    pro = next(spec for spec in cfg.profile.models if spec.key == 'pro')
    advertised = {m['id']: m for m in client.models()}
    missing = service.missing_models(list(advertised.values()), cfg.profile, cfg.reranker, False)
    model_ids = (pro.model, cfg.profile.emb_model) + ((cfg.reranker.model,) if cfg.reranker else ())
    if missing or any(not advertised[model]['loaded'] for model in model_ids):
        raise SystemExit('평가에 필요한 모델이 모두 상주 중이어야 합니다.')
    return {
        'webapp_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'model_server_commit': health.get('commit'),
        'server_max_concurrent': health['max_concurrent'],
        'profile': cfg.profile.name,
        'dataset_version': (manifest.load(cfg.profile.articles) or {}).get('dataset_version'),
        'articles_sha256': manifest.sha256(cfg.profile.articles),
        'system_sha256': sha256(service.system_text(cfg.profile, pro).encode()).hexdigest(),
        'router_sha256': sha256(ROUTER_SYSTEM.encode()).hexdigest(),
        'pr1_catalog_router_sha256': sha256(PR1_ROUTER_SYSTEM.encode()).hexdigest(),
        'temperature': TEMPERATURE,
        'router_max_tokens': ROUTER_MAX_TOKENS,
        'router_timeout_s': cfg.read_timeout,
        'answer_max_tokens': 1024,
        'embedding_max_seq': cfg.profile.max_seq,
        'models': {model: {key: advertised[model].get(key) for key in ('type', 'revision')} for model in model_ids},
        'model_residency': {model: advertised[model]['loaded'] for model in model_ids},
        'reranker': {
            'revision': advertised[cfg.reranker.model].get('revision'),
            'candidates': cfg.reranker.candidates,
        }
        if cfg.reranker
        else None,
        'decree': vars(cfg.decree) if cfg.decree else None,
    }


def _latency_summary(values):
    """Report inclusive percentiles in milliseconds, excluding unavailable values."""
    measured = [value for value in values if value is not None]
    if not measured:
        return {'n': 0, 'p50': None, 'p95': None}
    p95 = quantiles(measured, n=100, method='inclusive')[94] if len(measured) > 1 else measured[0]
    return {'n': len(measured), 'p50': round(median(measured), 3), 'p95': round(p95, 3)}


def _answer(app, question, *, routed):
    _time_guard()
    try:
        for stage, hits, raw, timing in service.ask(app, question, 'pro', calculation_candidate=routed):
            if stage == 'done':
                return {
                    'answer': raw,
                    'calculation': timing.get('_calculation'),
                    'checks': timing.get('calculation_metrics'),
                    'articles': [f'{hit["law"]} {hit["article_id"]}' for hit in hits],
                } | {
                    f'{key}_ms': round(value * 1000, 3) if (value := timing.get(key)) is not None else None
                    for key in (*ANSWER_TIMINGS, *STAGE_TIMINGS)
                }
    except service.AskError as exc:
        return {'error_type': exc.kind}
    raise RuntimeError('Answer did not emit a final event.')


def _answer_pair(app, case, *, routed_first):
    order = (True, False) if routed_first else (False, True)
    responses = {routed: _answer(app, case['question'], routed=routed) for routed in order}
    routed, baseline = responses[True], responses[False]
    calculation = routed.get('calculation') or {}
    actual = {row['label']: row['value'] for row in calculation.get('results', ())}
    expected = case.get('expected_results')
    deltas = {}
    for key in ANSWER_TIMINGS:
        current, original = routed.get(f'{key}_ms'), baseline.get(f'{key}_ms')
        deltas[f'{key}_delta_ms'] = round(current - original, 3) if None not in (current, original) else None
    return (
        {
            'id': case['id'],
            'question': case['question'],
            'baseline': baseline,
            'routed_first': routed_first,
            'expected_results': expected,
            'table_matches': actual == expected if expected is not None else not actual,
        }
        | routed
        | deltas
        | ({'prior_answer': case['prior_answer']} if 'prior_answer' in case else {})
    )


def _latency_report(routes, answers):
    metrics = {
        'router_ms': _latency_summary(row['router_ms'] for row in routes),
        'gated_router_ms': _latency_summary(row['router_ms'] for row in routes if row['gate']),
    }
    for key in ANSWER_TIMINGS:
        field = f'{key}_ms'
        metrics[f'answer_{field}'] = {
            'routed': _latency_summary(row.get(field) for row in answers),
            'baseline': _latency_summary(row['baseline'].get(field) for row in answers),
            'delta': _latency_summary(row[f'{key}_delta_ms'] for row in answers),
        }
    metrics['calculation_event_ms'] = _latency_summary(
        (row.get('checks') or {}).get('calculation_event_ms')
        for row in answers
        if (row.get('calculation') or {}).get('status') == 'calculated'
    )
    metrics['pr1_first_text_delta_ms'] = _latency_summary(
        row['first_text_delta_ms'] for row in answers if row['id'] in SAMPLES
    )
    metrics['answer_stages'] = {
        f'{stage}_ms': {
            'routed': _latency_summary(row.get(f'{stage}_ms') for row in answers),
            'baseline': _latency_summary(row['baseline'].get(f'{stage}_ms') for row in answers),
        }
        for stage in STAGE_TIMINGS
    }
    return metrics


def _route_case(case, pro, compatibility, group):
    raw = None
    usage = {}
    tax_only = group == 'pr1'

    def classify(question):
        nonlocal raw
        raw = pro.route_calculation(question, tax_only=tax_only, metrics=usage)
        return raw

    started = perf_counter()
    routed = route_calculation(case['question'], classify, compatibility, reference_year=2026, tax_only=tax_only)
    route_ms = (perf_counter() - started) * 1000
    calculator = routed.payload['calculator'] or 'none'
    status = routed.payload['status']
    results = {row['label']: row['value'] for row in routed.payload.get('results', [])}
    expected_results = case.get('expected_results')
    return {
        'group': group,
        'kind': case['kind'],
        'id': case['id'],
        'question': case['question'],
        'raw': raw,
        'gate': is_calculation_question(case['question']),
        'calculator': calculator,
        'status': status,
        'expected_calculator': case['calculator'],
        'expected_status': case['status'],
        'reason': routed.reason,
        'router_ms': routed.elapsed_ms,
        'router_validation_ms': round(max(0, route_ms - (routed.elapsed_ms or 0)), 3),
        'results': results,
        'expected_results': expected_results,
        'results_match': results == expected_results if status == 'calculated' else None,
        'classification_match': (
            (calculator, status) == (case['calculator'], case['status'])
            and (case['kind'] != 'unsupported' or routed.reason == 'unsupported_question')
        ),
        **usage,
    }


def _quality(routes):
    groups = {}
    wrong = []
    for row in routes:
        group = groups.setdefault(row['group'], {})
        key = (
            f'{row["expected_calculator"]}/{row["kind"]}'
            if row['group'] == 'labor' and row['kind'] not in ('general', 'unsupported')
            else row['kind']
        )
        totals = group.setdefault(key, {'matched': 0, 'total': 0})
        totals['matched'] += int(row['classification_match'])
        totals['total'] += 1
        if row['results_match'] is False:
            wrong.append(f'{row["group"]}/{row["id"]}')
    return {'groups': groups, 'wrong_calculations': wrong}


def _live(groups, samples, output, post_limit):
    _time_guard()
    envfile.load()
    env = {k: v for k, v in os.environ.items() if k not in ('LAWBOT_DB', 'LAWBOT_EXACT_CACHE_DB')}
    cfg = settings.load(env | {'LAWBOT_CORPUS': 'csv', 'LAWBOT_REQUEST_LOG': '', 'LAWBOT_EMBED_CACHE': '0'})
    if cfg.profile.name != 'v5' or cfg.llm_external or [m.key for m in cfg.profile.models] != ['pro']:
        raise SystemExit('개발 CSV v5 프로필과 공유 서버의 Pro 모델을 사용하세요.')
    compatibility = calculator_compatibility(cfg.profile.articles)
    if not compatibility or any(compatibility.values()):
        raise SystemExit('평가 코퍼스에서 계산기 근거 조문이 일치해야 합니다.')
    client = ModelServerClient(cfg.model_server, cfg.model_token)
    posts = 0
    original_send = httpx.Client.send

    def send(session, request, *args, **kwargs):
        nonlocal posts
        if request.method == 'POST':
            _time_guard()
            if posts >= post_limit:
                raise SystemExit('추론 요청 상한에 도달했습니다.')
            posts += 1
        return original_send(session, request, *args, **kwargs)

    with patch.object(httpx.Client, 'send', send):
        before = _configuration(cfg, client)
        app = service.load_app(cfg)
        pro, _ = app.models.get('pro')
        routes = [_route_case(case, pro, compatibility, name) for name, cases in groups.items() for case in cases]
        # Alternate order to balance warm-up and time-of-run effects across the pairs.
        answers = [_answer_pair(app, case, routed_first=index % 2 == 1) for index, case in enumerate(samples)]
        after = _configuration(cfg, client)
    report = {
        'before': before,
        'after': after,
        'valid': {k: v for k, v in before.items() if k != 'model_residency'}
        == {k: v for k, v in after.items() if k != 'model_residency'},
        'inference_posts': posts,
        'routes': routes,
        'answers': answers,
        'quality': _quality(routes),
        'latency': _latency_report(routes, answers),
    }
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return 0 if report['valid'] else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--live', action='store_true', help='Call the shared Pro model on the synthetic val and samples.'
    )
    parser.add_argument('--suite', choices=POST_LIMITS, default='pr1')
    parser.add_argument('--output', type=Path, help='New JSON output file for the live run.')
    args = parser.parse_args(argv)
    if args.live and (args.output is None or args.output.exists() or not args.output.parent.is_dir()):
        parser.error('--live requires --output pointing to a new file in an existing directory')
    groups, samples = _evaluation_cases(args.suite)
    cases = [row for group in groups.values() for row in group]
    misses = [row['id'] for row in cases if is_calculation_question(row['question']) != (row['kind'] != 'general')]
    print(json.dumps({'val': len(cases), 'groups': Counter(row['kind'] for row in cases), 'gate_misses': misses}))
    return _live(groups, samples, args.output, POST_LIMITS[args.suite]) if args.live else int(bool(misses))


if __name__ == '__main__':
    raise SystemExit(main())
