"""Atomic handoff ingestion and offline validation with synthetic artifacts."""

import json
from dataclasses import replace

import pytest

from lawbot.cache_seed import P07_SHA256, Protection
from lawbot.cache_store import Draft, Store, init
from lawbot.db.connection import connect
from lawbot.exact_cache import Article, Namespace, Snapshot, digest
from lawbot.semantic_handoff import Answer, Handoff, Question, Reference
from lawbot.semantic_import_store import IMPORT_REASON, import_seed
from lawbot.semantic_val import build_val

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
    result = import_seed(db, seed, snapshot, 1, Protection(frozenset(), {}, 'protected'))
    assert result['approved_questions'] == 2
    assert result['excluded_calculation'] == 1
    assert result['disabled_policy'] == 0 and result['semantic_enabled'] == 2
    assert Store(db).control().mode == 'off'
    with connect(db) as conn:
        assert selected(conn, 'SELECT status,exact_enabled FROM cache_queries') == [('approved', 1), ('approved', 1)]
        assert selected(conn, 'SELECT raw_answer FROM cache_render_inputs') == [(RAW,)]
        assert selected(conn, 'SELECT article_id FROM cache_citations ORDER BY position') == [(PARENT,), (SECONDARY,)]
        assert selected(conn, 'SELECT article_id FROM semantic_answer_evidence') == [(SECONDARY,)]
        assert selected(conn, 'SELECT semantic_enabled FROM atom_answer_bindings ORDER BY semantic_enabled') == [
            (1,),
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
    report = import_seed(db, seed, replace(good, articles=articles), 1, Protection(frozenset(), {}, 'protected'))
    assert 'approval_id' not in report
    assert report['approved_questions'] == 0 and report['blocked_evidence_questions'] == 2
    with connect(db) as conn:
        assert selected(conn, 'SELECT status FROM cache_answers') == [('draft',)]
        assert selected(conn, 'SELECT carry_status, semantic_enabled FROM atom_answer_bindings') == [
            ('off', 0),
            ('off', 0),
        ]


@pytest.mark.parametrize('same_revision', [False, True])
def test_existing_approval_is_linked_and_replay_does_not_append_history(handoff, tmp_path, same_revision):
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
        [(r.article_id, r.sha256) for r in (answer.display if same_revision else answer.display[:1])],
        {'bundle_sha256': P07_SHA256},
        RAW,
    )
    ns = snapshot.namespace.id
    store.stage(snapshot.namespace, [draft], 'p07', 'existing')
    prior_approval = store.approve(
        ns, 1, 'prior', {draft.query_id(ns): draft.answer_id(ns)}, actor='p07', reason='existing'
    )
    first_run = import_seed(db, seed, snapshot, 1, Protection(frozenset(), {}, 'protected'))
    with connect(db) as conn:
        history = selected(conn, 'SELECT * FROM cache_audit_events ORDER BY event_id')
    assert first_run['p07_links'] == int(same_revision)
    assert first_run['existing_conflicts'] == int(not same_revision)
    assert first_run['approved_questions'] == (2 if same_revision else 1)
    assert store.control().approval_id == prior_approval and store.control().protection_hash == 'prior'
    assert first_run['approval_id'] != prior_approval
    with connect(db) as conn:
        assert conn.execute(
            'SELECT b.semantic_enabled FROM atom_answer_bindings b JOIN atomic_questions q USING(query_revision_id) '
            "WHERE q.query_id='q1'"
        ).fetchone() == (int(same_revision),)
    assert import_seed(db, seed, snapshot, 1, Protection(frozenset(), {}, 'protected')) == first_run
    with connect(db) as conn:
        assert selected(conn, 'SELECT * FROM cache_audit_events ORDER BY event_id') == history
        assert conn.execute(
            'SELECT answer_id,reviewed_by FROM cache_queries WHERE query_id=?', (draft.query_id(ns),)
        ).fetchone() == (draft.answer_id(ns), 'p07')


