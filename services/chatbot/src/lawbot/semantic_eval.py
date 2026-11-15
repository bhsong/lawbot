"""Run the approved search calibration stages against private development DB copies."""

import argparse
import fcntl
import json
import os
from collections import Counter
from datetime import datetime
from functools import partial
from time import monotonic, perf_counter
from zoneinfo import ZoneInfo

from lawbot import deploy, envfile, security
from lawbot.api_client import Embedder, ModelServerClient, ModelServerError, request_timeout
from lawbot.cache_seed import json_lines
from lawbot.corpus_store import active_release
from lawbot.db.connection import check_db_path, connect
from lawbot.exact_cache import PROFILE, canonical
from lawbot.feedback import check_private
from lawbot.paths import DATA
from lawbot.reranker import TASK, Reranker
from lawbot.retriever import Retriever
from lawbot.semantic_gate import assess, score_pairs, valid_vectors
from lawbot.semantic_index_store import SUPPLEMENT_EXPRESSION, read_questions
from lawbot.semantic_index_tools import IndexContract, file_hash
from lawbot.semantic_pointer import active_revision
from lawbot.semantic_runtime import AtomicIndex
from lawbot.semantic_search_eval import (
    PairContract,
    ResearchSearch,
    ShadowRow,
    approved_parents,
    coverage,
    evaluation_rows,
    hit_key,
    latency,
    paired_outcome,
    require_coverage,
    select_thresholds,
    summarize_comparison,
)
from lawbot.semantic_settings import RERANK_MAX_LENGTH, Calibration, RerankTokens, ShadowSettings
from lawbot.semantic_store import SemanticStore
from lawbot.semantic_tools import ServerEmbedding, corpus_context, development_path
from lawbot.semantic_val import read_holdout, source_query_id

PHASES = ('compare-val', 'union-val', 'shadow-val', 'once-test')
ATOMIC_GRID = (20, 50)
UNION_EXTRA_GRID = (5, 10)
SCORE_GRID = (0.0, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.92, 0.95, 0.98)
MARGIN_GRID = (0.0, 0.01, 0.025, 0.05, 0.1, 0.15, 0.2)


def within_window(deadline):
    local = datetime.now(ZoneInfo('Asia/Seoul'))
    seconds = local.hour * 3600 + local.minute * 60 + local.second + local.microsecond / 1_000_000
    opening, closing = 380 * 60, 410 * 60
    if opening <= seconds < closing:
        raise ValueError('자동 갱신 시간에는 평가할 수 없습니다.')
    remaining = min(deadline - monotonic(), (opening - seconds) % 86400)
    if remaining <= 0:
        raise ValueError('승인된 평가 시간이 끝났습니다.')
    return request_timeout(min(remaining, 300))


class BoundedModel:
    """Count attempts before sending any inference and enforce the phase's aggregate ceiling."""

    def __init__(self, client, calibration, budget, seconds):
        self.client = client
        self.calibration = calibration
        self.budget = budget
        self.deadline = monotonic() + seconds
        self.calls = Counter(embedding=0, rerank=0)

    def infer_timeout(self, kind):
        timeout = within_window(self.deadline)
        if self.calls[kind] >= self.budget[kind]:
            raise ValueError(f'승인된 {kind} 요청 수를 초과합니다.')
        self.calls[kind] += 1
        return timeout

    def embed(self, model, texts, input_type, max_seq, timeout=None):
        return self.client.embed(model, texts, input_type, max_seq, timeout=self.infer_timeout('embedding'))

    def rerank(self, model, query, documents, instruction, timeout=None, *, strict=False):
        return self.client.rerank(
            model, query, documents, instruction, timeout=self.infer_timeout('rerank'), strict=strict
        )

    def check(self):
        models = self.client.models(timeout=within_window(self.deadline))
        if not self.calibration.matches_revisions([model for model in models if model['loaded']]):
            raise ValueError('상주 임베딩 또는 리랭커 모델 revision이 평가 설정과 다릅니다.')


def phase_plan(labor_val, labor_test, tax_test, semantic_questions):
    queries = labor_val * (1 + len(ATOMIC_GRID))
    unions = labor_val * len(UNION_EXTRA_GRID)
    return {
        'compare-val': {'embedding': queries + 1, 'rerank': queries},
        'union-val': {'embedding': 2 * unions + 1, 'rerank': unions},
        'shadow-val': {'embedding': semantic_questions, 'rerank': 2 * semantic_questions},
        'once-test': {'embedding': 4 * labor_test + 3 * tax_test + 1, 'rerank': 3 * labor_test + 2 * tax_test},
    }


