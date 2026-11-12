"""legalize_git 이벤트 보강 (계획 docs/archives/monitor-lawgo.md 2.1·2.2·2.3).

법령: 신판 법령MST 본문 → 개정문·제개정이유. forms 대상이면 구판 본문과 별표내용 텍스트를 비교해 lawgo_forms 이벤트.
행정규칙: 신판 행정규칙일련번호 상세 → 개정문·제개정이유·부칙·첨부파일 목록. 구판과 별표를 비교해 lawgo_admrul 이벤트.
법제처 호출이 실패하면 예외가 그대로 올라가 수집기가 실패한다 (원문 없는 이벤트를 저장하지 않음, 커서 그대로).
"""

import difflib
import hashlib
from typing import Protocol

from law_monitor import attachments
from law_monitor.config import Config
from law_monitor.events import make_event_id
from law_monitor.lawgo import BASE, LawGoClient, joined, root
from law_monitor.paths import Paths


class _Version(Protocol):
    path: str
    meta: dict


class FileStore:
    """data/files/<sha256>.<ext>: 같은 내용은 한 번만 저장."""

    def __init__(self, paths: Paths):
        self.paths = paths

    def put(self, data: bytes, ext: str) -> tuple[str, str]:
        sha = hashlib.sha256(data).hexdigest()
        path = self.paths.files / f'{sha}.{ext}'
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return sha, self.paths.rel(path)

    def text(self, value: str) -> tuple[str, str]:
        return self.put(value.encode('utf-8'), 'txt')


