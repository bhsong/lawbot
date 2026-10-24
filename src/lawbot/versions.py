# src/lawbot/versions.py  →  uv run python -m lawbot.versions
# 실제 설치된 패키지·맥 사양을 출력 (측정 결과와 함께 기록). 모델은 올리지 않는다
import platform
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version

PACKAGES = ('gradio', 'gradio-client', 'mlx', 'mlx-metal', 'mlx-lm', 'torch', 'sentence-transformers',
            'transformers', 'pandas', 'numpy')

def sysctl(name):
    r = subprocess.run(['sysctl', '-n', name], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else '?'

def main():
    print('python', sys.version.split()[0], '·', platform.platform())
    for p in PACKAGES:
        try:
            print(f'{p:22} {version(p)}')
        except PackageNotFoundError:
            print(f'{p:22} (설치 안 됨)')
    if platform.system() == 'Darwin':
        print('칩', sysctl('machdep.cpu.brand_string'), '· 메모리', int(sysctl('hw.memsize')) / 2**30, 'GiB')
        import mlx.core as mx
        info = mx.device_info() if hasattr(mx, 'device_info') else mx.metal.device_info()
        print('MLX 권장 작업 메모리', round(info.get('max_recommended_working_set_size', 0) / 2**30, 1), 'GiB')

if __name__ == '__main__':
    main()
