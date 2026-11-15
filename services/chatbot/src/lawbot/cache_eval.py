"""Compare sequential exact-cache request logs for a fixed test request manifest."""

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from statistics import mean, median

FIELDS = ('exact_lookup_ms', 'request_to_first_answer_ms', 'request_to_complete_ms')
CALLS = ('embedding', 'search', 'rerank', 'llm')


def distribution(values):
    values = sorted(values)
    if not values:
        return {'n': 0, 'mean': None, 'p50': None, 'p95': None}
    return {
        'n': len(values),
        'mean': mean(values),
        'p50': median(values),
        'p95': values[math.ceil(0.95 * len(values)) - 1],
    }


def stats(rows):
    hits = [r for r in rows if r['response_source'] == 'exact_cache' and r['status'] == 'ok']
    eligible = sum(r['cache_eligible'] for r in rows)
    return {
        'requests': len(rows),
        'eligible': eligible,
        'hits': len(hits),
        'would_hit': sum(r['would_hit'] for r in rows),
        'hit_rate': len(hits) / len(rows) if rows else None,
        'eligible_hit_rate': len(hits) / eligible if eligible else None,
        'statuses': dict(Counter(r['status'] for r in rows)),
        'reasons': dict(Counter(r['cache_reason'] for r in rows)),
        'call_counts': {key: sum(r['call_counts'][key] for r in rows) for key in CALLS},
        'hit_call_counts': {key: sum(r['call_counts'][key] for r in hits) for key in CALLS},
        'timing': {
            'all': {key: distribution(r[key] for r in rows if r.get(key) is not None) for key in FIELDS},
            'hit': {key: distribution(r[key] for r in hits if r.get(key) is not None) for key in FIELDS},
        },
    }


def compare(case_ids, off, on):
    if not case_ids or len(case_ids) != len(set(case_ids)) or len(case_ids) != len(off) or len(off) != len(on):
        raise ValueError('고정 요청 순서와 off/on 로그 행 수가 일치해야 합니다.')
    if any(row['cache_mode'] != mode for mode, rows in (('off', off), ('on', on)) for row in rows):
        raise ValueError('각 로그는 지정된 모드의 웹 요청만 포함해야 합니다.')
    if any(
        tuple(a.get(k) for k in ('profile', 'model', 'fmt', 'corpus_release_id'))
        != tuple(b.get(k) for k in ('profile', 'model', 'fmt', 'corpus_release_id'))
        for a, b in zip(off, on, strict=True)
    ):
        raise ValueError('off/on의 요청 형식과 코퍼스가 일치해야 합니다.')
    a, b = stats(off), stats(on)
    baseline_calls = a['call_counts']['llm']
    extra = [
        new['request_to_complete_ms'] - base['request_to_complete_ms']
        for base, new in zip(off, on, strict=True)
        if base['status'] == new['status'] == 'ok' and new['response_source'] == 'rag'
    ]
    return {
        'off': a,
        'on': b,
        'miss_added_overhead_ms': distribution(extra),
        'llm_call_reduction': 1 - b['call_counts']['llm'] / baseline_calls if baseline_calls else None,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('exact',))
    parser.add_argument('--cases', type=Path, required=True, help='같은 순서로 실행한 고유 케이스 ID의 JSON 배열')
    parser.add_argument('--off', type=Path, required=True)
    parser.add_argument('--on', type=Path, required=True)
    args = parser.parse_args(argv)

    def rows(path):
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]

    try:
        case_ids = json.loads(args.cases.read_text(encoding='utf-8'))
        if not isinstance(case_ids, list) or any(not isinstance(key, str) for key in case_ids):
            raise ValueError('케이스 ID는 문자열 배열이어야 합니다.')
        result = compare(case_ids, rows(args.off), rows(args.on))
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f'계측 자료 오류: {exc}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
