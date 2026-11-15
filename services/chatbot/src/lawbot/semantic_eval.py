"""Run the approved search calibration stages against private development DB copies."""

import argparse
import fcntl
import json
import os
from collections import Counter
from contextlib import ExitStack
from datetime import datetime
from functools import partial
from hashlib import sha256
from io import BytesIO
from subprocess import check_output
from time import monotonic, perf_counter
from zoneinfo import ZoneInfo

import numpy as np

from lawbot import deploy, envfile, security
from lawbot.api_client import Embedder, ModelServerClient, ModelServerError, request_timeout
from lawbot.cache_seed import json_lines
from lawbot.corpus_store import active_release
from lawbot.db.connection import check_db_path, connect
from lawbot.exact_cache import PROFILE, canonical
from lawbot.feedback import check_private
from lawbot.paths import DATA, ROOT
from lawbot.reranker import TASK, Reranker
from lawbot.retriever import Retriever
from lawbot.semantic_eval_replay import (
    CachedDocumentEncoder,
    item_key,
    paired_sample,
    plan_proposals,
    proposed_queries,
    result_values,
    verify_replay,
)
from lawbot.semantic_eval_store import StageJournal, call_signature
from lawbot.semantic_eval_watch import EvaluationStopped, LiveMonitor
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


class ItemFailure(RuntimeError):
    """The active item failed; the next item will follow the monitoring pause."""


class BoundedModel:
    """Persist the request attempt and response before advancing to another item."""

    def __init__(self, client, calibration, budget, seconds, *, journal=None, monitor=None, items=0):
        self.client = client
        self.calibration = calibration
        self.budget = budget
        self.deadline = monotonic() + seconds
        self.journal = journal
        self.monitor = monitor
        self.calls = Counter(embedding=0, rerank=0)
        if journal:
            self.calls.update(journal.request_counts())
        self.item, self.position = 'corpus-documents', 0
        self.clock_adjustment = 0.0
        self.item_count = items
        self.run_started = monotonic()
        self.initial_completed = journal.completed() if journal else 0
        self.last_report = monotonic()

    def clock(self):
        return perf_counter() + self.clock_adjustment

    def run_item(self, key, evaluate):
        if self.journal is None:
            return evaluate()
        if (saved := self.journal.get_item(key)) is not None:
            return saved
        self.item, self.position = key, 0
        value = evaluate()
        self.journal.save_item(key, value)
        if self.monitor:
            self.monitor.after_item()
            if monotonic() - self.last_report >= 300:
                complete = self.journal.completed()
                increment = complete - self.initial_completed
                elapsed = monotonic() - self.run_started
                eta = elapsed * (self.item_count - complete) / increment if increment > 0 else None
                self.monitor.record(
                    'progress',
                    completed_items=complete,
                    total_items=self.item_count,
                    requests=dict(self.calls),
                    elapsed_s=elapsed,
                    remaining_estimate_s=eta,
                )
                self.last_report = monotonic()
        return value

    def request(self, kind, signature, send):
        housekeeping = perf_counter()
        position, self.position = self.position, self.position + 1
        if self.journal:
            checksum = call_signature(signature)
            saved = self.journal.begin_call(self.item, position, kind, checksum, reserve=False)
            if saved is not None:
                status, response, duration = saved
                self.clock_adjustment += duration / 1000 - (perf_counter() - housekeeping)
                if status != 'done':
                    if self.monitor and status == 'interrupted':
                        self.monitor.record('unconfirmed_request_skipped', position=position, kind=kind)
                    raise ItemFailure(status)
                return np.load(BytesIO(response), allow_pickle=False) if kind == 'embedding' else response
        if self.monitor:
            self.monitor.before_request()
            timeout = request_timeout(300)
        else:
            timeout = within_window(self.deadline)
        if self.calls[kind] >= self.budget[kind]:
            raise ValueError(f'승인된 {kind} 요청 수를 초과합니다.')
        if self.journal:
            self.journal.begin_call(self.item, position, kind, checksum)
        self.calls[kind] += 1
        self.clock_adjustment -= perf_counter() - housekeeping
        started = perf_counter()
        try:
            result = send(timeout)
        except (ConnectionError, TimeoutError, ModelServerError) as exc:
            failed = not isinstance(exc, ModelServerError) or (exc.status is not None and exc.status >= 500)
            if self.monitor is None:
                raise
            if not failed:
                if isinstance(exc, ModelServerError) and exc.status in (400, 413, 422):
                    if self.journal:
                        self.journal.save_call(
                            self.item, position, None, (perf_counter() - started) * 1000, status='input_rejected'
                        )
                    raise ItemFailure('model_input_rejected') from exc
                raise
            if self.journal:
                self.journal.save_call(self.item, position, None, (perf_counter() - started) * 1000, status='failed')
            self.monitor.request_failure(kind, exc)
            raise ItemFailure('model_request_failure') from exc
        finished = perf_counter()
        duration = (finished - started) * 1000
        if self.journal:
            data = result
            if kind == 'embedding':
                stream = BytesIO()
                np.save(stream, result, allow_pickle=False)
                data = stream.getvalue()
            self.journal.save_call(self.item, position, data, duration)
        if self.monitor and kind == 'rerank':
            self.monitor.rerank_timing(duration)
        self.clock_adjustment -= perf_counter() - finished
        return result

    def embed(self, model, texts, input_type, max_seq, timeout=None):
        return self.request(
            'embedding',
            [model, texts, input_type, max_seq],
            lambda limit: self.client.embed(model, texts, input_type, max_seq, timeout=limit),
        )

    def rerank(self, model, query, documents, instruction, timeout=None, *, strict=False):
        return self.request(
            'rerank',
            [model, query, documents, instruction, strict],
            lambda limit: self.client.rerank(model, query, documents, instruction, timeout=limit, strict=strict),
        )

    def check(self):
        if self.monitor is not None:
            self.monitor.before_request()
            self.monitor.ready()
            return
        models = self.client.models(timeout=within_window(self.deadline))
        if not self.calibration.matches_revisions([model for model in models if model['loaded']]):
            raise ValueError('상주 임베딩 또는 리랭커 모델 revision이 평가 설정과 다릅니다.')


