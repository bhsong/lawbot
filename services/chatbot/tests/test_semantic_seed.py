"""Atomic handoff ingestion and offline validation with synthetic artifacts."""

import json
from dataclasses import replace

import pytest

from lawbot.cache_seed import P07_SHA256
from lawbot.cache_store import Draft, Store, init
from lawbot.db.connection import connect
from lawbot.exact_cache import Article, Namespace, Snapshot, digest
from lawbot.semantic_handoff import Answer, Handoff, Question, Reference
from lawbot.semantic_import_store import IMPORT_REASON, import_seed
from lawbot.semantic_val import Protection, build_val

PARENT = '근로기준법 제26조'
SECONDARY = '근로기준법 제36조'
CORPUS = '합성 조문은 신청 기한을 설명합니다.'
RAW = '【결론】\n합성 답변입니다.\n【근거 조문】\n제26조, 제36조'


@pytest.fixture
def handoff():
    parent = Article('근로기준법', '제26조', '합성', '법률', CORPUS, digest(CORPUS))
    other = replace(parent, number='제36조', text='별도 합성 근거', text_hash=digest('별도 합성 근거'))
    articles = {PARENT: parent, SECONDARY: other}
    answer = Answer(
        'fixture-answer',
        'unit-1',
        PARENT,
        RAW,
        digest(RAW),
        (Reference(PARENT, parent.text_hash), Reference(SECONDARY, other.text_hash)),
        (Reference(SECONDARY, other.text_hash),),
        {'batch_id': 'P01'},
        '신청 기한',
        7,
        parent.text_hash,
    )
    questions = (
        Question(
            'q1',
            answer.key,
            '신청 기한은 언제인가요?',
            {'split_emb': 'train', 'split_sllm': 'train', 'pair_verdict': 'pass'},
        ),
        Question(
            'q2',
            answer.key,
            '이 경우 어떻게 처리하나요?',
            {'split_emb': 'train', 'split_sllm': 'train', 'pair_verdict': 'pass'},
        ),
        Question(
            'q3',
            answer.key,
            '월급 300만원으로 3년 일하면 퇴직금 얼마인가요?',
            {'split_emb': 'train', 'split_sllm': 'train', 'pair_verdict': 'pass'},
        ),
    )
    return Handoff('a' * 64, (answer,), questions), Snapshot(
        'release', Namespace('corpus', 'policy', 'v5:law'), articles
    )


def selected(conn, sql):
    return conn.execute(sql).fetchall()


def test_import_approves_exact_with_all_display_and_review_evidence(handoff, tmp_path):
    seed, snapshot = handoff
    db = tmp_path / 'private' / 'cache.db'
    init(db)
    result = import_seed(db, seed, snapshot, 1, Protection([]))
    assert result['approved_questions'] == 2
    assert result['excluded_calculation'] == result['disabled_policy'] == 1
    assert Store(db).control().mode == 'off'
    with connect(db) as conn:
        assert selected(conn, 'SELECT status,exact_enabled FROM cache_queries') == [('approved', 1), ('approved', 1)]
        assert selected(conn, 'SELECT raw_answer FROM cache_render_inputs') == [(RAW,)]
        assert selected(conn, 'SELECT article_id FROM cache_citations ORDER BY position') == [(PARENT,), (SECONDARY,)]
        assert selected(conn, 'SELECT article_id FROM semantic_answer_evidence') == [(SECONDARY,)]
        assert selected(conn, 'SELECT semantic_enabled FROM atom_answer_bindings ORDER BY semantic_enabled') == [
            (0,),
            (1,),
        ]
        assert selected(conn, 'SELECT source_start,source_end FROM atoms') == [(7, 12)]
        approvals = conn.execute(
            "SELECT actor,reason,detail FROM cache_audit_events WHERE action='semantic_seed_import'"
        ).fetchall()
        assert len(approvals) == 1
        assert approvals[0][:2] == ('import', IMPORT_REASON)
        assert json.loads(approvals[0][2])['bundle_sha256'] == seed.sha256