def test_protected_qs_are_excluded_and_val_is_deterministic(handoff, tmp_path):
    seed, snapshot = handoff
    protected = Protection(frozenset(), {}, 'protected', (seed.questions[1].text,))
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
    rows, held, _ = build_val(seed, deferred, Protection(frozenset(), {}, 'protected'), snapshot)
    reversed_seed = replace(seed, questions=tuple(reversed(seed.questions)))
    assert build_val(reversed_seed, tuple(reversed(deferred)), Protection(frozenset(), {}, 'protected'), snapshot)[
        :2
    ] == (rows, held)
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
    protection = Protection(frozenset(), {}, 'protected', (question.text,))
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


def test_missing_handoff_field_exits_cli_without_traceback(tmp_path, monkeypatch, capsys):
    from hashlib import sha256
    from types import SimpleNamespace
    from zipfile import ZipFile

    from lawbot import semantic_tools

    path = tmp_path / 'incomplete.zip'
    with ZipFile(path, 'w') as bundle:
        bundle.writestr('manifest.json', '{}')
    monkeypatch.setattr('lawbot.semantic_handoff.PASS_SHA256', sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(semantic_tools, 'corpus_context', lambda _: SimpleNamespace())
    monkeypatch.setattr(semantic_tools.envfile, 'load', lambda: None)
    with pytest.raises(SystemExit) as exc:
        semantic_tools.main(
            [
                'import',
                '--bundle',
                str(path),
                '--cache-db',
                str(tmp_path / 'cache.db'),
                '--corpus-db',
                str(tmp_path / 'corpus.db'),
            ]
        )
    assert exc.value.code == 2
    output = capsys.readouterr().err
    assert '시맨틱 도구 실행 실패' in output and 'missing a required field or reference' in output
    assert 'Traceback' not in output


def test_v6_exposure_compares_normalized_training_questions_only():
    from lawbot.semantic_val import TrainingExposure, training_group

    train = training_group(
        [
            {'id': 'train', 'question': '  합성 질문'},
            {'id': 'protected', 'question': '보호용 예시'},
        ],
        [{'id': 'train', 'split_emb': 'train'}, {'id': 'protected', 'split_emb': 'test'}],
    )
    exposure = TrainingExposure(train, frozenset(train.values()))
    assert exposure.classify(Question('other', 'key', '합성 질문', {})) == 'same_text'
    assert exposure.classify(Question('train', 'key', '수정된 문장', {})) == 'same_id_changed'
    assert exposure.classify(Question('protected', 'key', '보호용 예시', {})) == 'none'


def test_legacy_seed_policy_upgrade_preserves_exact_history_and_question_order(handoff, tmp_path):
    from lawbot.cache_store import audit, now, transaction
    from lawbot.exact_cache import canonical
    from lawbot.semantic_import_store import ACTOR
    from lawbot.semantic_index_store import read_questions

    seed, snapshot = handoff
    db = tmp_path / 'cache.db'
    init(db)
    store = Store(db)
    answer = seed.answers[0]
    ns = snapshot.namespace.id
    questions = seed.questions[:2]
    citations = [(r.article_id, r.sha256) for r in answer.display]
    drafts = [
        Draft(q.id, q.text, answer.key, answer.sha256, 'legacy', citations, {'bundle_sha256': P07_SHA256}, answer.raw)
        for q in questions
    ]
    store.stage(snapshot.namespace, drafts, 'p07', 'legacy')
    legacy_approval = store.approve(
        ns,
        1,
        'legacy-protection',
        {d.query_id(ns): d.answer_id(ns) for d in drafts},
        actor='p07',
        reason='legacy approval',
    )
    receipt = digest(canonical([seed.sha256, ns, 1]))
    atom = digest(canonical([ns, answer.group_id, answer.parent_sha256, answer.start, digest(answer.proposition)]))
    with connect(db, write=True) as conn, transaction(conn):
        at = now()
        conn.execute(
            'INSERT INTO atoms VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (
                atom,
                answer.group_id,
                ns,
                snapshot.release_id,
                answer.parent,
                answer.parent_sha256,
                answer.proposition,
                answer.start,
                answer.start + len(answer.proposition),
                'approved',
                at,
                ACTOR,
                at,
                ACTOR,
            ),
        )
        conn.execute("INSERT INTO semantic_reuse_policies VALUES ('old','1','{}',?,?)", (at, ACTOR))
        for q, draft in zip(questions, drafts, strict=True):
            rev = digest(canonical([atom, q.id, digest(q.text)]))
            conn.execute(
                'INSERT INTO atomic_questions VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (rev, q.id, atom, q.text, digest(q.text), 'reviewed_handoff', 'approved', at, ACTOR, at, ACTOR),
            )
            binding = 'old-' + q.id
            conn.execute(
                'INSERT INTO atom_answer_bindings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    binding,
                    ns,
                    atom,
                    rev,
                    draft.answer_id(ns),
                    'old',
                    '1',
                    draft.query_id(ns),
                    1,
                    1,
                    at,
                    ACTOR,
                    at,
                    None,
                    'keep',
                ),
            )
            audit(conn, 'semantic_seed_question', binding, ACTOR, 'old', {'bundle_sha256': seed.sha256})
        audit(
            conn,
            'semantic_seed_import',
            receipt,
            ACTOR,
            'old',
            {
                'bundle_sha256': seed.sha256,
                'counts': {'inserted_questions': 0, 'existing_exclusions': 0},
                'protection_hash': 'legacy-seed',
            },
        )
        conn.execute(
            "UPDATE cache_control SET protection_hash='legacy-seed',approval_id=? WHERE singleton=1", (receipt,)
        )
    ordered = list(read_questions(db, snapshot, 1)[1])
    report = import_seed(db, seed, snapshot, 1, Protection(frozenset(), {}, 'legacy-protection'))
    assert report['approval_id'] == receipt and report['semantic_enabled'] == 2
    assert list(read_questions(db, snapshot, 1)[1]) == ordered
    assert store.control().approval_id == legacy_approval and store.control().protection_hash == 'legacy-protection'
    assert import_seed(db, seed, snapshot, 1, Protection(frozenset(), {}, 'legacy-protection')) == report
    with connect(db) as conn:
        assert conn.execute(
            'SELECT action,count(*) FROM cache_audit_events WHERE action LIKE ? GROUP BY action', ('semantic_seed_%',)
        ).fetchall() == [
            ('semantic_seed_import', 1),
            ('semantic_seed_policy_upgrade', 1),
            ('semantic_seed_question', 2),
        ]
        assert conn.execute("SELECT count(*) FROM atom_answer_bindings WHERE carry_status='keep'").fetchone() == (2,)


