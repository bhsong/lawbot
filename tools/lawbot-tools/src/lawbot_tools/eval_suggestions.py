"""Evaluate reviewed question suggestions on the fixed development validation labels."""

import argparse
import hashlib
from collections import Counter, defaultdict

from lawbot import envfile, settings
from lawbot.api_client import ModelServerClient
from lawbot.cache_admin import corpus_context, development_path, same_file
from lawbot.cache_question_store import question_catalog
from lawbot.cache_seed import json_lines
from lawbot.cache_seed_pass import read_pass, read_records, verified_zip
from lawbot.cache_store import translate_sqlite_errors
from lawbot.calc_gate import is_calculation_question
from lawbot.db.connection import check_db_path
from lawbot.exact_cache import canonical, digest, normalize
from lawbot.paths import DATA
from lawbot.question_vectors import (
    current_revision,
    embedding_budget,
    encode_questions,
    request_count,
    seconds_remaining,
)
from lawbot.suggestions import candidates_with_vectors, rank

DEFERRED_SHA256 = '41dd33d4c1e7eae01bdfc95bb18793eeb1f11a24d0fc7b5b917d799955183862'
LABELS_SHA256 = '777f6e7bf1750f55ebfbbb321340e679d0e4954e464e6c468d04a97270381e28'
MAX_VAL_QUERIES = 1797
# These IDs were published with the fixed validation labels.
GROUP_ID_TAG = 'semantic-answer-group-v1'


def read_deferred(path):
    with verified_zip(path, DEFERRED_SHA256, 'deferred') as (archive, manifest):
        rows = read_records(archive, 'data/deferred_pairs_full.jsonl', ('id', 'question', 'question_sha256'))
    questions = {row['id']: row['question'] for row in rows}
    if len(questions) != len(rows) or len(rows) != manifest['question_pairs']:
        raise ValueError('deferred ZIP 문항 수가 일치하지 않습니다.')
    if any(digest(row['question']) != row['question_sha256'] for row in rows):
        raise ValueError('deferred ZIP 질문 해시가 일치하지 않습니다.')
    return questions


def validation_input(path, handoff, deferred, protection):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != LABELS_SHA256:
        raise ValueError('고정 val 라벨 파일의 SHA-256이 일치하지 않습니다.')
    groups = {
        answer.key: digest(canonical([GROUP_ID_TAG, answer.parent, answer.unit_id])) for answer in handoff.answers
    }
    pass_queries = {q.id: q for q in handoff.questions}
    texts = {
        digest(canonical([source, uid])): text
        for source, items in (
            ('pass', ((q.id, q.text) for q in handoff.questions)),
            ('deferred', deferred.items()),
        )
        for uid, text in items
    }
    labels = defaultdict(dict)
    exclusions = set()
    for row in json_lines(raw):
        uid, target, label = row['query_id'], row['target_answer_id'], row['label']
        text = texts.get(uid)
        if text is None or row['split_v4'] != 'train' or (target is not None and target not in groups.values()):
            raise ValueError('val 질의 또는 답변 라벨을 인계 자료에 연결할 수 없습니다.')
        if uid not in labels and (is_calculation_question(text) or protection.match(text)):
            exclusions.add(uid)
            continue
        if target in labels[uid] and labels[uid][target] != label:
            raise ValueError('같은 val 질의와 답변에 상충하는 라벨이 있습니다.')
        labels[uid][target] = label
    queries = {uid: texts[uid] for uid in sorted(labels)}
    if not 0 < len(queries) <= MAX_VAL_QUERIES:
        raise ValueError('고정 검증셋 질의 수 범위를 벗어났습니다.')
    return queries, labels, {uid: groups[q.answer_key] for uid, q in pass_queries.items()}, len(exclusions)


def curve(scored, taus):
    rows = []
    eligible = []
    for tau in taus:
        exposed = 0
        counts = Counter()
        for picks in scored:
            selected = [label for score, label in picks if score >= tau]
            exposed += bool(selected)
            counts.update(selected)
        known = counts['reusable'] + counts['not_reusable']
        shown = counts.total()
        exposure = exposed / len(scored)
        rows.append(
            {
                'tau': tau,
                'exposure': exposure,
                'exposed_queries': exposed,
                'labeled': known,
                'reusable': counts['reusable'] / known if known else None,
                'not_reusable': counts['not_reusable'] / known if known else None,
                'unlabeled': (shown - known) / shown if shown else None,
            }
        )
        if known and counts['not_reusable'] * 10 <= known:
            eligible.append((exposure, tau))
    return rows, max(eligible)[1] if eligible else None


