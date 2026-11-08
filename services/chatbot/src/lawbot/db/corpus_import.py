import json
import re
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from lawbot import deploy, manifest
from lawbot.db.connection import transaction
from lawbot.db.csv_import import CsvFile, CsvLayout, read_csv, sha256, source_name

LAW_COLUMNS = (
    'law',
    'article_id',
    'article_no',
    'title',
    'chapter',
    'deleted',
    'text',
    'effective_date',
    'related_ids',
)
COMBINED_COLUMNS: tuple[str, ...] = (*LAW_COLUMNS, 'kind', 'parent_law', 'parent_ids', 'child_ids')
VERSION_COLUMNS = COMBINED_COLUMNS[1:]
LAW_KIND = '법률'
KINDS = (LAW_KIND, '시행령', '시행규칙')


@dataclass(frozen=True)
class CorpusPart:
    csv: CsvFile
    manifest_name: str
    manifest_text: str
    manifest_data: dict


@dataclass(frozen=True)
class CorpusImport:
    release_id: str
    profile: str
    directory: Path
    law: CorpusPart
    combined: CorpusPart

    @property
    def row_count(self) -> int:
        return len(self.combined.csv.rows)

    def files(self) -> dict[str, bytes]:
        return {
            name: raw
            for part in (self.law, self.combined)
            for name, raw in (
                (part.csv.path.name, part.csv.layout.render(part.csv.rows)),
                (part.manifest_name, part.manifest_text.encode()),
            )
        }


def _read_part(path: Path, columns: tuple[str, ...]) -> CorpusPart:
    file = read_csv(path, columns)
    manifest_path = manifest.path_for(path)
    text = manifest_path.read_bytes().decode('utf-8')
    data = json.loads(text)
    if not isinstance(data, dict) or data.get('articles_file') != path.name:
        raise ValueError(f'{manifest_path.name}에 {path.name} 조문 정보가 없습니다.')
    if any(not isinstance(data.get(key), str) or not data[key] for key in ('dataset_version', 'reference_date')):
        raise ValueError(f'{manifest_path.name}의 데이터 버전 또는 기준일이 비어 있습니다.')
    source = data.get('source')
    if source is not None and not isinstance(source, dict):
        raise ValueError(f'{manifest_path.name}의 출처 형식이 잘못됐습니다.')
    live = [row for row in file.rows if row['deleted'] == '0']
    counts = {
        'articles_sha256': file.checksum,
        'articles_total': len(live),
        'articles_by_law': dict(Counter(row['law'] for row in live)),
        'articles_by_kind': dict(Counter(row.get('kind', LAW_KIND) for row in live)),
    }
    for key, value in counts.items():
        if key in data or key in ('articles_sha256', 'articles_total'):
            if data.get(key) != value:
                raise ValueError(f'{path.name}의 {key}가 매니페스트와 다릅니다.')
    return CorpusPart(file, manifest_path.name, text, data)


