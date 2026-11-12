"""Synthetic bare repositories shaped like monitor's admrule-kr and legalize-kr clones (no real data)."""

import os
import subprocess
from pathlib import Path

GIT_ENV = {
    **os.environ,
    'GIT_AUTHOR_NAME': 'test',
    'GIT_AUTHOR_EMAIL': 'test@example.invalid',
    'GIT_COMMITTER_NAME': 'test',
    'GIT_COMMITTER_EMAIL': 'test@example.invalid',
}

PENSION = 'Ministry-A/_본부/고시/가상 연금 상한액/본문.md'
INJURY = 'Ministry-B/_본부/고시/2026년도 가상 산재율/본문.md'
UNRELATED = 'Ministry-B/_본부/고시/가상 다른 고시/본문.md'
TAX_DECREE = 'kr/가상소득세법/시행령.md'
HEALTH_DECREE = 'kr/가상건강보험법/시행령.md'

CONFIG = """\
sources:
  - key: INS-01
    title: 가상 연금 상·하한
    category: insurance
    admrule: {agency: Ministry-A, name: '^가상 연금 상한액$'}
  - key: INS-03
    title: 가상 산재율
    category: insurance
    admrule: {agency: Ministry-B, name: '^\\d{4}년도 가상 산재율$'}
  - key: TAX-01
    title: 가상 간이세액표
    category: tax
    form: {file: kr/가상소득세법/시행령.md, kind: 별표, number: '0002', branch: '00'}
  - key: INS-02H
    title: 가상 건강보험료율
    category: insurance
    article: {file: kr/가상건강보험법/시행령.md, article: 제44조}
"""


def admrule(name: str, agency: str, *, rule_id: str, number: str, effective: str, body: str, flseq: str = '') -> str:
    attachments = (
        "첨부파일:\n- 별표번호: '0000'\n  별표가지번호: '00'\n  별표구분: '별지'\n"
        f"  제목: '{name}'\n  파일링크: 'https://law.example.invalid/f?flSeq={flseq}1'\n"
        f"  PDF링크: 'https://law.example.invalid/f?flSeq={flseq}3'\n"
        if flseq
        else '첨부파일: []\n'
    )
    return (
        f"---\n행정규칙ID: '{rule_id}'\n행정규칙명: '{name}'\n행정규칙종류: '고시'\n소관부처명: '{agency}'\n"
        f"발령번호: '{number}'\n발령일자: 2025-12-31\n시행일자: {effective}\n"
        f"출처: 'https://law.example.invalid/admrule/{rule_id}'\n{attachments}---\n\n# {name}\n\n{body}\n"
    )


def decree(title: str, *, effective: str, attachments: str = '', body: str = '') -> str:
    return (
        f'---\n제목: {title}\n법령구분: 대통령령\n시행일자: {effective}\n상태: 시행\n'
        f'출처: https://law.example.invalid/법령/{title}\n첨부파일:{attachments or " []"}\n---\n\n{body}\n'
    )


def form_entry(number: str, kind: str, title: str, flseq: str) -> str:
    return (
        f"\n- 별표번호: '{number}'\n  별표가지번호: '00'\n  별표구분: {kind}\n  제목: {title}\n"
        f'  파일링크: https://law.example.invalid/f?flSeq={flseq}1\n'
        f'  PDF링크: https://law.example.invalid/f?flSeq={flseq}3'
    )


def baseline() -> dict[str, dict[str, str]]:
    return {
        'admrule-kr': {
            PENSION: admrule(
                '가상 연금 상한액',
                'Ministry-A',
                rule_id='901',
                number='2026-1',
                effective='2026-07-01',
                body='  1\\. 하한액 : 100천원\n  2\\. 상한액 : 900천원',
            ),
            INJURY: admrule(
                '2026년도 가상 산재율',
                'Ministry-B',
                rule_id='902',
                number='2025-9',
                effective='2026-01-01',
                body='  **1\\.** 요율은 별지와 같다.',
                flseq='5000',
            ),
            UNRELATED: admrule(
                '가상 다른 고시', 'Ministry-B', rule_id='903', number='2026-2', effective='2026-02-01', body='무관'
            ),
        },
        'legalize-kr': {
            TAX_DECREE: decree(
                '가상소득세법 시행령',
                effective='2026-10-01',
                attachments=form_entry('0001', '별표', '가상 별표 1', '7000')
                + form_entry('0002', '별표', '가상 간이세액표(제1조 관련)', '7100'),
            ),
            HEALTH_DECREE: decree(
                '가상건강보험법 시행령',
                effective='2026-03-01',
                body='##### 제43조 (앞 조문)\n\n앞 내용\n\n##### 제44조 (보험료율)\n\n**①** 보험료율은 1만분의 719로 한다.'
                '\n\n##### 제44조의2 (뒤 조문)\n\n뒤 내용',
            ),
        },
    }


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(['git', *args], cwd=cwd, env=GIT_ENV, check=True, capture_output=True, text=True).stdout


def publish(root: Path, trees: dict[str, dict[str, str]]) -> Path:
    """Commit each tree to a work repository and push it to a bare clone under root/repos."""
    repos = root / 'repos'
    for name, files in trees.items():
        work = root / 'work' / name
        bare = repos / name
        if not work.exists():
            work.mkdir(parents=True)
            git('init', '-q', '-b', 'main', cwd=work)
            bare.mkdir(parents=True)
            git('init', '-q', '--bare', '-b', 'main', cwd=bare)
            git('remote', 'add', 'origin', str(bare), cwd=work)
        for path, text in files.items():
            target = work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding='utf-8')
        git('add', '-A', cwd=work)
        git('commit', '-q', '--allow-empty', '-m', 'update', cwd=work)
        git('push', '-q', 'origin', 'main', cwd=work)
    return repos


def head(repos: Path, name: str) -> str:
    return git('--git-dir', str(repos / name), 'rev-parse', 'HEAD', cwd=repos).strip()
