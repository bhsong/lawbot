"""Score M5 records using the fixed validation denominators and paired gates."""

import json
from collections import Counter
from math import ceil
from statistics import median

from lawbot.citation import norm
from lawbot.service import CITE_VERDICTS
from lawbot_tools.refusal import is_refusal, oos_verdict
from lawbot_tools.score_answers import score_one


def key(row):
    return row['set'], row['id']


def rate(n, d):
    return n / d if d else None


def duration(values):
    ranked = sorted(value for value in values if value is not None)
    if not ranked:
        return dict(n=0, p50=None, p95=None, max=None)
    return dict(n=len(ranked), p50=median(ranked), p95=ranked[ceil(0.95 * len(ranked)) - 1], max=ranked[-1])


def primary(row):
    return row.get('resumed', row['initial'])


def failed(row):
    return any(row.get(step, {}).get('error') for step in ('initial', 'resumed', 'diagnostic'))


def law_keys(entries):
    return {norm(f'{entry["law"]} {entry["article_id"]}') for entry in entries if entry['law'] is not None}


def score_item(item, row):
    initial = row['initial']
    final = primary(row)
    answered = final['stage'] == 'done' and final['verdict'] in ('shown', 'refused') and not failed(row)
    raw = final['raw'] if answered else ''
    refused = is_refusal(raw)
    gold = {norm(article) for article in item.get('article_ids', [])}
    provided = law_keys(entry for entry in initial['provided'] if entry['kind'] != 'definition')
    with_definitions = law_keys(initial['provided'])
    kind = item['set']
    if kind == 'general':
        search_hit = gold <= provided
        terms = {k: json.dumps(v, ensure_ascii=False) for k, v in item['answer_key'].items() if k != 'id'}
        article = item['article_ids'][0].rsplit(' ', 1)[-1]
        scored = score_one(terms | {'article_id': article}, raw) if answered else {'total': 0, 'cite_ok': 0}
        return {
            'total': scored['total'] if search_hit else 0,
            'cite_ok': scored['cite_ok'],
            'search_hit': search_hit,
            'refused': refused,
        }
    if kind == 'reasoning' and item['expected'] == 'answer':
        cited = law_keys(final['conclusion_citations'] + final['ground_citations'])
        return {
            'all_provided': gold <= provided,
            'all_provided_incl_definitions': gold <= with_definitions,
            'unbased_assertion': answered and not refused and not gold <= provided,
            'unbased_assertion_incl_definitions': answered and not refused and not gold <= with_definitions,
            'cite_ok': answered and not refused and gold <= cited,
            'false_refusal': refused and gold <= with_definitions,
        }
    if kind == 'reasoning':
        return {'conditional': True, 'withheld': answered and refused, 'answer': raw}
    if kind == 'out_of_scope':
        outcome = oos_verdict(raw, item.get('refer_lsa')) if answered else '미응답'
        return {'correct': outcome in ('거절', '안내'), 'outcome': outcome}
    return {
        'asked': initial['verdict'] == 'clarify',
        'question': row['question'],
        'missing_facts': item['missing_facts'],
        'clarify_question': row.get('clarify_question'),
        'reply': row.get('reply'),
        'final_answer': raw,
    }


def measurements(rows):
    verdicts = {name: Counter(dict.fromkeys(CITE_VERDICTS, 0)) for name in ('conclusion', 'ground')}
    statuses = Counter()
    calls, tokens = Counter(), Counter()
    retries = exposure = unnecessary = 0
    completion, first_text, verified, judge = [], [], [], []
    for row in rows:
        initial, final = row['initial'], primary(row)
        clarification = initial['clarification']
        statuses[clarification['status']] += 1
        retries += clarification.get('retries', 0)
        unnecessary += row['unnecessary_clarification']
        for turn in (initial, row.get('resumed')):
            if turn is not None:
                exposure += turn['unmarked_exposure']
                for name in verdicts:
                    verdicts[name].update(c['verdict'] for c in turn[f'{name}_citations'])
        completion.append(row['system_seconds'])
        if final['verdict'] == 'shown' and not failed(row):
            verified.append(row['system_seconds'])
        if (seconds := final['timing']['first_text']) is not None:
            first_text.append(seconds + (initial['timing']['complete'] if final is not initial else 0))
        judge.append(initial['timing']['clarify_seconds'])
        for step in ('initial', 'resumed', 'diagnostic'):
            if (turn := row.get(step)) is not None:
                calls.update(turn['calls'])
                tokens.update({name: count for name, value in turn['tokens'].items() if (count := value or 0)})
    return {
        'request_errors': sum(failed(row) for row in rows),
        'unnecessary_clarifications': unnecessary,
        'unnecessary_clarification_rate': rate(unnecessary, sum(r['set'] != 'fact_missing' for r in rows)),
        'citation_verdicts': verdicts,
        'unmarked_exposure': exposure,
        'timing_seconds': {
            'first_text': duration(first_text),
            'complete': duration(completion),
            'verified_answer': duration(verified),
            'clarification': duration(judge),
        },
        'clarify_statuses': statuses,
        'clarify_failure_rate': rate(statuses['failed_open'], len(rows) - statuses['skipped']),
        'clarify_format_retries': retries,
        'calls': calls,
        'generation_calls': calls['llm'] + calls['router'] + calls['clarify'],
        'tokens': tokens,
        'input_tokens': sum(value for name, value in tokens.items() if name.endswith('_input')),
        'output_tokens': sum(value for name, value in tokens.items() if name.endswith('_output')),
    }