def phase_plan(labor_val, labor_test, tax_test, semantic_questions):
    queries = labor_val * (1 + len(ATOMIC_GRID))
    test_questions = labor_test + tax_test
    return {
        'compare-val': {'embedding': queries + 1, 'rerank': queries},
        'union-val': {'embedding': 200, 'rerank': labor_val + 200},
        'shadow-val': {'embedding': semantic_questions, 'rerank': 2 * semantic_questions},
        'once-test': {'embedding': 2 * test_questions, 'rerank': 3 * test_questions},
    }


def write_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(canonical(value) + '\n')


def write_derived(path, value):
    if path.exists():
        if json.loads(path.read_bytes()) != json.loads(canonical(value)):
            raise ValueError('기존 결과 파일과 체크포인트 결과가 다릅니다.')
    else:
        write_private(path, value)


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


def evaluate_routes(search, examples, routes, query_k, union_limit, covered, supplemental, *, bound=None):
    for uid, gold, question in examples:
        tax = uid.startswith('v5:')
        cohorts = ['tax'] if tax else ['labor']
        if not tax and gold in covered:
            cohorts.append('covered')
        if not tax and gold in supplemental:
            cohorts.append('supplement')
        outputs = {}
        for route in routes:

            def evaluate(route=route, question=question, gold=gold):
                started = search.clock() if bound else perf_counter()
                try:
                    result = search.run(route, question, query_k, union_limit)
                except ItemFailure as exc:
                    return {'error': str(exc), 'rank': None, 'top3': [], 'total_ms': (search.clock() - started) * 1000}
                values = result_values(result, gold)
                if not values['top3']:
                    values['error'] = result.metrics['atomic_reason']
                return values

            key = item_key(route, query_k, union_limit, uid)
            outputs[route] = bound.run_item(key, evaluate) if bound else evaluate()
        row = {'id': uid, 'gold': gold, 'cohorts': cohorts, 'results': outputs, 'question': question}
        if 'base' in outputs and 'atomic' in outputs:
            row['base_vs_atomic'] = paired_outcome(outputs['base']['rank'], outputs['atomic']['rank'])
        yield row