@pytest.mark.parametrize('missing', [False, True])
def test_incompatible_evidence_stays_unapproved_and_off(handoff, tmp_path, missing):
    seed, good = handoff
    articles = {PARENT: good.articles[PARENT]}
    if not missing:
        articles[SECONDARY] = replace(good.articles[SECONDARY], text_hash=digest('changed'))
    db = tmp_path / 'private' / 'cache.db'
    init(db)
    report = import_seed(db, seed, replace(good, articles=articles), 1, Protection([]))
    assert report['approved_questions'] == 0 and report['blocked_evidence_questions'] == 2
    with connect(db) as conn:
        assert selected(conn, 'SELECT status FROM cache_answers') == [('draft',)]
        assert selected(conn, 'SELECT carry_status, semantic_enabled FROM atom_answer_bindings') == [
            ('off', 0),
            ('off', 0),
        ]


def test_existing_approval_is_linked_and_replay_does_not_append_history(handoff, tmp_path):
    seed, snapshot = handoff
    db = tmp_path / 'private' / 'cache.db'
    init(db)
    store = Store(db)
    first = seed.questions[0]
    answer = seed.answers[0]
    draft = Draft(
        first.id,
        first.text,
        answer.key,
        answer.sha256,
        '기존 표시',
        [(PARENT, snapshot.articles[PARENT].text_hash)],
        {'bundle_sha256': P07_SHA256},
        RAW,
    )
    ns = snapshot.namespace.id
    store.stage(snapshot.namespace, [draft], 'p07', 'existing')
    store.approve(ns, 1, 'prior', {draft.query_id(ns): draft.answer_id(ns)}, actor='p07', reason='existing')
    first_run = import_seed(db, seed, snapshot, 1, Protection([]))
    with connect(db) as conn:
        history = selected(conn, 'SELECT * FROM cache_audit_events ORDER BY event_id')
    assert first_run['p07_links'] == 1
    assert import_seed(db, seed, snapshot, 1, Protection([])) == first_run
    with connect(db) as conn:
        assert selected(conn, 'SELECT * FROM cache_audit_events ORDER BY event_id') == history
        assert conn.execute(
            'SELECT answer_id,reviewed_by FROM cache_queries WHERE query_id=?', (draft.query_id(ns),)
        ).fetchone() == (draft.answer_id(ns), 'p07')


def test_protected_qs_are_excluded_and_val_is_deterministic(handoff, tmp_path):
    seed, snapshot = handoff
    protected = Protection([seed.questions[1].text])
    db = tmp_path / 'private' / 'cache.db'
    init(db)
    report = import_seed(db, seed, snapshot, 1, protected)
    assert report['seed_protected_exact'] == 1
    with connect(db) as conn:
        assert selected(conn, 'SELECT source_id FROM cache_queries') == [('q1',)]
    deferred = (
        Question('d1', 'deferred-key', '별도 합성 보류 사유는 무엇인가요?', {'pair_verdict': 'review'}),
        Question('d2', 'deferred-key', '다른 합성 수정 사유가 있나요?', {'pair_verdict': 'revise'}),
    )
    rows, held, _ = build_val(seed, deferred, Protection([]), snapshot)
    reversed_seed = replace(seed, questions=tuple(reversed(seed.questions)))
    assert build_val(reversed_seed, tuple(reversed(deferred)), Protection([]), snapshot)[:2] == (rows, held)
    assert len(held) == 1 and {r['label'] for r in rows} == {'reusable', 'not_reusable', 'uncertain'}
    assert RAW not in json.dumps(rows, ensure_ascii=False)