def write_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(canonical(value) + '\n')


def prior_stages(directory, phase, stamp):
    history = {}
    for previous in PHASES[: PHASES.index(phase)]:
        path = directory / f'{previous}.json'
        if not path.is_file():
            raise ValueError(f'먼저 {previous} 단계를 완료하세요.')
        result = json.loads(path.read_bytes())
        if result['input'] != stamp:
            raise ValueError('평가 중 코퍼스·인덱스·설정 또는 val 입력이 바뀌었습니다.')
        history[previous] = result
    return history


def evaluate_routes(search, examples, routes, query_k, union_limit, covered, supplemental):
    for uid, gold, question in examples:
        tax = uid.startswith('v5:')
        cohorts = ['tax'] if tax else ['labor']
        if not tax and gold in covered:
            cohorts.append('covered')
        if not tax and gold in supplemental:
            cohorts.append('supplement')
        outputs = {}
        for route in routes:
            result = search.run(route, question, query_k, union_limit)
            ids = [hit_key(hit) for hit in result.hits]
            if not tax and route != 'base' and result.metrics['atomic_reason'] not in ('ok', 'truncated_input'):
                raise ValueError('노동 문항의 원자 검색 인덱스 계약을 확인하지 못했습니다.')
            outputs[route] = dict(result.metrics) | {
                'rank': ids.index(gold) + 1 if gold in ids else None,
                'top3': ids[:3],
            }
        row = {'id': uid, 'gold': gold, 'cohorts': cohorts, 'results': outputs, 'question': question}
        if 'base' in outputs and 'atomic' in outputs:
            row['base_vs_atomic'] = paired_outcome(outputs['base']['rank'], outputs['atomic']['rank'])
        yield row


def best_budget(summaries, route):
    return max(
        summaries,
        key=lambda size: (summaries[size]['labor'][route]['r3'], summaries[size]['labor'][route]['mrr'], -size),
    )


def retrieval_phase(phase, history, ctx, directory, search, covered, supplemental):
    split = 'test' if phase == 'once-test' else 'val'
    examples = evaluation_rows(ctx.db, 'v4', split, questions=True)
    base_size = search.reranker.candidates
    run = partial(evaluate_routes, search, examples, covered=covered, supplemental=supplemental)
    if phase == 'compare-val':
        base = list(run(('base',), ATOMIC_GRID[0], base_size))
        details = {}
        for size in ATOMIC_GRID:
            atomic = run(('atomic',), size, base_size)
            rows = [
                reference | {'results': reference['results'] | trial['results']}
                for reference, trial in zip(base, atomic, strict=True)
            ]
            details[size] = summarize_comparison(rows)
            write_private(directory / f'compare-val-{size}.rows.json', rows)
        return {'atomic_query_k': best_budget(details, 'atomic'), 'comparisons': details}

    query_k = history['compare-val']['atomic_query_k']
    if phase == 'union-val':
        base_rows = json.loads((directory / f'compare-val-{query_k}.rows.json').read_bytes())
        details = {}
        for extra in UNION_EXTRA_GRID:
            union = run(('union',), query_k, base_size + extra)
            rows = []
            for base, trial in zip(base_rows, union, strict=True):
                if base['id'] != trial['id'] or base['gold'] != trial['gold']:
                    raise ValueError('저장한 val 기준선의 순서 또는 정답이 바뀌었습니다.')
                rows.append(base | {'results': base['results'] | trial['results']})
            details[base_size + extra] = summarize_comparison(rows)
            write_private(directory / f'union-val-{base_size + extra}.rows.json', rows)
        return {'union_limit': best_budget(details, 'union'), 'comparisons': details}

    limit = history['union-val']['union_limit']
    labor = list(run(('base', 'atomic', 'union'), query_k, limit))
    tax = tax_legal_rows(ctx, 'test', questions=True)
    rows = labor + list(evaluate_routes(search, tax, ('base', 'union'), query_k, limit, covered, supplemental))
    write_private(directory / 'once-test.rows.json', rows)
    chosen_calibration = history['shadow-val']['calibration']
    return {
        'comparisons': summarize_comparison(rows),
        'atomic_query_k': query_k,
        'union_limit': limit,
        'semantic_thresholds': {
            key: chosen_calibration[key] for key in ('parent_threshold', 'question_threshold', 'min_answer_margin')
        },
    }


def tax_legal_rows(ctx, split, *, questions=False):
    return [
        row
        for row in evaluation_rows(ctx.db, 'v5', split, questions=questions)
        if (article := ctx.snapshot.articles.get(row[1])) is not None and article.kind == '법률'
    ]