def prepare_corpus(directory: Path, profile: str, release_id: str) -> CorpusImport:
    """Validate and snapshot both CSVs and manifests before opening a write transaction."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', release_id):
        raise ValueError('릴리스 ID는 영문·숫자로 시작하고 영문·숫자·점·밑줄·하이픈만 쓸 수 있습니다.')
    law_path, combined_path = (directory / p.name for p in deploy.article_paths(profile, {}))
    law, combined = _read_part(law_path, LAW_COLUMNS), _read_part(combined_path, COMBINED_COLUMNS)
    rows = combined.csv.rows
    if not rows or not law.csv.rows:
        raise ValueError('조문 CSV가 비어 있습니다.')
    if len({row['article_id'] for row in rows}) != len(rows) or len(
        {(row['law'], row['article_no']) for row in rows}
    ) != len(rows):
        raise ValueError(f'{combined_path.name}의 조문 키가 중복됩니다.')
    law_types = {}
    for position, row in enumerate(rows, 1):
        kind, parent = row['kind'], row['parent_law']
        if (
            kind not in KINDS
            or row['deleted'] not in ('0', '1')
            or not all(row[key] for key in ('law', 'article_id', 'article_no'))
            or any('\x1f' in value for value in row.values())
        ):
            raise ValueError(f'{combined_path.name} 조문 {position}의 종류·삭제 값 또는 키가 잘못됐습니다.')
        if law_types.setdefault(row['law'], (kind, parent)) != (kind, parent):
            raise ValueError(f'{combined_path.name} 조문 {position}의 법령 분류가 일치하지 않습니다.')
    if law.csv.rows != [{key: row[key] for key in LAW_COLUMNS} for row in rows if row['kind'] == LAW_KIND]:
        raise ValueError('법률 CSV가 조합 CSV의 법률 행과 내용 또는 순서가 다릅니다.')
    return CorpusImport(release_id, profile, directory, law, combined)


def release_bytes(conn: sqlite3.Connection, release_id: str) -> dict[str, bytes]:
    """Reconstruct a release's two CSVs and original manifests from SQLite."""
    release = conn.execute(
        'SELECT law_csv_name, combined_csv_name, law_columns, combined_columns, law_csv_sha256, combined_csv_sha256, '
        'law_manifest_name, combined_manifest_name, law_manifest, combined_manifest, csv_format '
        'FROM corpus_releases WHERE release_id=?',
        (release_id,),
    ).fetchone()
    if release is None:
        raise ValueError(f'조문 릴리스를 찾을 수 없습니다: {release_id}')
    law_name, combined_name, law_cols, combined_cols, law_sha, combined_sha, lm, cm, ltext, ctext, csv_format = release
    if csv_format is None:
        raise ValueError(f'{release_id} 조문 CSV의 복원 형식이 없습니다.')
    fields = ', '.join(f'av.{col}' for col in VERSION_COLUMNS)
    records = [
        dict(zip(COMBINED_COLUMNS, row, strict=True))
        for row in conn.execute(
            f'SELECT l.name, {fields} FROM corpus_release_articles AS cra '
            'JOIN article_versions AS av USING (version_id) JOIN laws AS l USING (law_id) '
            'WHERE cra.release_id=? ORDER BY cra.position',
            (release_id,),
        )
    ]
    formats = json.loads(csv_format)
    law_bytes = CsvLayout(json.loads(law_cols), **formats['law']).render(r for r in records if r['kind'] == LAW_KIND)
    combined_bytes = CsvLayout(json.loads(combined_cols), **formats['combined']).render(records)
    if (sha256(law_bytes), sha256(combined_bytes)) != (law_sha, combined_sha):
        raise ValueError(f'{release_id} 조문 CSV의 복원 해시가 저장된 해시와 다릅니다.')
    manifests = {lm: ltext.encode(), cm: ctext.encode()}
    for manifest_name, csv_name, checksum in ((lm, law_name, law_sha), (cm, combined_name, combined_sha)):
        metadata = json.loads(manifests[manifest_name])
        if not isinstance(metadata, dict) or (metadata.get('articles_file'), metadata.get('articles_sha256')) != (
            csv_name,
            checksum,
        ):
            raise ValueError(f'{release_id}의 저장된 매니페스트가 복원 CSV와 다릅니다: {manifest_name}')
    manifest_sha = formats.get('manifest_sha256')
    if manifest_sha is not None and manifest_sha != {name: sha256(raw) for name, raw in manifests.items()}:
        raise ValueError(f'{release_id} 매니페스트의 복원 해시가 저장된 해시와 다릅니다.')
    return {law_name: law_bytes, combined_name: combined_bytes, **manifests}


def _record_event(conn, profile, kind, release_id, now, actor, *, previous=None, detail=None):
    conn.execute(
        'INSERT INTO corpus_events (release_id,profile,kind,previous_release_id,at,actor,detail) VALUES (?,?,?,?,?,?,?)',
        (release_id, profile, kind, previous, now, actor, json.dumps(detail, ensure_ascii=False) if detail else None),
    )