def test_verified_zip_preserves_raw_provenance_and_rejects_deferred(tmp_path, handoff, monkeypatch):
    from hashlib import sha256
    from zipfile import ZipFile

    from lawbot.exact_cache import canonical
    from lawbot.semantic_handoff import PROVENANCE, read_pass

    seed, snapshot = handoff
    answer = seed.answers[0]
    question = seed.questions[0]
    manifest = {
        'package_kind': 'pass',
        'approval_status': 'not_approved',
        'semantic_enabled': False,
        'exact_enabled': False,
        'question_pairs': 1,
        'answer_units_used_by_pairs': 1,
    }
    provenance = dict(zip(PROVENANCE, ['P01', 'synthetic-report', 'b' * 64, 'synthetic-release']))
    answer_row = {
        'answer_key': answer.key,
        'answer': answer.raw,
        'answer_sha256': answer.sha256,
        'review_unit_id': answer.unit_id,
        'primary_article_id': answer.parent,
        'display_citation_article_ids': [r.article_id for r in answer.display],
        'display_citation_hashes': {r.article_id: r.sha256 for r in answer.display},
        'review_evidence_article_ids': [r.article_id for r in answer.evidence],
        **provenance,
    }
    row = (
        answer_row
        | question.metadata
        | {
            'id': question.id,
            'question': question.text,
            'question_sha256': digest(question.text),
            'answer_verdict': 'pass',
        }
    )
    review = {
        'id': question.id,
        'answer_verdict': 'pass',
        'pair_verdict': 'pass',
        'evidence': [{'article_id': answer.parent, 'quote': answer.proposition}],
    }
    articles = 'article_id,deleted,law,article_no,title,text\n' + ''.join(
        f'{key},0,{article.law},{article.number},{article.title},{article.text}\n'
        for key, article in snapshot.articles.items()
    )
    files = {
        'corpus/law_articles_v5_decree.csv': articles,
        'data/pass_seed_candidates.jsonl': canonical(row),
        'data/pass_answers.jsonl': canonical(answer_row),
        'data/cache_key_inputs.csv': (
            f'id,answer_key,normalized_question_sha256\n{question.id},{answer.key},{digest(question.text)}\n'
        ),
        'reviews/pass_review_evidence.jsonl': canonical(review),
    }
    path = tmp_path / 'handoff.zip'
    with ZipFile(path, 'w') as bundle:
        bundle.writestr('manifest.json', canonical(manifest))
        for name, content in files.items():
            bundle.writestr(name, content)
    checksum = sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr('lawbot.semantic_handoff.PASS_SHA256', checksum)
    parsed = read_pass(path)
    assert parsed.sha256 == checksum
    assert parsed.answers == (replace(answer, metadata=provenance),)
    assert parsed.questions == (
        replace(question, metadata=provenance | question.metadata | {'answer_verdict': 'pass'}),
    )
    deferred = tmp_path / 'deferred.zip'
    with ZipFile(deferred, 'w') as bundle:
        bundle.writestr('manifest.json', canonical(manifest | {'package_kind': 'deferred'}))
    with pytest.raises(ValueError, match='SHA-256 mismatch'):
        read_pass(deferred)
    monkeypatch.setattr('lawbot.semantic_handoff.PASS_SHA256', sha256(deferred.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match='invalid handoff manifest'):
        read_pass(deferred)


def test_matching_legacy_query_excluded_by_protection_retains_approval_history(handoff, tmp_path):
    seed, snapshot = handoff
    db = tmp_path / 'private' / 'cache.db'
    init(db)
    store = Store(db)
    question = seed.questions[0]
    answer = seed.answers[0]
    citations = [(reference.article_id, reference.sha256) for reference in answer.display]
    draft = Draft(
        question.id,
        question.text,
        answer.key,
        answer.sha256,
        'synthetic old rendering',
        citations,
        {'bundle_sha256': P07_SHA256},
        answer.raw,
    )
    ns = snapshot.namespace.id
    store.stage(snapshot.namespace, [draft], 'p07', 'original review')
    store.approve(
        ns, 1, 'previous-protection', {draft.query_id(ns): draft.answer_id(ns)}, actor='p07', reason='original review'
    )
    protection = Protection([question.text])
    result = import_seed(db, seed, snapshot, 1, protection)
    with connect(db) as conn:
        assert conn.execute(
            'SELECT exact_enabled,status,reviewed_by FROM cache_queries WHERE query_id=?', (draft.query_id(ns),)
        ).fetchone() == (0, 'approved', 'p07')
        assert conn.execute(
            "SELECT actor,reason FROM cache_audit_events WHERE action='semantic_seed_exclusion'"
        ).fetchone() == (
            'import',
            'protected_exact',
        )
    assert result['existing_exclusions'] == 1