def score_shadow(question, index, eligible, bound, calibration, tokens, embed, store, event):
    metrics = {'embedding': 0, 'rerank_http': 0, 'rerank_pairs': 0}
    started = perf_counter()
    if not embed.fits(question):
        return None, metrics | {'reason': 'truncated_input', 'total_ms': (perf_counter() - started) * 1000}
    metrics['embedding'] = 1
    vectors = embed.encode([question])
    if not valid_vectors(vectors, 1, index.vectors.shape[1]):
        raise ValueError('shadow val 임베딩 벡터의 차원 또는 값이 올바르지 않습니다.')
    matches = index.top_queries(vectors[0], set(eligible), calibration.top_k)
    candidates = [candidate for key in matches for candidate in eligible[key]]

    def before_request(count):
        metrics['rerank_http'] += 1
        metrics['rerank_pairs'] += count

    scores = score_pairs(question, candidates, bound, calibration, tokens, before_request=before_request)
    assessment = assess(question, candidates, scores, calibration, complete=len(eligible) <= calibration.top_k)
    reason = assessment.reason
    if assessment.would_hit and assessment.candidate is not None:
        with connect(store.path) as conn:
            reason = store.verify_selection(conn, store.snapshot.namespace.id, event, assessment.candidate)
    return assessment, metrics | {
        'reason': reason,
        'total_ms': (perf_counter() - started) * 1000,
        'candidate_queries': len(matches),
        'unique_atoms': len({c.atom_revision for c in candidates}),
        'unique_parents': len({c.parent.chunk_id for c in candidates}),
    }


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('phase', choices=('coverage', 'plan', *PHASES))
    cli.add_argument('--corpus-db', required=True)
    cli.add_argument('--cache-db', required=True)
    cli.add_argument('--val-ids', default='data/v5/semantic_val_ids.jsonl')
    cli.add_argument('--output')
    cli.add_argument('--index')
    cli.add_argument('--calibration')
    cli.add_argument('--embedding-model-dir')
    cli.add_argument('--reranker-tokenizer')
    cli.add_argument('--pass-bundle')
    cli.add_argument('--deferred-bundle')
    cli.add_argument('--approved-embedding', type=int)
    cli.add_argument('--approved-rerank', type=int)
    cli.add_argument('--max-runtime-seconds', type=int)
    return cli


def semantic_phase(ctx, cache, store, index, bound, calibration, tokens, embed, manifest, args):
    from lawbot.semantic_handoff import read_deferred, read_pass

    held = read_holdout(manifest)
    _, queries = read_questions(cache, ctx.snapshot, ctx.event)
    excluded = {key for key, (source, _) in queries.items() if source_query_id('pass', source) in held}
    if len(excluded) != len(held):
        raise ValueError('시맨틱 val holdout ID가 승인 인덱스와 일치하지 않습니다.')
    eligible, groups = store.calibration_candidates(index.manifest, ctx.event, excluded)
    if not eligible:
        raise ValueError('시맨틱 val에서 사용할 승인 binding이 없습니다.')
    pass_bundle = read_pass(required_path(args.pass_bundle, 'pass ZIP'))
    deferred = read_deferred(required_path(args.deferred_bundle, 'deferred ZIP'))
    inputs = {
        source_query_id(source, item.id): item.text
        for source, items in (('pass', pass_bundle.questions), ('deferred', deferred))
        for item in items
    }
    labels = json_lines(manifest.read_bytes())
    required = sorted({row['query_id'] for row in labels})
    if set(required) - inputs.keys():
        raise ValueError('시맨틱 val ID를 검증한 질문 원본으로 복원할 수 없습니다.')
    score_calibration = calibration.model_copy(
        update={
            'parent_threshold': 0.0,
            'question_threshold': 0.0,
            'min_answer_margin': 0.0,
        }
    )
    observations = {
        key: score_shadow(inputs[key], index, eligible, bound, score_calibration, tokens, embed, store, ctx.event)
        for key in required
    }
    rows = []
    for label in labels:
        assessment, metrics = observations[label['query_id']]
        group = groups[assessment.candidate.binding_id] if assessment and assessment.candidate else None
        target = label['target_answer_id']
        # A same-article negative labels a particular wrong answer, not every possible answer to that question.
        eligible_row = metrics['reason'] == 'hit' and (
            label['label'] != 'not_reusable' or target is None or group == target
        )
        rows.append(
            ShadowRow(
                label['label'],
                group == target and target is not None,
                assessment.linked_parent_score if assessment else None,
                assessment.question_match_score if assessment else None,
                assessment.margin if assessment else None,
                eligible_row,
            )
        )
    chosen, counts = select_thresholds(rows, SCORE_GRID, MARGIN_GRID)
    parent_threshold, question_threshold, margin = chosen
    points = {
        'parent_threshold': parent_threshold,
        'question_threshold': question_threshold,
        'min_answer_margin': margin,
    }
    decisions = []
    for key, (assessment, metrics) in observations.items():
        parent = assessment.linked_parent_score if assessment else None
        match = assessment.question_match_score if assessment else None
        gap = assessment.margin if assessment else None
        would_hit = (
            metrics['reason'] == 'hit'
            and parent is not None
            and parent > parent_threshold
            and match is not None
            and match > question_threshold
            and (gap is None or gap > margin)
        )
        decisions.append(
            metrics
            | {
                'query_id': key,
                'linked_parent_score': parent,
                'question_match_score': match,
                'answer_margin': gap,
                'would_hit': would_hit,
                'answer_group': groups[assessment.candidate.binding_id]
                if assessment and assessment.candidate
                else None,
            }
        )
    write_private(development_path(args.output) / 'shadow-val.rows.json', decisions)
    return {
        'calibration': calibration.model_dump() | points,
        'counts': counts,
        'labels': dict(Counter(q.label for q in rows)),
        'reason_counts': dict(Counter(row['reason'] for row in decisions)),
        'holdout_queries': len(held),
        'would_hit_queries': sum(row['would_hit'] for row in decisions),
        'actual_generation_savings': 0,
        'rerank_pairs': sum(row['rerank_pairs'] for row in decisions),
        'search_ms': latency([row['total_ms'] for row in decisions]),
        'training_exposure': dict(Counter(row['v6_embedding_training'] for row in labels)),
    }


