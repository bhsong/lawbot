# lawbot_model_server/revision.py  →  모델 폴더 지문(/v1/models의 revision)과 서버 git 커밋(/health의 commit)
# 지문: 폴더 안 모든 파일(숨김 제외)의 상대 경로·크기·앞뒤 EDGE바이트를 경로 순서로 sha256
#   가중치 전체(27B 약 15GB)를 해시하지 않는다: 시작이 수십 초 늘어나기 때문. 폴더 교체·가중치 변경은 이것으로 잡힌다
#   (docs/archives/e2e-playwright.md 5-1절)
import hashlib
import subprocess
from pathlib import Path

EDGE = 1 << 20  # 파일마다 앞뒤로 읽는 바이트


def fingerprint(path):
    """'sha256:<16자>' 또는 폴더가 없으면(경로 없음 포함) None"""
    if path is None or not Path(path).is_dir():
        return None
    root = Path(path)
    h = hashlib.sha256()
    files = sorted(
        p
        for p in root.rglob('*')
        if p.is_file() and not any(part.startswith('.') for part in p.relative_to(root).parts)
    )
    for p in files:
        size = p.stat().st_size
        h.update(f'{p.relative_to(root).as_posix()}\0{size}\0'.encode())
        with p.open('rb') as f:
            h.update(f.read(EDGE))
            if size > EDGE:
                f.seek(max(EDGE, size - EDGE))
                h.update(f.read(EDGE))
    return 'sha256:' + h.hexdigest()[:16]


def git_commit(repo):
    """repo의 HEAD 커밋 40자, 작업 트리에 변경이 있으면 '+dirty'. git 저장소가 아니거나 git이 없으면 None"""

    def run(*args):
        return subprocess.run(
            ['git', '-C', str(repo), *args], capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()

    try:
        head = run('rev-parse', 'HEAD')
        dirty = bool(run('status', '--porcelain', '--untracked-files=no'))
    except OSError, subprocess.SubprocessError:
        return None
    return head + ('+dirty' if dirty else '')