class LawGoEnricher:
    def __init__(self, client: LawGoClient, config: Config):
        self.client, self.config = client, config

    def enrich(self, kind: str, event: dict, old: _Version | None, new: _Version | None, paths: Paths) -> list:
        """event의 context를 채우고, 별표서식 이벤트 [(event, diff_text)]를 돌려준다."""
        if kind == 'law':
            return self._law(event, old, new, FileStore(paths))
        return self._admrule(event, old, new, FileStore(paths))

    # ---- 법령 ----

    def _law(self, event, old, new, files) -> list:
        mst_old = _meta(old, '법령MST')
        mst_new = _meta(new, '법령MST')
        if not (mst_old or mst_new):
            return []
        body_new = self.client.law_body(mst_new) if mst_new else None
        if body_new is None:
            assert mst_old  # 둘 다 없으면 위에서 끝남
        ref = root(body_new or self.client.law_body(mst_old or ''))
        info = ref.get('기본정보') or {}
        ctx = event['context']
        ctx['amendment_text'] = joined((ref.get('개정문') or {}).get('개정문내용'))
        ctx['amendment_reason'] = joined((ref.get('제개정이유') or {}).get('제개정이유내용'))
        ctx['lawgo'] = {
            'mst_old': mst_old,
            'mst_new': mst_new,
            'attachment_effective': info.get('별표시행일자문자열') if body_new else None,
            'article_effective': info.get('조문시행일자문자열') if body_new else None,
        }
        title = str((new or old).meta.get('제목') or '')
        form = next((f for f in self.config.forms if f.law == title), None)
        if form is None or not (mst_old and mst_new):  # 새 파일·삭제는 첨부 비교 안 함 (목록은 event.attachments)
            return []
        body_old = self.client.law_body(mst_old)
        effective = attachments.parse_effective(info.get('별표시행일자문자열'))
        changes = attachments.compare(body_old, body_new, kinds=form.kinds or None)
        law_id = str(new.meta.get('법령ID') or title)
        return [self._attachment_event('lawgo_forms', event, c, law_id, title, [2], effective, files) for c in changes]

    # ---- 행정규칙 ----

    def _admrule(self, event, old, new, files) -> list:
        serial_old = _meta(old, '행정규칙일련번호')
        serial_new = _meta(new, '행정규칙일련번호')
        if not (serial_old or serial_new):
            return []
        detail_new = self.client.admrule_detail(serial_new) if serial_new else None
        if detail_new is None:
            assert serial_old  # 둘 다 없으면 위에서 끝남
        ref = root(detail_new or self.client.admrule_detail(serial_old or ''))
        ctx = event['context']
        ctx['amendment_text'] = joined((ref.get('개정문') or {}).get('개정문내용'))
        ctx['amendment_reason'] = joined((ref.get('제개정이유') or {}).get('제개정이유내용'))
        ctx['addenda'] = joined((ref.get('부칙') or {}).get('부칙내용'))
        ctx['lawgo'] = {'serial_old': serial_old, 'serial_new': serial_new, 'files': _files(ref.get('첨부파일'))}
        if not (serial_old and serial_new) or serial_old == serial_new:
            return []
        detail_old = self.client.admrule_detail(serial_old)
        changes = attachments.compare(detail_old, detail_new, kinds=None)
        rule_id = str(new.meta.get('행정규칙ID') or serial_new)
        name = str(new.meta.get('행정규칙명') or '')
        cats = event.get('categories') or [event.get('category')]
        return [self._attachment_event('lawgo_admrul', event, c, rule_id, name, cats, {}, files) for c in changes]

    # ---- 별표서식 이벤트 ----

    def _attachment_event(self, source, parent, change, owner_id, law_name, cats, effective, files):
        unit = change.new or change.old
        old_text = attachments.text(change.old) if change.old else None
        new_text = attachments.text(change.new) if change.new else None
        old_side = self._side(old_text, None, files)
        new_side = self._side(new_text, change.new, files)
        item = attachments.item_id(unit)
        side = new_side or old_side
        assert side is not None  # 신·구판 중 하나는 있음 (attachments.compare)
        version = side['version_key'] if new_side else f'removed:{side["version_key"]}'
        dates = effective.get(attachments.label(unit)) or []
        diff = '\n'.join(
            difflib.unified_diff(
                (old_text or '').splitlines(),
                (new_text or '').splitlines(),
                fromfile=f'a/{item}' if old_text is not None else '/dev/null',
                tofile=f'b/{item}' if new_text is not None else '/dev/null',
                lineterm='',
            )
        )
        pctx = parent['context']
        event = {
            'event_id': make_event_id(source, f'{owner_id}|{item}', version),
            'detected_at': parent['detected_at'],
            'category': cats[0] if cats else None,
            'categories': cats,
            'source': source,
            'change_type': f'attachment_{change.kind}',
            'removed': change.new is None,
            'target': {
                'law_name': law_name,
                'item_name': unit.get('별표제목'),
                'item_id': item,
                'kind': unit.get('별표구분'),
            },
            'dates': {
                'promulgation': parent['dates'].get('promulgation'),
                'effective': dates[0] if dates else parent['dates'].get('effective'),
                'effective_dates': dates,
            },
            'old': old_side,
            'new': new_side,
            'diff': {'kind': 'unified_text', 'path': None, 'chunks': []},
            'context': {
                'parent_event_id': parent['event_id'],
                'amendment_text': pctx.get('amendment_text'),
                'amendment_reason': pctx.get('amendment_reason'),
                'source_url': _abs(unit.get('별표서식PDF파일링크') or unit.get('별표서식파일링크')),
                'repo': pctx.get('repo'),
                'commit': pctx.get('commit'),
            },
        }
        if parent.get('replay'):
            event['replay'] = True
        return event, diff

    def _side(self, value: str | None, unit: dict | None, files: FileStore) -> dict | None:
        """텍스트 사본 + (신판만) PDF·HWP 원본."""
        if value is None:
            return None
        sha, rel = files.text(value)
        side = {'version_key': sha, 'text': rel}
        if unit is not None:
            for field, kind, name in (('별표서식PDF파일링크', 'pdf', 'file'), ('별표서식파일링크', 'hwp', 'hwp')):
                link = unit.get(field)
                if isinstance(link, str) and link:
                    side[name] = files.put(self.client.download(link, kind), kind)[1]
        return side


def _meta(version: _Version | None, key: str) -> str | None:
    value = version.meta.get(key) if version else None
    return str(value) if value not in (None, '') else None


def _abs(link) -> str | None:
    if not isinstance(link, str) or not link:
        return None
    return link if link.startswith('http') else f'{BASE}{link}'


def _files(value) -> list[dict]:
    """첨부파일: {'첨부파일링크': [...] 또는 문자열, '첨부파일명': [...]} → [{name, link}]."""
    if not isinstance(value, dict):
        return []
    links, names = value.get('첨부파일링크') or [], value.get('첨부파일명') or []
    links = links if isinstance(links, list) else [links]
    names = names if isinstance(names, list) else [names]
    return [{'name': n, 'link': link} for n, link in zip(names, links)]