def required_path(value, name):
    if not value:
        raise ValueError(f'{name} 경로를 지정하세요.')
    return development_path(value)


def live_run(
    args, ctx, cache, manifest, budget, directory, calibration, index_dir, store, index, tokens, stamp, history
):
    embedding_requests, rerank_requests = budget['embedding'], budget['rerank']
    if (
        (args.approved_embedding, args.approved_rerank) != (embedding_requests, rerank_requests)
        or not args.max_runtime_seconds
        or args.max_runtime_seconds < 1
    ):
        raise ValueError('단계별 승인 요청 상한과 실행 시간 상한을 입력하세요.')
    if (directory / f'{args.phase}.json').exists() or (directory / f'{args.phase}.started').exists():
        raise ValueError('이미 시작한 단계입니다. 이전 결과 파일을 확인하세요.')
    url = security.model_server_url()
    if url is None:
        raise ValueError('개발 설정에서 모델 주소를 확인할 수 없습니다.')
    model_dir = required_path(args.embedding_model_dir, '로컬 임베딩 모델')
    contract = IndexContract(
        calibration.embedding_model,
        calibration.embedding_revision,
        calibration.query_max_seq,
        index.vectors.shape[1],
    )
    profile = deploy.get_profile()
    reranker_cfg = deploy.reranker(profile)
    if profile.emb_model != calibration.embedding_model or (
        reranker_cfg is None or reranker_cfg.model != calibration.reranker_model
    ):
        raise ValueError('평가 모델이 개발 검색 설정과 다릅니다.')
    lock_path = development_path(str(DATA / 'db' / 'semantic' / '.embedding.lock'))
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        client = ModelServerClient(url, security.model_token())
        bound = BoundedModel(client, calibration, budget, args.max_runtime_seconds)
        try:
            embed = ServerEmbedding(bound, model_dir, contract, bound.deadline)
            ctx.check()
            bound.check()
            write_private(directory / f'{args.phase}.started', stamp | {'request_budget': budget})
            if args.phase == 'shadow-val':
                result = semantic_phase(ctx, cache, store, index, bound, calibration, tokens, embed, manifest, args)
            else:
                release = active_release(ctx.db, PROFILE)
                if release.release_id != ctx.snapshot.release_id:
                    raise ValueError('검색 조문 코퍼스가 바뀌었습니다.')
                retriever = Retriever(Embedder(bound, profile.emb_model), release.law.data, profile.max_seq)
                reranker = Reranker(bound, reranker_cfg.model, reranker_cfg.candidates)
                parents = approved_parents(cache, ctx.snapshot, ctx.event)
                with connect(cache) as conn:
                    supplement = {
                        article
                        for (article,) in conn.execute(
                            'SELECT DISTINCT a.parent_chunk_id FROM atomic_questions q JOIN atoms a USING(atom_revision_id) '
                            "WHERE a.corpus_snapshot_id=? AND q.expression_type=? AND q.status='approved'",
                            (ctx.snapshot.release_id, SUPPLEMENT_EXPRESSION),
                        )
                    }
                pair = PairContract(
                    reranker.model,
                    calibration.reranker_revision,
                    TASK,
                    f'{RERANK_MAX_LENGTH}/right',
                    'pointwise-f32-softmax-yes',
                    ctx.snapshot.release_id,
                    True,
                )
                search = ResearchSearch(
                    retriever,
                    reranker,
                    index,
                    {key: ctx.snapshot.articles[parent].hit() for key, parent in parents.items()},
                    lambda question: embed.encode([question]) if embed.fits(question) else None,
                    pair,
                    calibration.index_revision,
                )
                result = retrieval_phase(args.phase, history, ctx, directory, search, set(parents.values()), supplement)
            bound.check()
            ctx.check()
            if active_revision(index_dir) != calibration.index_revision:
                raise ValueError('평가 도중 활성 인덱스가 바뀌었습니다.')
            result |= {'input': stamp, 'requests': dict(bound.calls), 'generation_requests': 0}
            write_private(directory / f'{args.phase}.json', result)
            return result
        finally:
            client.http.close()


