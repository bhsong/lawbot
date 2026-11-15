# lawbot_model_server/revision.py  →  모델 폴더 지문(/v1/models의 revision)과 서버 git 커밋(/health의 commit)
# 지문: 폴더 안 모든 파일(숨김 제외)의 상대 경로·크기·앞뒤 EDGE바이트를 경로 순서로 sha256
#   가중치 전체(27B 약 15GB)를 해시하지 않는다: 시작이 수십 초 늘어나기 때문. 폴더 교체·가중치 변경은 이것으로 잡힌다
#   (docs/archives/e2e-playwright.md 5-1절)
import subprocess

from lawbot_common.model_revision import EDGE as EDGE
from lawbot_common.model_revision import fingerprint as fingerprint


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
