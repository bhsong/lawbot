"""Atomic handoff ingestion and offline validation with synthetic artifacts."""

import json
import zlib
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
        ns, 1, 'protected', {draft.query_id(ns): draft.answer_id(ns)}, actor='p07', reason='existing'
    )
    first_run = import_seed(db, seed, snapshot, 1, Protection(frozenset(), {}, 'protected'))
    with connect(db) as conn:
        history = selected(conn, 'SELECT * FROM cache_audit_events ORDER BY event_id')
    assert first_run['p07_links'] == int(same_revision)
    assert first_run['existing_conflicts'] == int(not same_revision)
    assert first_run['approved_questions'] == (2 if same_revision else 1)
    assert store.control().approval_id == prior_approval and store.control().protection_hash == 'protected'
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
    from lawbot import semantic_handoff

    csv_rows = semantic_handoff.csv_rows

    def key_records(raw):
        assert not raw.startswith(b'article_id,'), 'article CSV was parsed again for schema validation'
        return csv_rows(raw)

    monkeypatch.setattr(semantic_handoff, 'csv_rows', key_records)
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
    store.approve(ns, 1, 'protected', {draft.query_id(ns): draft.answer_id(ns)}, actor='p07', reason='original review')
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


@pytest.mark.parametrize('prior_kind', ['receipt', 'atom'])
def test_seed_cli_rejects_legacy_cache_without_writing(prior_kind, handoff, tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    from lawbot import semantic_tools
    from lawbot.cache_store import audit
    from lawbot.exact_cache import canonical

    seed, snapshot = handoff
    db, corpus = tmp_path / 'cache.db', tmp_path / 'corpus.db'
    init(db)
    corpus.touch()
    ns = snapshot.namespace.id
    answer = seed.answers[0]
    with connect(db, write=True) as conn:
        if prior_kind == 'receipt':
            legacy_id = digest(canonical([seed.sha256, ns, 1]))
            audit(conn, 'semantic_seed_import', legacy_id, 'import', 'old', {'bundle_sha256': seed.sha256})
        else:
            conn.execute('INSERT INTO cache_namespaces VALUES (?,?,?)', (ns, snapshot.namespace.serialize(), 'now'))
            atom = digest(
                canonical([ns, answer.group_id, answer.parent_sha256, answer.start, digest(answer.proposition)])
            )
            conn.execute(
                'INSERT INTO atoms (atom_revision_id,atom_id,namespace_id,corpus_snapshot_id,parent_chunk_id,'
                'parent_sha256,proposition,source_start,source_end,created_at,created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (
                    atom,
                    answer.group_id,
                    ns,
                    snapshot.release_id,
                    answer.parent,
                    answer.parent_sha256,
                    answer.proposition,
                    0,
                    len(answer.proposition),
                    'now',
                    'import',
                ),
            )
    monkeypatch.setattr(semantic_tools.envfile, 'load', lambda: None)
    monkeypatch.setattr(
        semantic_tools, 'corpus_context', lambda _: SimpleNamespace(snapshot=snapshot, event=1, check=lambda: None)
    )
    monkeypatch.setattr(semantic_tools, 'read_pass', lambda _: seed)
    monkeypatch.setattr(semantic_tools, 'seed_protection', lambda *a: Protection(frozenset(), {}, 'current'))
    before = Store(db).control()
    with pytest.raises(SystemExit) as exc:
        semantic_tools.main(
            ['import', '--bundle', str(tmp_path / 'seed.zip'), '--cache-db', str(db), '--corpus-db', str(corpus)]
        )
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert '이전 형식 seed import가 있는 DB입니다. 새 개발 사본을 사용하세요' in error
    assert 'UNIQUE' not in error
    assert Store(db).control() == before
    with connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM atom_answer_bindings').fetchone() == (0,)


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


def test_seed_activation_rejects_changed_exact_protection(handoff, tmp_path):
    from lawbot.semantic_import_store import activate_seed

    seed, snapshot = handoff
    db = tmp_path / 'cache.db'
    init(db)
    protection = Protection(frozenset(), {}, 'current')
    receipt = import_seed(db, seed, snapshot, 1, protection)['approval_id']
    with connect(db, write=True) as conn:
        conn.execute("UPDATE cache_control SET protection_hash='approved-with-extra-csv' WHERE singleton=1")
    with pytest.raises(ValueError, match='protected split changed'):
        activate_seed(db, 'shadow', 'test', 'activate seed', receipt, snapshot, 1, protection, lambda: None)
    assert Store(db).control().mode == 'off'


