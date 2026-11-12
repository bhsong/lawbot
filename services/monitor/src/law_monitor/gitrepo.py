"""git 호출 (subprocess). 저장소는 작업 트리 없는 부분 클론(bare, --filter=blob:none)으로 둔다.

- 작업 트리가 없으니 pull·reset이 필요 없다. fetch는 '+refs/heads/*:refs/heads/*'로 강제 푸시도 그대로 받는다.
- 파일 내용(blob)은 처음 읽을 때 원격에서 받아 온다 (대상 파일만 받으므로 저장소 전체보다 훨씬 작다).
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

ZERO = '0' * 40


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Change:
    status: str  # A, M, D, R (T·C는 M·A로 취급)
    old_path: str | None
    new_path: str | None
    old_blob: str  # 없으면 ZERO
    new_blob: str


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    subject: str
    date: str  # author date (legalize-kr은 공포일)


class GitRepo:
    def __init__(self, path: Path):
        self.path = path
        self._blobs: dict[str, str] = {}

    def run(self, *args: str, cwd: Path | None = None) -> str:
        env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C'}
        out = subprocess.run(['git', *args], cwd=cwd or self.path, env=env, capture_output=True, check=False)
        if out.returncode != 0:
            raise GitError(
                f'git {" ".join(args[:3])} 실패 ({out.returncode}): {out.stderr.decode(errors="replace").strip()}'
            )
        return out.stdout.decode('utf-8', errors='replace')

    def ensure(self, url: str) -> None:
        """없으면 부분 클론, 있으면 fetch."""
        if not (self.path / 'HEAD').exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.run('clone', '--bare', '--filter=blob:none', '--quiet', url, str(self.path), cwd=self.path.parent)
            self.run('config', 'remote.origin.fetch', '+refs/heads/*:refs/heads/*')
            return
        self.run('fetch', '--prune', '--quiet', 'origin')

    def head(self) -> str:
        return self.run('rev-parse', 'HEAD').strip()

    def resolve(self, rev: str) -> str:
        return self.run('rev-parse', '--verify', '--quiet', f'{rev}^{{commit}}').strip()

    def has_commit(self, sha: str) -> bool:
        try:
            self.run('cat-file', '-e', f'{sha}^{{commit}}')
            return True
        except GitError:
            return False

    def is_ancestor(self, older: str, newer: str) -> bool:
        if not self.has_commit(older):
            return False
        try:
            self.run('merge-base', '--is-ancestor', older, newer)
            return True
        except GitError:
            return False

    def rev_list(self, since: str, until: str, pathspecs: list[str] | None = None) -> list[str]:
        """since..until 커밋 (오래된 것부터). 날짜가 아니라 커밋 그래프로 고른다 (커밋 날짜가 공포일이라서)."""
        args = ['rev-list', '--reverse', '--no-merges', f'{since}..{until}']
        if pathspecs:
            args += ['--', *pathspecs]
        return self.run(*args).split()

    def info(self, sha: str) -> CommitInfo:
        out = self.run('log', '-1', '--format=%H%x00%s%x00%aI', sha)
        full, subject, date = out.rstrip('\n').split('\x00')
        return CommitInfo(full, subject, date)

    def changes(self, sha: str, pathspecs: list[str] | None = None) -> list[Change]:
        args = ['diff-tree', '-r', '-M', '--root', '--no-commit-id', '-z', sha]
        if pathspecs:
            args += ['--', *pathspecs]
        fields = self.run(*args).split('\x00')
        out, i = [], 0
        while i < len(fields) and fields[i]:
            meta = fields[i].lstrip(':').split()
            _, _, old_blob, new_blob, status = meta
            kind = status[0]
            if kind in 'RC':
                old_path, new_path = fields[i + 1], fields[i + 2]
                i += 3
                if kind == 'C':
                    old_path, old_blob, kind = None, ZERO, 'A'
            else:
                path = fields[i + 1]
                i += 2
                old_path = None if kind == 'A' else path
                new_path = None if kind == 'D' else path
                kind = 'M' if kind == 'T' else kind
            out.append(Change(kind, old_path, new_path, old_blob, new_blob))
        return out

    def ls_tree(self, rev: str) -> list[tuple[str, str]]:
        """(경로, blob) 전체 목록. 내용은 받지 않는다."""
        out = []
        for entry in self.run('ls-tree', '-r', '-z', '--full-tree', rev).split('\x00'):
            if not entry:
                continue
            meta, path = entry.split('\t', 1)
            _, kind, blob = meta.split()
            if kind == 'blob':
                out.append((path, blob))
        return out

    def blob(self, sha: str) -> str:
        if sha not in self._blobs:
            self._blobs[sha] = self.run('cat-file', 'blob', sha)
        return self._blobs[sha]