def best_budget(summaries, route):
    return max(
        summaries,
        key=lambda size: (summaries[size]['labor'][route]['r3'], summaries[size]['labor'][route]['mrr'], -size),
    )


def compare_phase(ctx, directory, search, covered, supplemental, *, bound=None):
    examples = evaluation_rows(ctx.db, 'v4', 'val', questions=True)
    base_size = search.reranker.candidates
    run = partial(evaluate_routes, search, examples, covered=covered, supplemental=supplemental, bound=bound)
    base = list(run(('base',), ATOMIC_GRID[0], base_size))
    details = {}
    for size in ATOMIC_GRID:
        atomic = run(('atomic',), size, base_size)
        rows = [
            reference | {'results': reference['results'] | trial['results']}
            for reference, trial in zip(base, atomic, strict=True)
        ]
        details[size] = summarize_comparison(rows)
        write_derived(directory / f'compare-val-{size}.rows.json', rows)
    return {'atomic_query_k': best_budget(details, 'atomic'), 'comparisons': details}


def create_search(ctx, cache, profile, reranker_cfg, calibration, index, embed, bound, *, saved=None):
    release = active_release(ctx.db, PROFILE)
    if release.release_id != ctx.snapshot.release_id:
        raise ValueError('검색 조문 코퍼스가 바뀌었습니다.')
    encoder = (
        CachedDocumentEncoder(saved, profile.emb_model, bound)
        if saved is not None
        else Embedder(bound, profile.emb_model)
    )
    retriever = Retriever(encoder, release.law.data, profile.max_seq)
    reranker = Reranker(bound, reranker_cfg.model, reranker_cfg.candidates)
    parents = approved_parents(cache, ctx.snapshot, ctx.event)
    with connect(cache) as conn:
        supplemental = {
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

    def encode(question):
        if embed.fits(question):
            return bound.embed(calibration.embedding_model, [question], 'query', calibration.query_max_seq)
        return None

    search = ResearchSearch(
        retriever,
        reranker,
        index,
        {key: ctx.snapshot.articles[parent].hit() for key, parent in parents.items()},
        encode,
        pair,
        calibration.index_revision,
        shared_query=embed.fits,
        **({'clock': bound.clock} if bound else {}),
    )
    return search, set(parents.values()), supplemental


def val_replay_inputs(directory, ctx, search, history):
    chosen = history['compare-val']['atomic_query_k']
    base = search.reranker.candidates
    limits = [base + extra for extra in UNION_EXTRA_GRID]
    examples = evaluation_rows(ctx.db, 'v4', 'val', questions=True)
    previous = json.loads((directory / f'compare-val-{chosen}.rows.json').read_bytes())
    return chosen, limits, examples, previous


def union_replay_phase(phase, history, ctx, directory, search, calibration, saved, bound, covered, supplemental):
    compare = phase == 'union-val'
    if compare:
        chosen, limits, examples, previous = val_replay_inputs(directory, ctx, search, history)
    else:
        chosen, limits = history['compare-val']['atomic_query_k'], [history['union-val']['union_limit']]
        examples = evaluation_rows(ctx.db, 'v4', 'test', questions=True) + tax_legal_rows(ctx, 'test', questions=True)
        previous = list(
            evaluate_routes(
                search,
                examples,
                ('base', 'atomic'),
                chosen,
                limits[-1],
                covered,
                supplemental,
                bound=bound,
            )
        )
        saved = bound.journal
    proposals = proposed_queries(
        saved,
        search,
        calibration,
        examples,
        previous,
        chosen,
        limits,
        compare=compare,
        compare_grid=ATOMIC_GRID,
    )
    rows = []
    additional_pairs = unavailable = 0
    for uid, gold, question, baseline, replay, reason in proposals:

        def evaluate(replay=replay, reason=reason, question=question, gold=gold):
            default = {'rank': None, 'top3': [], 'total_ms': 0.0, 'error': reason}
            if replay is None:
                return {str(limit): default for limit in limits}
            rankings = {}
            # The largest budget contains every narrower candidate set.
            for limit in reversed(limits):
                try:
                    result = replay.rank(question, limit, search.reranker, search.contract)
                except ItemFailure as exc:
                    return {str(n): default | {'error': str(exc)} for n in limits}
                rankings[str(limit)] = result_values(result, gold)
            return rankings

        if replay is not None:
            additional_pairs += len(replay.missing(limits[-1]))
        outputs = bound.run_item(f'union-replay:{uid}', evaluate)
        biggest = outputs[str(limits[-1])]
        unavailable += int(biggest.get('error') is not None)
        rows.append((baseline, outputs))

    details, trials = {}, {}
    for limit in limits:
        result_rows = [
            baseline | {'results': baseline['results'] | {'union': outputs[str(limit)]}} for baseline, outputs in rows
        ]
        details[limit] = summarize_comparison(result_rows)
        trials[limit] = result_rows
        filename = f'union-val-{limit}.rows.json' if compare else 'once-test.rows.json'
        write_derived(directory / filename, result_rows)
    limit = best_budget(details, 'union') if compare else limits[0]
    for groups in details.values():
        for row in groups.values():
            # Offline replay time is not a measurement of model retrieval time.
            row['union'].pop('search_ms', None)
            row.pop('union_added_ms', None)
    result = {
        'comparisons': details if compare else details[limit],
        'atomic_query_k': chosen,
        'union_limit': limit,
        'additional_rerank_pairs': additional_pairs,
        'additional_rerank_requests': bound.journal.request_counts(prefix='union-replay:').get('rerank', 0),
        'replay_unavailable': unavailable,
    }
    if compare:
        sample = paired_sample(trials[limit])
        online = [
            next(
                evaluate_routes(
                    search,
                    [(row['id'], row['gold'], row['question'])],
                    routes,
                    chosen,
                    limit,
                    covered,
                    supplemental,
                    bound=bound,
                )
            )
            for row, routes in sample
        ]
        comparisons, verification = verify_replay([row for row, _ in sample], online)
        write_derived(directory / 'union-verification.rows.json', comparisons)
        by_route = {route: bound.journal.request_counts(prefix=route + ':') for route in ('base', 'union')}
        verification['requests_by_route'] = by_route
        verification['requests'] = dict(Counter(by_route['base']) + Counter(by_route['union']))
        result['verification'] = verification
    else:
        result['union_latency_val100'] = history['union-val']['verification']
        chosen_calibration = history['shadow-val']['calibration']
        result['semantic_thresholds'] = {
            key: chosen_calibration[key]
            for key in (
                'parent_threshold',
                'question_threshold',
                'min_answer_margin',
            )
        }
    return result


def tax_legal_rows(ctx, split, *, questions=False):
    return [
        row
        for row in evaluation_rows(ctx.db, 'v5', split, questions=questions)
        if (article := ctx.snapshot.articles.get(row[1])) is not None and article.kind == '법률'
    ]


def score_shadow(question, index, eligible, bound, calibration, tokens, embed, store, event):
    metrics = {'embedding': 0, 'rerank_http': 0, 'rerank_pairs': 0}
    started = bound.clock()
    if not embed.fits(question):
        return None, metrics | {'reason': 'truncated_input', 'total_ms': (bound.clock() - started) * 1000}
    metrics['embedding'] = 1
    vectors = bound.embed(calibration.embedding_model, [question], 'query', calibration.query_max_seq)
    if not valid_vectors(vectors, 1, index.vectors.shape[1]):
        return None, metrics | {'reason': 'invalid_vector', 'total_ms': (bound.clock() - started) * 1000}
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
        'total_ms': (bound.clock() - started) * 1000,
        'candidate_queries': len(matches),
        'unique_atoms': len({c.atom_revision for c in candidates}),
        'unique_parents': len({c.parent.chunk_id for c in candidates}),
    }


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('phase', choices=('coverage', 'plan', 'union-plan', *PHASES))
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
    cli.add_argument('--monitor-operations', action='store_true')
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

    def observe(key):
        started = bound.clock()
        try:
            assessment, metrics = score_shadow(
                inputs[key], index, eligible, bound, score_calibration, tokens, embed, store, ctx.event
            )
        except ItemFailure as exc:
            return {'reason': str(exc), 'total_ms': (bound.clock() - started) * 1000, 'rerank_pairs': 0}
        return metrics | {
            'parent': assessment.linked_parent_score if assessment else None,
            'question': assessment.question_match_score if assessment else None,
            'margin': assessment.margin if assessment else None,
            'group': groups[assessment.candidate.binding_id] if assessment and assessment.candidate else None,
        }

    observations = {key: bound.run_item('shadow:' + key, partial(observe, key)) for key in required}
    rows = []
    for label in labels:
        metrics = observations[label['query_id']]
        group = metrics.get('group')
        target = label['target_answer_id']
        # The same-article negative labels a particular wrong answer.
        eligible_row = metrics['reason'] == 'hit' and (
            label['label'] != 'not_reusable' or target is None or group == target
        )
        rows.append(
            ShadowRow(
                label['label'],
                group == target and target is not None,
                metrics.get('parent'),
                metrics.get('question'),
                metrics.get('margin'),
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
    for key, metrics in observations.items():
        parent, match, gap = metrics.get('parent'), metrics.get('question'), metrics.get('margin')
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
                'answer_group': metrics.get('group'),
            }
        )
    write_derived(development_path(args.output) / 'shadow-val.rows.json', decisions)
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
    if (args.approved_embedding, args.approved_rerank) != (embedding_requests, rerank_requests) or (
        not args.monitor_operations and (not args.max_runtime_seconds or args.max_runtime_seconds < 1)
    ):
        raise ValueError('단계별 승인 요청 상한과 실행 시간 상한을 입력하세요.')
    if (directory / f'{args.phase}.json').exists() or (directory / f'{args.phase}.stopped.json').exists():
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
    if profile.max_seq < calibration.query_max_seq:
        raise ValueError('union에는 원자 query 길이 이상을 지원하는 조문 검색 인코더가 필요합니다.')
    lock_path = development_path(str(DATA / 'db' / 'semantic' / '.embedding.lock'))
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (
        lock_path.open('a') as lock,
        StageJournal(directory / f'{args.phase}.sqlite') as journal,
        ExitStack() as stack,
    ):
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        client = ModelServerClient(url, security.model_token())
        monitor = None
        if args.monitor_operations:
            expected = {
                f'embedding/{calibration.embedding_model}': calibration.embedding_revision,
                f'rerank/{calibration.reranker_model}': calibration.reranker_revision,
            }
            monitor = LiveMonitor(client, directory, args.phase, ROOT.with_name('lawbot-prod') / 'logs', expected)
        total_items = sum(budget.values())  # Exact counts for the phase's item journal are supplied below.
        if args.phase == 'compare-val':
            total_items = budget['rerank']
        elif args.phase == 'union-val':
            total_items = 1110 + budget['embedding']
        elif args.phase == 'shadow-val':
            total_items = budget['embedding']
        bound = BoundedModel(
            client,
            calibration,
            budget,
            args.max_runtime_seconds or float('inf'),
            journal=journal,
            monitor=monitor,
            items=total_items,
        )
        begin = monotonic()

        def current_config():
            release = active_release(ctx.db, PROFILE)
            return {
                'web_commit': check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                'server_commit': monitor.commit if monitor else None,
                'model_revisions': monitor.model_rows if monitor else None,
                'corpus_version': release.release_id,
                'corpus_law_sha256': sha256(release.law.data).hexdigest(),
                'corpus_combined_sha256': sha256(release.combined.data).hexdigest(),
                'index_generation': active_revision(index_dir),
                'embedding_model': calibration.embedding_model,
                'reranker': {'model': reranker_cfg.model, 'candidates': reranker_cfg.candidates, 'task': TASK},
                'semantic_top_k': calibration.top_k,
                'semantic_instructions': [calibration.parent_instruction, calibration.question_instruction],
            }

        try:
            embed = ServerEmbedding(bound, model_dir, contract, bound.deadline)
            ctx.check()
            bound.check()
            config = current_config()
            write_derived(
                directory / f'{args.phase}.started', stamp | {'request_budget': budget, 'configuration': config}
            )
            if monitor:
                monitor.record('phase_start', config=config, completed_items=journal.completed())
            if args.phase == 'shadow-val':
                result = semantic_phase(ctx, cache, store, index, bound, calibration, tokens, embed, manifest, args)
            else:
                replay = args.phase in ('union-val', 'once-test')
                saved = (
                    stack.enter_context(StageJournal(directory / 'compare-val.sqlite', read_only=True))
                    if replay
                    else None
                )
                search, covered, supplemental = create_search(
                    ctx,
                    cache,
                    profile,
                    reranker_cfg,
                    calibration,
                    index,
                    embed,
                    bound,
                    saved=saved,
                )
                if saved is None:
                    result = compare_phase(ctx, directory, search, covered, supplemental, bound=bound)
                else:
                    result = union_replay_phase(
                        args.phase,
                        history,
                        ctx,
                        directory,
                        search,
                        calibration,
                        saved,
                        bound,
                        covered,
                        supplemental,
                    )
            bound.check()
            ctx.check()
            final_config = current_config()
            if final_config != config or final_config['index_generation'] != calibration.index_revision:
                if monitor:
                    monitor.stop('configuration_changed', before=config, after=final_config)
                raise ValueError('평가 도중 실행 구성이 바뀌었습니다.')
            result |= {
                'input': stamp,
                'configuration_start': config,
                'configuration_end': final_config,
                'requests': dict(bound.calls),
                'generation_requests': 0,
                'elapsed_s': monotonic() - begin,
                'completed_items': journal.completed(),
            }
            write_private(directory / f'{args.phase}.json', result)
            if monitor:
                monitor.record('phase_complete', **result)
            return result
        except EvaluationStopped as exc:
            report = {
                'invalid': True,
                'cause': str(exc),
                'completed_items': journal.completed(),
                'requests': dict(bound.calls),
                'elapsed_s': monotonic() - begin,
            }
            write_private(directory / f'{args.phase}.stopped.json', report)
            if monitor:
                monitor.record('phase_stopped', **report)
            raise
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
    tax = len(tax_legal_rows(ctx, 'test')) if args.phase in ('plan', 'once-test') else 0
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
    phase = 'union-val' if args.phase == 'union-plan' else args.phase
    history = prior_stages(directory, phase, stamp)
    if phase == 'union-val':
        model_dir = required_path(args.embedding_model_dir, '로컬 임베딩 모델')
        contract = IndexContract(
            calibration.embedding_model,
            calibration.embedding_revision,
            calibration.query_max_seq,
            index.vectors.shape[1],
        )
        embed = ServerEmbedding(None, model_dir, contract, float('inf'))
        with StageJournal(directory / 'compare-val.sqlite', read_only=True) as saved:
            search, _, _ = create_search(ctx, cache, profile, reranker, calibration, index, embed, None, saved=saved)
            chosen, limits, examples, previous = val_replay_inputs(directory, ctx, search, history)
            proposals = proposed_queries(
                saved,
                search,
                calibration,
                examples,
                previous,
                chosen,
                limits,
                compare=True,
                compare_grid=ATOMIC_GRID,
            )
            counts = plan_proposals(proposals, limits)
        sample = min(len(examples), 100)
        budget = {
            'embedding': 2 * sample,
            'rerank': counts['additional_rerank_requests'].get(str(limits[-1]), 0) + 2 * sample,
        }
        plan[phase] = budget
        details = counts | {
            'sample_questions': sample,
            'sample_requests': {'embedding': 2 * sample, 'rerank': 2 * sample},
            'additional_base_requests_for_pairing': {'embedding': sample, 'rerank': sample},
            'request_ceiling': budget,
        }
        if args.phase == 'union-plan':
            return details
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