def write_curve(path, summary, rows, chosen):
    def percent(value):
        return '—' if value is None else f'{value:.2%}'

    with path.open('x', encoding='utf-8') as stream:
        stream.write('# 검토된 비슷한 질문 val\n\n')
        stream.write('집계 `' + canonical(summary) + '`\n\n')
        stream.write(f'선택 τ `{chosen}`\n\n')
        stream.write('| τ | 노출률 | 노출 질의 | 라벨 수 | reusable | not_reusable | 라벨 없음 |\n')
        stream.write('| ---: | ---: | ---: | ---: | ---: | ---: | ---: |\n')
        for row in rows:
            stream.write(
                f'| {row["tau"]:g} | {percent(row["exposure"])} | {row["exposed_queries"]} | {row["labeled"]} '
                f'| {percent(row["reusable"])} | {percent(row["not_reusable"])} | {percent(row["unlabeled"])} |\n'
            )


def run(args):
    corpus = check_db_path(development_path(args.corpus_db))
    cache = check_db_path(development_path(args.cache_db))
    if same_file(corpus, cache):
        raise ValueError('코퍼스와 캐시에는 별도 개발 DB 사본을 지정하세요.')
    if not args.dry_run and not args.out:
        raise ValueError('결과를 기록할 새 --out 경로가 필요합니다.')
    output = development_path(args.out) if args.out else None
    if output is not None and output.exists():
        raise ValueError('결과를 기록할 새 --out 경로가 필요합니다.')
    taus = sorted(set(args.tau or [i / 100 for i in range(-100, 101)]))
    if any(not -1 <= tau <= 1 for tau in taus):
        raise ValueError('τ는 -1~1 사이여야 합니다.')
    ctx = corpus_context(corpus, [])
    handoff = read_pass(development_path(args.pass_bundle))
    queries, labels, answers, excluded = validation_input(
        development_path(args.labels),
        handoff,
        read_deferred(development_path(args.deferred_bundle)),
        ctx.protection,
    )
    requests = request_count(len(queries))
    summary = {'val_queries': len(queries), 'excluded_queries': excluded, 'embedding_requests': requests}
    if args.dry_run:
        return summary
    cfg = settings.load()
    model = cfg.profile.emb_model
    client = ModelServerClient(cfg.model_server, cfg.model_token)
    try:
        with embedding_budget(requests, args.approved_requests, args.max_runtime_seconds) as deadline:
            revision = current_revision(client, model, min(5, seconds_remaining(deadline)))
            control, approved, stored = question_catalog(cache, ctx.snapshot, model, revision, timeout=5)
            if (
                control.protection_hash != ctx.protection.fingerprint
                or control.activation_event != ctx.event
                or not approved
                or len(stored) != len(approved)
                or any(q.source_id not in answers for q in approved)
            ):
                raise ValueError('현행 보호 분할에서 통과 ZIP으로 만든 캐시와 모든 승인 벡터가 필요합니다.')
            candidates = candidates_with_vectors(approved, stored)
            vectors = encode_questions(client, cfg, queries.values(), deadline)
            if current_revision(client, model, min(5, seconds_remaining(deadline))) != revision:
                raise ValueError('val 도중 임베딩 모델 revision이 바뀌었습니다.')
        ctx.check_import()
        scored = []
        for (uid, question), vector in zip(queries.items(), vectors, strict=True):
            if not candidates or candidates[0].vector.shape != vector.shape:
                raise ValueError('val 질의와 캐시 벡터 차원이 일치하지 않습니다.')
            scores = []
            for row, score in rank(candidates, vector, normalize(question), taus[0]):
                label = labels[uid].get(answers[row.source_id], labels[uid].get(None))
                scores.append((score, label if label in ('reusable', 'not_reusable') else 'unlabeled'))
            scored.append(scores)
        rows, chosen = curve(scored, taus)
        summary.update(embedding_model=model, model_revision=revision, candidates=len(approved), chosen_tau=chosen)
        write_curve(output, summary, rows, chosen)
        return summary
    finally:
        client.http.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('corpus-db', 'cache-db', 'pass-bundle', 'deferred-bundle'):
        parser.add_argument(f'--{flag}', required=True)
    parser.add_argument('--labels', default=DATA / 'v5' / 'suggestion_val_ids.jsonl')
    parser.add_argument('--out')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--tau', type=float, action='append')
    parser.add_argument('--approved-requests', type=int)
    parser.add_argument('--max-runtime-seconds', type=int)
    args = parser.parse_args(argv)
    try:
        envfile.load()
        with translate_sqlite_errors():
            result = run(args)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f'추천 val 실패: {exc}\n')
    print(canonical(result))


if __name__ == '__main__':
    main()
