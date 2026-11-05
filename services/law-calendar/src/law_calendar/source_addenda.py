"""Recover an explicitly matched borrowed addendum from a saved law version."""

import re
from datetime import date

ADDENDUM = re.compile(
    r'^부칙[ \t]*(?:\((?P<owner>[^)\n]+)\))?[ \t]*'
    r'<제(?P<number>\d+)호[ \t]*,[ \t]*(?P<day>\d{4}\.\d{1,2}\.\d{1,2})\.?>[ \t]*$',
    re.MULTILINE,
)
NUMBER = re.compile(r"""^공포번호:[ \t]*['"]?(\d+)['"]?[ \t]*$""", re.MULTILINE)
SECTION = re.compile(r'^제([1-9]\d*)조(?=[ \t]*\(|[ \t]+생략)', re.MULTILINE)
LAW_GROUP = re.compile(r'^[ \t]*[①-⑳][ \t]*(.+?) 일부를 다음과 같이 개정한다\.[ \t]*$', re.MULTILINE)
GROUP_BOUNDARY = re.compile(r'^[ \t]*[①-⑳]', re.MULTILINE)
OVERRIDE = re.compile(r'시행일\s*(?:은|을)\b|시행(?:한다|하고|하며|하되)')


def borrowed_clause(text: str, law: str, article: str, promulgated: str) -> str:
    """Read only a dated enactment that expressly names this law and article."""
    parts = text.replace('\r\n', '\n').split('\n---\n', 1)
    if len(parts) != 2 or not parts[0].startswith('---\n'):
        return ''
    numbers = NUMBER.findall(parts[0])
    if len(numbers) != 1:
        return ''
    body = parts[1]
    headings = list(ADDENDUM.finditer(body))
    blocks = []
    for index, heading in enumerate(headings):
        if not heading['owner'] or heading['number'] != numbers[0]:
            continue
        try:
            year, month, day = map(int, heading['day'].split('.'))
            stamp = date(year, month, day).isoformat()
        except ValueError:
            return ''
        if stamp == promulgated:
            end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
            blocks.append(body[heading.end() : end])
    if len(blocks) != 1:
        return ''
    block = blocks[0]
    sections = list(SECTION.finditer(block))
    if not sections or sections[0][1] != '1' or len(sections) < 2 or block[: sections[0].start()].strip():
        return ''
    clause = block[sections[0].start() : sections[1].start()].strip()
    if not re.match(r'제1조\s*\(시행일\)', clause) or '다만' in clause or OVERRIDE.search(block[sections[1].start() :]):
        return ''
    group_matches = []
    for index, section in enumerate(sections[1:], start=1):
        end = sections[index + 1].start() if index + 1 < len(sections) else len(block)
        amendments = block[section.start() : end]
        if not re.match(r'제\d+조\s*\(다른 법률의 개정\)', amendments):
            continue
        for group in LAW_GROUP.finditer(amendments):
            if group[1] == law:
                end_group = GROUP_BOUNDARY.search(amendments, group.end())
                group_matches.append(amendments[group.end() : end_group.start() if end_group else len(amendments)])
    if len(group_matches) == 1 and re.search(rf'^[ \t]*{re.escape(article)}[ \t]', group_matches[0], re.MULTILINE):
        return clause
    return ''