def _record_release(
    conn: sqlite3.Connection, corpus: CorpusImport, now: str, *, status='validated', origin=None, actor='csv-import'
) -> None:
    law, combined = corpus.law, corpus.combined
    data = combined.manifest_data
    source = data.get('source') or {}
    formats = {name: asdict(part.csv.layout) for name, part in (('law', law), ('combined', combined))}
    for layout in formats.values():
        del layout['columns']
    formats['manifest_sha256'] = {part.manifest_name: sha256(part.manifest_text.encode()) for part in (law, combined)}
    conn.execute(
        'INSERT INTO corpus_releases (release_id, profile, dataset_version, reference_date, collected_date, '
        'source_name, source_url, source_ref, law_csv_name, combined_csv_name, law_columns, combined_columns, '
        'law_csv_sha256, combined_csv_sha256, law_manifest, combined_manifest, law_manifest_name, combined_manifest_name, '
        'origin, status, created_at, validated_at, csv_format) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (
            corpus.release_id,
            corpus.profile,
            data['dataset_version'],
            data['reference_date'],
            data.get('collected_date'),
            source.get('name'),
            source.get('url'),
            source.get('ref'),
            law.csv.path.name,
            combined.csv.path.name,
            json.dumps(law.csv.layout.columns),
            json.dumps(combined.csv.layout.columns),
            law.csv.checksum,
            combined.csv.checksum,
            law.manifest_text,
            combined.manifest_text,
            law.manifest_name,
            combined.manifest_name,
            origin if origin is not None else source_name(corpus.directory),
            status,
            now,
            now if status == 'validated' else None,
            json.dumps(formats),
        ),
    )
    law_ids = {}
    fields = ', '.join(VERSION_COLUMNS)
    placeholders = ','.join('?' for _ in range(len(VERSION_COLUMNS) + 2))
    for position, row in enumerate(combined.csv.rows):
        name, kind, parent = row['law'], row['kind'], row['parent_law'] or None
        if name not in law_ids:
            conn.execute(
                'INSERT INTO laws (name, kind, parent_name, source) VALUES (?,?,?,?) ON CONFLICT(name) DO NOTHING',
                (name, kind, parent, source.get('name') or ''),
            )
            law_id, stored_kind, stored_parent = conn.execute(
                'SELECT law_id, kind, parent_name FROM laws WHERE name=?', (name,)
            ).fetchone()
            if (kind, parent) != (stored_kind, stored_parent):
                raise ValueError(f'기존 법령과 새 릴리스의 종류 또는 상위법이 다릅니다: {name}')
            law_ids[name] = law_id
        law_id = law_ids[name]
        checksum = sha256('\x1f'.join(row[key] for key in COMBINED_COLUMNS).encode())
        conn.execute(
            f'INSERT INTO article_versions (law_id, {fields}, content_sha256) VALUES ({placeholders}) '
            'ON CONFLICT(law_id, article_no, content_sha256) DO NOTHING',
            (law_id, *(row[key] for key in VERSION_COLUMNS), checksum),
        )
        (version,) = conn.execute(
            'SELECT version_id FROM article_versions WHERE law_id=? AND article_no=? AND content_sha256=?',
            (law_id, row['article_no'], checksum),
        ).fetchone()
        conn.execute('INSERT INTO corpus_release_articles VALUES (?,?,?)', (corpus.release_id, position, version))
    _record_event(conn, corpus.profile, 'staged' if status == 'staged' else 'imported', corpus.release_id, now, actor)


def _activate(conn: sqlite3.Connection, profile, release_id, now, *, actor='csv-import', kind='activated') -> int:
    if conn.execute('SELECT profile,status FROM corpus_releases WHERE release_id=?', (release_id,)).fetchone() != (
        profile,
        'validated',
    ):
        raise ValueError(f'활성화할 수 있는 검증 릴리스가 아닙니다: {release_id}')
    current = conn.execute('SELECT release_id FROM active_corpus WHERE profile=?', (profile,)).fetchone()
    if current == (release_id,):
        return 0
    conn.execute(
        'INSERT INTO active_corpus VALUES (?,?,?,?) ON CONFLICT(profile) DO UPDATE '
        'SET release_id=excluded.release_id, activated_at=excluded.activated_at, activated_by=excluded.activated_by',
        (profile, release_id, now, actor),
    )
    _record_event(conn, profile, kind, release_id, now, actor, previous=current[0] if current else None)
    return 1


def write_corpus(
    conn: sqlite3.Connection, corpus: CorpusImport, activate: bool = False, *, require_inactive: bool = False
) -> int:
    """Import one corpus atomically, optionally activating its validated release."""
    now = datetime.now(UTC).isoformat()
    with transaction(conn):
        if (
            require_inactive
            and conn.execute('SELECT 1 FROM active_corpus WHERE profile=?', (corpus.profile,)).fetchone()
        ):
            raise ValueError(f'프로필 {corpus.profile}에 이미 활성 코퍼스 릴리스가 있습니다.')
        existing = conn.execute(
            'SELECT profile FROM corpus_releases WHERE release_id=?', (corpus.release_id,)
        ).fetchone()
        if existing is None:
            _record_release(conn, corpus, now)
            changed = corpus.row_count
        else:
            if existing != (corpus.profile,) or release_bytes(conn, corpus.release_id) != corpus.files():
                raise ValueError(f'같은 릴리스 ID의 원본 내용이 다릅니다: {corpus.release_id}')
            changed = 0
        return changed + (_activate(conn, corpus.profile, corpus.release_id, now) if activate else 0)