def test_seed_mode_reports_new_exact_approval_without_reading_zip(handoff, tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from admin_test_data import seed_qa
    from lawbot import cache_admin, semantic_tools
    from lawbot.cache_seed import Bundle, load_protection
    from lawbot.db.migrate import init as init_corpus

    seed, snapshot = handoff
    db = tmp_path / 'cache.db'
    corpus = tmp_path / 'corpus.db'
    init_corpus(corpus)
    seed_qa(corpus)
    extra = tmp_path / 'protected.csv'
    extra.write_text('question\n추가로 보호할 합성 문항\n', encoding='utf-8')
    init(db)
    zip_path = tmp_path / 'p07.zip'
    zip_path.touch()
    monkeypatch.setenv('LAWBOT_EXACT_CACHE_BUNDLE', str(zip_path))
    monkeypatch.setattr(semantic_tools, 'load_bundle', lambda path: Bundle([], frozenset()))
    protection = semantic_tools.seed_protection(corpus, [str(extra)])
    assert protection.fingerprint == load_protection(corpus, None, [extra]).fingerprint
    receipt = import_seed(db, seed, snapshot, 1, protection)['approval_id']
    monkeypatch.setattr(semantic_tools, 'load_bundle', lambda *a: pytest.fail('mode change loaded the P07 ZIP'))

    original_open = Path.open

    def reject_zip(path, *args, **kwargs):
        if path.suffix == '.zip':
            pytest.fail('mode change opened a ZIP')
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', reject_zip)
    monkeypatch.setattr(cache_admin, 'load_bundle', lambda *a: pytest.fail('mode change loaded the P07 ZIP'))
    monkeypatch.setattr(
        semantic_tools, 'corpus_context', lambda *a: SimpleNamespace(snapshot=snapshot, event=1, check=lambda: None)
    )
    args = cache_admin.parser().parse_args(
        [
            'set-mode',
            'shadow',
            '--semantic-seed',
            '--db',
            str(db),
            '--corpus-db',
            str(corpus),
            '--approval',
            receipt,
            '--reason',
            'verified seed',
            '--protected-csv',
            str(extra),
        ]
    )
    result = cache_admin.run(args)
    assert result == {'mode': 'shadow', 'approval_id': Store(db).control().approval_id}
    assert result['approval_id'] not in (None, receipt)


@pytest.mark.parametrize('state', ['unset', 'missing', 'corrupt'])
def test_import_requires_verified_p07_bundle(state, handoff, tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    from lawbot import semantic_tools

    seed, snapshot = handoff
    db = tmp_path / 'cache.db'
    corpus = tmp_path / 'corpus.db'
    corpus.touch()
    init(db)
    bundle = tmp_path / 'p07.zip'
    monkeypatch.delenv('LAWBOT_EXACT_CACHE_BUNDLE', raising=False)
    if state != 'unset':
        monkeypatch.setenv('LAWBOT_EXACT_CACHE_BUNDLE', str(bundle))
    if state == 'corrupt':
        bundle.write_bytes(b'invalid P07')
    monkeypatch.setattr(semantic_tools.envfile, 'load', lambda: None)
    monkeypatch.setattr(
        semantic_tools, 'corpus_context', lambda *a: SimpleNamespace(snapshot=snapshot, event=1, check=lambda: None)
    )
    monkeypatch.setattr(semantic_tools, 'read_pass', lambda *a: seed)
    monkeypatch.setattr(semantic_tools, 'load_protection', lambda *a, **kw: Protection(frozenset(), {}, 'current'))
    with pytest.raises(SystemExit) as exc:
        semantic_tools.main(
            ['import', '--bundle', str(tmp_path / 'pass.zip'), '--cache-db', str(db), '--corpus-db', str(corpus)]
        )
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert '시맨틱 도구 실행 실패' in error and ('LAWBOT_EXACT_CACHE_BUNDLE' in error or 'P07' in error)
    with connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM cache_audit_events').fetchone() == (0,)


@pytest.mark.parametrize('malformed', ['zip', 'manifest', 'row'])
def test_bundle_type_or_zip_defects_exit_cli_with_status_two(malformed, tmp_path, monkeypatch, capsys):
    from hashlib import sha256
    from types import SimpleNamespace
    from zipfile import ZipFile

    from lawbot import semantic_tools

    path = tmp_path / 'bad.zip'
    if malformed == 'zip':
        path.write_bytes(b'not a ZIP')
    else:
        manifest = {
            'package_kind': 'pass',
            'approval_status': 'not_approved',
            'semantic_enabled': False,
            'exact_enabled': False,
            'question_pairs': 1,
            'answer_units_used_by_pairs': 1,
        }
        with ZipFile(path, 'w') as bundle:
            bundle.writestr('manifest.json', json.dumps([] if malformed == 'manifest' else manifest))
            bundle.writestr('corpus/law_articles_v5_decree.csv', 'article_id,deleted,law,article_no,title,text\n')
            bundle.writestr('data/pass_seed_candidates.jsonl', '"not a record"\n')
            bundle.writestr('data/pass_answers.jsonl', '')
            bundle.writestr('data/cache_key_inputs.csv', 'id,answer_key,normalized_question_sha256\n')
            bundle.writestr('reviews/pass_review_evidence.jsonl', '')
    monkeypatch.setattr('lawbot.semantic_handoff.PASS_SHA256', sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(semantic_tools, 'corpus_context', lambda *a: SimpleNamespace())
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
    error = capsys.readouterr().err
    assert '시맨틱 도구 실행 실패' in error and 'Traceback' not in error


def test_verified_zip_does_not_relabel_callers_key_error(tmp_path):
    from hashlib import sha256
    from zipfile import ZipFile

    from lawbot.semantic_handoff import verified_zip

    path = tmp_path / 'caller.zip'
    with ZipFile(path, 'w') as bundle:
        bundle.writestr(
            'manifest.json',
            json.dumps(
                {
                    'package_kind': 'pass',
                    'approval_status': 'not_approved',
                    'semantic_enabled': False,
                    'exact_enabled': False,
                    'question_pairs': 1,
                    'answer_units_used_by_pairs': 1,
                }
            ),
        )
    with pytest.raises(KeyError, match='caller bug'):
        with verified_zip(path, sha256(path.read_bytes()).hexdigest(), 'pass'):
            raise KeyError('caller bug')


def test_first_seed_activation_returns_the_new_exact_approval(handoff, tmp_path):
    from lawbot.semantic_import_store import activate_seed

    seed, snapshot = handoff
    db = tmp_path / 'cache.db'
    init(db)
    protection = Protection(frozenset(), {}, 'current')
    receipt = import_seed(db, seed, snapshot, 1, protection)['approval_id']
    result = activate_seed(db, 'shadow', 'test', 'verified seed', receipt, snapshot, 1, protection, lambda: None)
    assert result == {'mode': 'shadow', 'approval_id': Store(db).control().approval_id}
    assert result['approval_id'] not in (None, receipt)


@pytest.mark.parametrize('replay', [False, True])
def test_import_checks_exact_fingerprint_before_writing(handoff, tmp_path, replay):
    seed, snapshot = handoff
    db = tmp_path / 'cache.db'
    init(db)
    protection = Protection(frozenset(), {}, 'seed-fingerprint')
    if replay:
        import_seed(db, seed, snapshot, 1, protection)
    with connect(db, write=True) as conn:
        conn.execute("UPDATE cache_control SET protection_hash='p07-with-extra-csv' WHERE singleton=1")
        before = selected(conn, 'SELECT * FROM cache_audit_events ORDER BY event_id')
        atoms = selected(conn, 'SELECT atom_revision_id FROM atoms ORDER BY atom_revision_id')
    control = Store(db).control()
    with pytest.raises(ValueError, match='보호 지문'):
        import_seed(db, seed, snapshot, 1, protection)
    assert Store(db).control() == control
    with connect(db) as conn:
        assert selected(conn, 'SELECT * FROM cache_audit_events ORDER BY event_id') == before
        assert selected(conn, 'SELECT atom_revision_id FROM atoms ORDER BY atom_revision_id') == atoms


@pytest.mark.parametrize('error_type', [EOFError, NotImplementedError, zlib.error])
def test_member_decompression_errors_exit_cli_with_status_two(error_type, tmp_path, monkeypatch, capsys):
    from hashlib import sha256
    from types import SimpleNamespace
    from zipfile import ZipFile

    from lawbot import semantic_tools

    path = tmp_path / 'bad-member.zip'
    with ZipFile(path, 'w') as archive:
        archive.writestr('manifest.json', '{}')

    def fail_read(*args):
        raise error_type('synthetic decompression failure')

    monkeypatch.setattr(ZipFile, 'read', fail_read)
    monkeypatch.setattr('lawbot.semantic_handoff.PASS_SHA256', sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(semantic_tools, 'corpus_context', lambda *a: SimpleNamespace())
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
    assert '시맨틱 도구 실행 실패: invalid handoff ZIP entry' in capsys.readouterr().err
