"""legalize-kr·admrule-kr 파일의 머리말(YAML)과 본문을 나누고, 머리말·첨부 목록을 비교한다."""

import datetime as dt

import yaml

ATTACHMENT_KEY = ('별표구분', '별표번호', '별표가지번호')
SKIP_META = ('첨부파일',)  # 첨부는 attachment_changes로 따로 비교 (링크는 개정마다 전부 바뀜)


def split(text: str) -> tuple[dict, str]:
    if not text.startswith('---\n'):
        return {}, text
    end = text.find('\n---\n', 4)
    if end < 0:
        return {}, text
    meta = yaml.safe_load(text[4:end]) or {}
    plain = _plain(meta)
    return (plain if isinstance(plain, dict) else {}), text[end + 5 :].lstrip('\n')


def _plain(value):
    """YAML 날짜를 'YYYY-MM-DD' 문자열로 (JSON에 그대로 쓰기 위해)."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def _attachment(item: dict) -> dict:
    return {k: str(item.get(k, '') or '') for k in (*ATTACHMENT_KEY, '제목')}


def attachment_changes(old: list[dict] | None, new: list[dict] | None) -> dict:
    """링크(파일링크·PDF링크)를 뺀 첨부 목록 비교: 추가·삭제·제목 변경."""
    before = {tuple(a[k] for k in ATTACHMENT_KEY): a for a in map(_attachment, old or [])}
    after = {tuple(a[k] for k in ATTACHMENT_KEY): a for a in map(_attachment, new or [])}
    retitled = [
        {**{k: after[key][k] for k in ATTACHMENT_KEY}, 'old': before[key]['제목'], 'new': after[key]['제목']}
        for key in after
        if key in before and before[key]['제목'] != after[key]['제목']
    ]
    return {
        'added': [a for key, a in after.items() if key not in before],
        'removed': [a for key, a in before.items() if key not in after],
        'retitled': retitled,
    }


def is_empty(changes: dict) -> bool:
    return not any(changes.values())


def meta_changes(old: dict, new: dict) -> dict:
    keys = [k for k in dict.fromkeys([*old, *new]) if k not in SKIP_META]
    return {k: {'old': old.get(k), 'new': new.get(k)} for k in keys if old.get(k) != new.get(k)}
