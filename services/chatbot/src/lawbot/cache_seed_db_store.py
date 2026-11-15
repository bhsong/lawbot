"""Read a closed source cache snapshot and verify its approved revisions for transfer."""

import json
import re
import shutil
from hashlib import file_digest
from pathlib import Path
from tempfile import TemporaryDirectory

from lawbot.cache_seed_store import ACTION, ACTOR, Answer, Handoff, Question, Reference, earliest
from lawbot.cache_store import Store, now, read_control, valid, verify_answer, with_review_evidence
from lawbot.db.connection import connect
from lawbot.exact_cache import canonical, digest, normalize


def file_identity(path: Path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def require_closed_source(path: Path):
    for suffix in ('-wal', '-journal'):
        sidecar = path.with_name(path.name + suffix)
        try:
            length = sidecar.stat().st_size
        except FileNotFoundError:
            continue
        if length:
            raise ValueError('원천 캐시는 닫힌 SQLite 백업 사본이어야 합니다.')


def read_source(path: Path) -> tuple[Handoff, str]:
    """Hash exactly the bytes queried, without creating sidecars beside the source."""
    with TemporaryDirectory(prefix='lawbot-cache-import-') as directory:
        copy = Path(directory) / 'source.db'
        require_closed_source(path)
        identity = file_identity(path)
        copy.touch(mode=0o600, exist_ok=False)
        with path.open('rb') as src, copy.open('wb') as dst:
            shutil.copyfileobj(src, dst)
        require_closed_source(path)
        if file_identity(path) != identity:
            raise ValueError('읽는 동안 원천 캐시 DB 파일이 바뀌었습니다.')
        with copy.open('rb') as stream:
            checksum = file_digest(stream, 'sha256').hexdigest()
        Store(copy).check_schema()
        with connect(copy) as conn:
            return read_approved(conn), checksum


def read_approved(conn) -> Handoff:
    control = read_control(conn)
    namespace = control.namespace_id
    row = conn.execute(
        "SELECT detail FROM cache_audit_events WHERE action=? AND actor=? AND json_extract(detail, '$.namespace_id')=? "
        'ORDER BY event_id DESC LIMIT 1',
        (ACTION, ACTOR, namespace),
    ).fetchone()
    checksum = json.loads(row[0]).get('bundle_sha256') if row else None
    if not isinstance(checksum, str) or not re.fullmatch(r'[0-9a-f]{64}', checksum):
        raise ValueError('원천 캐시에 현행 namespace의 통과 ZIP 적재 감사가 없습니다.')

    at = now()
    questions = []
    answers = {}
    rows = conn.execute(
        'SELECT q.query_id,q.source_id,q.original,q.raw_sha256,q.normalized,q.normalized_sha256,q.source,'
        'q.valid_from,q.valid_until,q.expires_at,a.valid_from,a.valid_until,a.expires_at,a.answer_id '
        'FROM cache_queries q JOIN cache_answers a USING (namespace_id,answer_id) '
        "WHERE q.namespace_id=? AND q.status='approved' AND q.exact_enabled=1 AND a.status='approved' "
        'ORDER BY a.answer_id,q.query_id',
        (namespace,),
    )
    for (
        uid,
        source_id,
        text,
        raw_hash,
        normalized,
        key,
        source,
        start,
        end,
        expiry,
        a_start,
        a_end,
        a_expiry,
        aid,
    ) in rows:
        if not (valid(at, start, end, expiry) and valid(at, a_start, a_end, a_expiry)):
            continue
        if (digest(text), normalize(text), digest(normalized), digest(canonical([namespace, key]))) != (
            raw_hash,
            normalized,
            key,
            uid,
        ):
            raise ValueError('원천 캐시 질문 해시가 일치하지 않습니다.')
        if aid not in answers:
            answers[aid] = read_answer(conn, namespace, aid)
        questions.append(
            Question(uid, aid, text, json.loads(source), earliest(end, a_end), earliest(expiry, a_expiry), source_id)
        )
    return Handoff(checksum, tuple(answers.values()), tuple(questions))


def read_answer(conn, namespace, answer_id):
    row = conn.execute(
        'SELECT a.answer_id,a.answer_key,a.raw_sha256,a.answer_text,a.text_sha256,a.citation_count,a.source,i.raw_answer '
        'FROM cache_answers a LEFT JOIN cache_render_inputs i USING (answer_id) WHERE a.answer_id=?',
        (answer_id,),
    ).fetchone()
    citations, _ = verify_answer(conn, namespace, row[:6])
    encoded, raw = row[6:]
    if citations is None or raw is None or digest(raw) != row[2]:
        raise ValueError('원천 캐시 답변 리비전·인용 또는 렌더 원문 해시가 일치하지 않습니다.')
    source = with_review_evidence(conn, answer_id, json.loads(encoded))
    citation_hashes = dict(citations)
    evidence = source.get('review_evidence_hashes', {})
    dependencies = set(source.get('review_evidence_article_ids', ())) | evidence.keys()
    if any(
        not re.fullmatch(r'[0-9a-f]{64}', evidence.get(key) or citation_hashes.get(key) or '')
        or (key in evidence and key in citation_hashes and evidence[key] != citation_hashes[key])
        for key in dependencies
    ):
        raise ValueError('원천 캐시의 검수 근거 해시를 확인할 수 없습니다.')
    refs = tuple(
        Reference(key, evidence[key] if key in evidence else citation_hashes[key]) for key in sorted(dependencies)
    )
    return Answer(
        answer_id,
        source.get('review_unit_id', ''),
        None,
        raw,
        row[2],
        tuple(Reference(*pair) for pair in citations),
        refs,
        source,
        None,
        row[1],
    )