def score(items, rows):
    by_key = {key(row): row for row in rows}
    expected = Counter(item['set'] for item in items)
    per_item = {key(item): score_item(item, by_key[key(item)]) for item in items if key(item) in by_key}
    summary = {'overall': measurements(rows) | {'recorded': len(rows), 'expected': len(items)}}
    for name in expected:
        group = [r for r in rows if r['set'] == name]
        stats = measurements(group) | {'recorded': len(group), 'expected': expected[name]}
        scores = [per_item[key(r)] for r in group]
        n = len(scores)
        if name == 'general':
            search_hits = sum(s['search_hit'] for s in scores)
            total = sum(s['total'] for s in scores)
            stats.update(
                search_hits=search_hits,
                search_hit_rate=rate(search_hits, n),
                total=rate(total, search_hits),
                total_all=rate(total, n),
                cite_ok=rate(sum(s['cite_ok'] for s in scores), n),
                refusal_rate=rate(sum(s['refused'] for s in scores), n),
            )
        elif name == 'reasoning':
            legal = [s for s in scores if not s.get('conditional')]
            stats['legal_items'] = len(legal)
            for field in (
                'all_provided',
                'all_provided_incl_definitions',
                'cite_ok',
                'unbased_assertion',
                'unbased_assertion_incl_definitions',
                'false_refusal',
            ):
                stats[field] = sum(s[field] for s in legal)
                stats[f'{field}_rate'] = rate(stats[field], len(legal))
            stats['all_cited'] = stats['cite_ok']
            stats['conditional_review'] = [{'id': r['id'], **s} for r, s in zip(group, scores) if s.get('conditional')]
        elif name == 'fact_missing':
            stats['ask_rate'] = rate(sum(s['asked'] for s in scores), n)
            stats['review'] = [{'id': r['id'], **s} for r, s in zip(group, scores)]
        elif name == 'out_of_scope':
            stats['correct'] = sum(s['correct'] for s in scores)
            stats['correct_rate'] = rate(stats['correct'], n)
            stats['outcomes'] = Counter(s['outcome'] for s in scores)
        summary[name] = stats
    return summary, per_item


def pair_counts(base, new, group, metric):
    counts = dict(improved=0, worsened=0, same=0, excluded=0)
    for ident, a in base.items():
        if ident[0] != group or metric not in a:
            continue
        b = new[ident]
        if group == 'general' and not (a['search_hit'] or b['search_hit']):
            counts['excluded'] += 1
            continue
        counts['improved' if b[metric] > a[metric] else 'worsened' if b[metric] < a[metric] else 'same'] += 1
    return counts


def compare(items, base_rows, new_rows):
    base, base_items = score(items, base_rows)
    new, new_items = score(items, new_rows)
    pairs = {
        'general_total': pair_counts(base_items, new_items, 'general', 'total'),
        'reasoning_cite_ok': pair_counts(base_items, new_items, 'reasoning', 'cite_ok'),
    }
    grouped = [new[name] for name in ('general', 'reasoning')]
    unnecessary = rate(sum(g['unnecessary_clarifications'] for g in grouped), sum(g['recorded'] for g in grouped))
    a = base['overall']['timing_seconds']['verified_answer']
    b = new['overall']['timing_seconds']['verified_answer']
    p50_increase = b['p50'] - a['p50'] if a['p50'] is not None and b['p50'] is not None else None
    exposure = new['overall']['unmarked_exposure']
    ask_rate = new['fact_missing']['ask_rate']
    failure = new['overall']['clarify_failure_rate']
    gates = {
        'unmarked_exposure': {'value': exposure, 'passed': exposure == 0},
        'fact_ask_rate': {'value': ask_rate, 'passed': ask_rate is not None and ask_rate >= 0.50},
        'general_reasoning_unnecessary_rate': {
            'value': unnecessary,
            'passed': unnecessary is not None and unnecessary <= 0.03,
        },
        'paired_quality': {
            'passed': all(
                c['worsened'] <= c['improved'] and sum(c[k] for k in ('same', 'worsened', 'improved'))
                for c in pairs.values()
            )
        },
        'verified_answer_latency': {
            'p95': b['p95'],
            'p50_increase': p50_increase,
            'passed': b['p95'] is not None and b['p95'] <= 60 and p50_increase is not None and p50_increase <= 5,
        },
        'clarify_failure_rate': {'value': failure, 'passed': failure is not None and failure <= 0.02},
    }
    return {
        'base': base,
        'new': new,
        'pairs': pairs,
        'gates': gates,
        'all_passed': all(gate['passed'] for gate in gates.values()),
    }
