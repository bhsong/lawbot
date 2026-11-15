"""Exact handoff ingestion and validation with synthetic artifacts."""

import json
import zlib
from dataclasses import replace

import pytest

from lawbot.cache_seed import P07_SHA256, Protection
from lawbot.cache_seed_store import IMPORT_REASON, Answer, Handoff, Question, Reference, import_seed
from lawbot.cache_store import Draft, Store, init
from lawbot.db.connection import connect
from lawbot.exact_cache import Article, Namespace, Snapshot, canonical, digest

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
    assert result['approval_id'] == Store(db).control().approval_id
    assert Store(db).control().mode == 'off'
    with connect(db) as conn:
        assert selected(conn, 'SELECT status,exact_enabled FROM cache_queries') == [('approved', 1), ('approved', 1)]
        assert selected(conn, 'SELECT raw_answer FROM cache_render_inputs') == [(RAW,)]
        assert selected(conn, 'SELECT article_id FROM cache_citations ORDER BY position') == [(PARENT,), (SECONDARY,)]
        source = json.loads(conn.execute('SELECT source FROM cache_answers').fetchone()[0])
        assert source['review_evidence_hashes'] == {SECONDARY: snapshot.articles[SECONDARY].text_hash}
        approvals = conn.execute(
            "SELECT actor,reason,detail FROM cache_audit_events WHERE action='pass_seed_import'"
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
        assert selected(conn, 'SELECT status,exact_enabled FROM cache_queries') == [('draft', 0), ('draft', 0)]


def test_import_excludes_normalized_and_near_protected_questions(handoff, tmp_path):
    seed, snapshot = handoff
    first, second, calculation = seed.questions
    second = replace(second, text='근로계약이 끝난 뒤 회사는 미지급 임금을 언제 지급해야 하나요?')
    protected = (
        '  신청   기한은 언제인가요? ',
        '근로계약이 끝난 뒤 회사는 미지급 임금을 언제까지 지급해야 하나요?',
    )
    db = tmp_path / 'cache.db'
    init(db)
    report = import_seed(
        db,
        replace(seed, questions=(first, second, calculation)),
        snapshot,
        1,
        Protection(frozenset(), {}, 'protected', protected),
    )
    assert report['seed_protected_exact'] == report['seed_protected_near'] == report['excluded_calculation'] == 1
    with connect(db) as conn:
        assert selected(conn, 'SELECT source_id FROM cache_queries') == []


def test_import_rolls_back_if_active_corpus_changes(handoff, tmp_path):
    seed, snapshot = handoff
    db = tmp_path / 'cache.db'
    init(db)
    control = Store(db).control()

    def changed():
        raise ValueError('active corpus changed')

    with pytest.raises(ValueError, match='active corpus changed'):
        import_seed(db, seed, snapshot, 1, Protection(frozenset(), {}, 'protected'), check_snapshot=changed)
    assert Store(db).control() == control
    with connect(db) as conn:
        assert selected(conn, 'SELECT answer_id FROM cache_answers') == []
        assert selected(conn, 'SELECT event_id FROM cache_audit_events') == []


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
    assert store.control().approval_id == first_run['approval_id'] and store.control().protection_hash == 'protected'
    with connect(db) as conn:
        receipt, detail = conn.execute(
            "SELECT target,detail FROM cache_audit_events WHERE action='pass_seed_import'"
        ).fetchone()
        assert json.loads(detail)['previous_approval_id'] == prior_approval
    assert first_run['approval_id'] == digest(canonical([prior_approval, receipt]))
    assert import_seed(db, seed, snapshot, 1, Protection(frozenset(), {}, 'protected')) == first_run
    with connect(db) as conn:
        assert selected(conn, 'SELECT * FROM cache_audit_events ORDER BY event_id') == history
        assert conn.execute(
            'SELECT answer_id,reviewed_by FROM cache_queries WHERE query_id=?', (draft.query_id(ns),)
        ).fetchone() == (draft.answer_id(ns), 'p07')


def test_verified_zip_preserves_raw_provenance_and_rejects_deferred(tmp_path, handoff, monkeypatch):
    from hashlib import sha256
    from zipfile import ZipFile

    from lawbot.cache_seed_pass import PROVENANCE, read_pass
    from lawbot.exact_cache import canonical

    seed, snapshot = handoff
    answer = seed.answers[0]
    question = seed.questions[0]
    manifest = {
        'package_kind': 'pass',
        'approval_status': 'not_approved',
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
        'evidence': [{'article_id': answer.parent, 'quote': '신청 기한'}],
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
    monkeypatch.setattr('lawbot.cache_seed_pass.PASS_SHA256', checksum)
    from lawbot import cache_seed_pass

    csv_rows = cache_seed_pass.csv_rows

    def key_records(raw):
        assert not raw.startswith(b'article_id,'), 'article CSV was parsed again for schema validation'
        return csv_rows(raw)

    monkeypatch.setattr(cache_seed_pass, 'csv_rows', key_records)
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
    monkeypatch.setattr('lawbot.cache_seed_pass.PASS_SHA256', sha256(deferred.read_bytes()).hexdigest())
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
            "SELECT actor,reason FROM cache_audit_events WHERE action='pass_seed_exclusion'"
        ).fetchone() == (
            'import',
            'protected_exact',
        )
    assert result['existing_exclusions'] == 1


def test_missing_handoff_field_exits_cli_without_traceback(tmp_path, monkeypatch, capsys):
    from hashlib import sha256
    from types import SimpleNamespace
    from zipfile import ZipFile

    from lawbot import cache_seed_pass

    path = tmp_path / 'incomplete.zip'
    with ZipFile(path, 'w') as bundle:
        bundle.writestr('manifest.json', '{}')
    monkeypatch.setattr('lawbot.cache_seed_pass.PASS_SHA256', sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(cache_seed_pass, 'corpus_context', lambda *a: SimpleNamespace())
    monkeypatch.setattr(cache_seed_pass.envfile, 'load', lambda: None)
    with pytest.raises(SystemExit) as exc:
        cache_seed_pass.main(
            [
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
    assert '통과 ZIP 적재 실패' in output and 'missing a required field or reference' in output
    assert 'Traceback' not in output


@pytest.mark.parametrize('option', ['--bundle', '--cache-db', '--corpus-db'])
def test_pass_cli_rejects_all_production_paths_before_reading_zip(option, tmp_path, monkeypatch, capsys):
    from lawbot import cache_admin, cache_seed_pass

    monkeypatch.setattr(cache_seed_pass.envfile, 'load', lambda: None)
    monkeypatch.setattr(cache_seed_pass, 'read_pass', lambda *_: pytest.fail('production ZIP command opened archive'))
    args = {
        '--bundle': tmp_path / 'pass.zip',
        '--cache-db': tmp_path / 'cache.db',
        '--corpus-db': tmp_path / 'corpus.db',
    }
    args[option] = cache_admin.ROOT.with_name('lawbot-prod') / args[option].name
    with pytest.raises(SystemExit) as exc:
        cache_seed_pass.main([value for key, path in args.items() for value in (key, str(path))])
    assert exc.value.code == 2
    assert '개발용 사본에서만' in capsys.readouterr().err


def test_p07_zip_commands_reject_the_production_target(tmp_path, monkeypatch):
    from lawbot import cache_admin

    monkeypatch.setattr(cache_admin, 'load_bundle', lambda *_: pytest.fail('production command opened archive'))
    db = cache_admin.ROOT.with_name('lawbot-prod') / 'cache.db'
    args = cache_admin.parser().parse_args(['inspect', '--db', str(db), '--bundle', str(tmp_path / 'p07.zip')])
    with pytest.raises(ValueError, match='개발용 사본에서만'):
        cache_admin.run(args)


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
        assert conn.execute(
            "SELECT json_extract(detail, '$.outcome') FROM cache_audit_events WHERE action='pass_seed_question'"
        ).fetchall() == [('existing_conflict',)]


def test_seed_rerun_reports_a_protection_change_since_import(handoff, tmp_path):
    seed, snapshot = handoff
    db = tmp_path / 'cache.db'
    init(db)
    import_seed(db, seed, snapshot, 1, Protection(frozenset(), {}, 'current'))
    with pytest.raises(ValueError, match='protected split changed since import'):
        import_seed(db, seed, snapshot, 1, Protection(frozenset(), {}, 'changed'))


def test_seed_mode_reports_new_exact_approval_without_reading_zip(handoff, tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from admin_test_data import seed_qa
    from lawbot import cache_admin
    from lawbot.cache_seed import load_protection
    from lawbot.db.migrate import init as init_corpus

    seed, snapshot = handoff
    db = tmp_path / 'cache.db'
    corpus = tmp_path / 'corpus.db'
    init_corpus(corpus)
    seed_qa(corpus)
    extra = tmp_path / 'protected.csv'
    extra.write_text('question\n추가로 보호할 합성 문항\n', encoding='utf-8')
    init(db)
    protection = load_protection(corpus, [extra])
    receipt = import_seed(db, seed, snapshot, 1, protection)['approval_id']

    original_open = Path.open

    def reject_zip(path, *args, **kwargs):
        if path.suffix == '.zip':
            pytest.fail('mode change opened a ZIP')
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', reject_zip)
    monkeypatch.setattr(cache_admin, 'load_bundle', lambda *a: pytest.fail('mode change loaded the P07 ZIP'))
    monkeypatch.setattr(cache_admin, 'active_token', lambda *a: (snapshot.release_id, 1))
    monkeypatch.setattr(cache_admin, 'active_release', lambda *a: SimpleNamespace(release_id=snapshot.release_id))
    monkeypatch.setattr(cache_admin, 'cache_snapshot', lambda *a: snapshot)
    monkeypatch.setattr(
        cache_admin.settings,
        'load',
        lambda: SimpleNamespace(db=corpus, corpus='db', profile=SimpleNamespace(name='v5'), decree=None),
    )
    args = cache_admin.parser().parse_args(
        [
            'set-mode',
            'shadow',
            '--db',
            str(db),
            '--approval',
            receipt,
            '--reason',
            'verified seed',
            '--protected-csv',
            str(extra),
        ]
    )
    result = cache_admin.run(args)
    assert result == {'mode': 'shadow'}
    assert Store(db).control().approval_id == receipt
    args.protected_csv = []
    with pytest.raises(ValueError, match='보호 분할이 변경'):
        cache_admin.run(args)


@pytest.mark.parametrize('malformed', ['zip', 'manifest', 'row'])
def test_bundle_type_or_zip_defects_exit_cli_with_status_two(malformed, tmp_path, monkeypatch, capsys):
    from hashlib import sha256
    from types import SimpleNamespace
    from zipfile import ZipFile

    from lawbot import cache_seed_pass

    path = tmp_path / 'bad.zip'
    if malformed == 'zip':
        path.write_bytes(b'not a ZIP')
    else:
        manifest = {
            'package_kind': 'pass',
            'approval_status': 'not_approved',
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
    monkeypatch.setattr('lawbot.cache_seed_pass.PASS_SHA256', sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(cache_seed_pass, 'corpus_context', lambda *a: SimpleNamespace())
    monkeypatch.setattr(cache_seed_pass.envfile, 'load', lambda: None)
    with pytest.raises(SystemExit) as exc:
        cache_seed_pass.main(
            [
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
    assert '통과 ZIP 적재 실패' in error and 'Traceback' not in error


def test_verified_zip_does_not_relabel_callers_key_error(tmp_path):
    from hashlib import sha256
    from zipfile import ZipFile

    from lawbot.cache_seed_pass import verified_zip

    path = tmp_path / 'caller.zip'
    with ZipFile(path, 'w') as bundle:
        bundle.writestr(
            'manifest.json',
            json.dumps(
                {
                    'package_kind': 'pass',
                    'approval_status': 'not_approved',
                    'exact_enabled': False,
                    'question_pairs': 1,
                    'answer_units_used_by_pairs': 1,
                }
            ),
        )
    with pytest.raises(KeyError, match='caller bug'):
        with verified_zip(path, sha256(path.read_bytes()).hexdigest()):
            raise KeyError('caller bug')


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
        queries = selected(conn, 'SELECT query_id FROM cache_queries ORDER BY query_id')
    control = Store(db).control()
    with pytest.raises(ValueError, match='보호 지문'):
        import_seed(db, seed, snapshot, 1, protection)
    assert Store(db).control() == control
    with connect(db) as conn:
        assert selected(conn, 'SELECT * FROM cache_audit_events ORDER BY event_id') == before
        assert selected(conn, 'SELECT query_id FROM cache_queries ORDER BY query_id') == queries


@pytest.mark.parametrize('error_type', [EOFError, NotImplementedError, zlib.error])
def test_member_decompression_errors_exit_cli_with_status_two(error_type, tmp_path, monkeypatch, capsys):
    from hashlib import sha256
    from types import SimpleNamespace
    from zipfile import ZipFile

    from lawbot import cache_seed_pass

    path = tmp_path / 'bad-member.zip'
    with ZipFile(path, 'w') as archive:
        archive.writestr('manifest.json', '{}')

    def fail_read(*args):
        raise error_type('synthetic decompression failure')

    monkeypatch.setattr(ZipFile, 'read', fail_read)
    monkeypatch.setattr('lawbot.cache_seed_pass.PASS_SHA256', sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(cache_seed_pass, 'corpus_context', lambda *a: SimpleNamespace())
    monkeypatch.setattr(cache_seed_pass.envfile, 'load', lambda: None)
    with pytest.raises(SystemExit) as exc:
        cache_seed_pass.main(
            [
                '--bundle',
                str(path),
                '--cache-db',
                str(tmp_path / 'cache.db'),
                '--corpus-db',
                str(tmp_path / 'corpus.db'),
            ]
        )
    assert exc.value.code == 2
    assert '통과 ZIP 적재 실패: invalid handoff ZIP entry' in capsys.readouterr().err