def test_p07_dependency_conflict_preserves_only_the_original_exact_approval(handoff, tmp_path):
    seed, snapshot = handoff
    db = tmp_path / 'cache.db'
    init(db)
    store = Store(db)
    answer = seed.answers[0]
    question = seed.questions[0]
    citations = [(ref.article_id, ref.sha256) for ref in answer.display]
    from lawbot.answer_format import render
    from lawbot.exact_cache import FORMAT

    shown = render(answer.raw, [snapshot.articles[key].hit() for key, _ in citations], FORMAT, done=True)
    draft = Draft(
        question.id,
        question.text,
        answer.key,
        answer.sha256,
        shown,
        citations,
        {'bundle_sha256': P07_SHA256, 'review_evidence_article_ids': [PARENT]},
        answer.raw,
    )
    ns = snapshot.namespace.id
    store.stage(snapshot.namespace, [draft], 'p07', 'historical')
    store.approve(ns, 1, 'protected', {draft.query_id(ns): draft.answer_id(ns)}, actor='p07', reason='historical')
    result = import_seed(
        db, replace(seed, questions=(question,)), snapshot, 1, Protection(frozenset(), {}, 'protected')
    )
    assert result['existing_conflicts'] == 1 and result['approved_questions'] == result['approved_answers'] == 0
    assert 'approval_id' not in result
    with connect(db) as conn:
        assert conn.execute("SELECT reviewed_by FROM cache_answers WHERE status='approved'").fetchall() == [('p07',)]
        assert conn.execute('SELECT answer_id FROM cache_queries').fetchall() == [(draft.answer_id(ns),)]
        assert conn.execute('SELECT reason FROM semantic_binding_decisions').fetchall() == [('existing_conflict',)]