def run(args):
    corpus = check_db_path(development_path(args.corpus_db))
    cache = check_db_path(development_path(args.cache_db))
    ctx = corpus_context(corpus)
    counts = coverage(corpus, cache, ctx.snapshot, ctx.event)
    if args.phase == 'coverage':
        return counts
    require_coverage(counts)
    manifest = development_path(args.val_ids)
    semantic_questions = len({row['query_id'] for row in json_lines(manifest.read_bytes())})
    tax = len(tax_legal_rows(ctx, 'test'))
    plan = phase_plan(counts['val']['questions'], counts['test']['questions'], tax, semantic_questions)
    if args.phase == 'plan':
        return {
            'coverage': counts,
            'tax_v5_legal_test_questions': tax,
            'semantic_val_unique_questions': semantic_questions,
            'inference_request_ceiling': plan,
        }
    directory = required_path(args.output, '비공개 결과 디렉터리')
    check_private(directory, 'semantic eval')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    config_path = required_path(args.calibration, '평가 calibration')
    calibration = Calibration.model_validate_json(config_path.read_bytes())
    profile = deploy.get_profile()
    reranker = deploy.reranker(profile)
    if reranker is None:
        raise ValueError('법률 검색 리랭커 설정이 필요합니다.')
    if calibration.parent_instruction == calibration.question_instruction:
        raise ValueError('시맨틱 A와 B 리랭크 instruction을 각각 지정하세요.')
    index_path = required_path(args.index, '활성 인덱스 디렉터리')
    if not index_path.is_dir():
        raise ValueError('활성 인덱스 디렉터리를 지정하세요.')
    store = SemanticStore(cache, ctx.snapshot, calibration.index_revision)
    tokens = RerankTokens.load(
        required_path(args.reranker_tokenizer, '리랭커 tokenizer'), calibration.reranker_tokenizer_sha256
    )
    index = AtomicIndex.load(ShadowSettings(calibration, index_path, tokens), store)
    if index is None or set(index.query_ids) != approved_parents(cache, ctx.snapshot, ctx.event).keys():
        raise ValueError('승인된 전체 질문과 평가 인덱스의 행이 일치하지 않습니다.')
    stamp = {
        'snapshot': ctx.snapshot.release_id,
        'namespace': ctx.snapshot.namespace.id,
        'activation_event': ctx.event,
        'index_revision': calibration.index_revision,
        'index_sha256': index.manifest[6],
        'calibration_sha256': file_hash(config_path),
        'semantic_val_sha256': file_hash(manifest),
        'base_query_max_seq': profile.max_seq,
        'base_candidate_count': reranker.candidates,
    }
    history = prior_stages(directory, args.phase, stamp)
    return live_run(
        args,
        ctx,
        cache,
        manifest,
        plan[args.phase],
        directory,
        calibration,
        index_path,
        store,
        index,
        tokens,
        stamp,
        history,
    )


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    try:
        envfile.load()
        result = run(args)
    except ModelServerError, TimeoutError:
        cli.exit(2, '시맨틱 평가 실패: 모델 요청이 실패했습니다. 자동 재시도하지 않습니다.\n')
    except (OSError, ValueError, RuntimeError) as exc:
        cli.exit(2, f'시맨틱 평가 실패: {exc}\n')
    print(canonical(result))
    if args.phase == 'coverage':
        try:
            require_coverage(result)
        except ValueError as exc:
            cli.exit(2, f'시맨틱 평가 실패: {exc}\n')


if __name__ == '__main__':
    main()
