# src/lawbot/bench_models.py  →  uv run python -m lawbot.bench_models --tag m32_v1_r2
# 웹앱과 같은 경로(service.load_app·ask)로 Fast/Pro를 번갈아 질문하며 지연·메모리를 잰다 (UI 없이, 맥 전용)
#   LAWBOT_MAX_RESIDENT=2: 동시 상주 / 1: 전환 때마다 다시 로딩 (전환 시간 측정)
# 결과: results/bench_<tag>.csv (질문마다 한 행) + 모델별 중앙값 표
import argparse
import platform
import re
import subprocess
import time
import pandas as pd
from lawbot.answer_format import concise
from lawbot.paths import RESULTS
from lawbot.service import ask, load_app

# 지연 측정용 고정 질문 (평가 데이터가 아님). 첫 질문은 가이드의 간결형 예시
QUESTIONS = ['연장근로수당은 얼마나 더 받나요?',
             '수습 기간에도 해고 예고를 해야 하나요?',
             '주휴수당은 어떤 조건에서 받을 수 있나요?',
             '회사가 연차를 못 쓰게 하면 어떻게 되나요?',
             '퇴직금은 어떻게 계산하나요?']
PRESSURE = {1: '정상', 2: '경고', 4: '위험'}

def sysctl(name):
    if platform.system() != 'Darwin':
        return None
    r = subprocess.run(['sysctl', '-n', name], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None

def memory():
    """macOS 메모리 압박 단계·스왑 사용량, MLX·PyTorch MPS가 잡은 메모리(GB)"""
    m = {}
    level = sysctl('kern.memorystatus_vm_pressure_level')
    m['pressure'] = PRESSURE.get(int(level), level) if level else None
    swap = sysctl('vm.swapusage')          # 'total = 2048.00M  used = 1001.25M  free = ...'
    used = re.search(r'used = ([\d.]+)M', swap or '')
    m['swap_used_gb'] = round(float(used[1]) / 1024, 2) if used else None
    try:
        import mlx.core as mx
        m['mlx_active_gb'] = round(mx.get_active_memory() / 1e9, 2)
        m['mlx_peak_gb'] = round(mx.get_peak_memory() / 1e9, 2)
    except ImportError:
        pass
    try:
        import torch
        m['mps_driver_gb'] = round(torch.mps.driver_allocated_memory() / 1e9, 2)
    except (ImportError, RuntimeError, AttributeError):
        pass
    return m

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', required=True, help='결과 파일 이름 (예: m32_v1_r2)')
    ap.add_argument('--models', default='fast,pro', help='번갈아 물을 모델 key 순서')
    ap.add_argument('--rounds', type=int, default=2, help='질문 목록 반복 횟수 (1회차는 워밍업 포함)')
    ap.add_argument('--early-stop', action='store_true', help='간결형 조기 종료 조건으로 측정')
    args = ap.parse_args()
    pd.set_option('display.width', 200, 'display.max_columns', None)

    print('시작 메모리', memory())
    t0 = time.perf_counter()
    app = load_app()
    print(f'앱 준비 {time.perf_counter() - t0:.1f}초 · 상주 {list(app.models.loaded)} ·', memory())
    rows = []
    for rnd in range(args.rounds):
        for q in QUESTIONS:
            for key in args.models.split(','):
                for stage, hits, raw, st in ask(app, q, key, early_stop=args.early_stop):
                    pass
                rows.append({'round': rnd, 'model': key, 'fmt': app.models.specs[key].fmt, 'question': q,
                             **st, **memory(),
                             'answer_len': len(raw), 'concise_len': len(concise(raw, hits)),
                             'resident': ','.join(app.models.loaded), 'answer': raw})
                r = rows[-1]
                print(f"[{rnd}] {key:4} load={r['load'] or 0:5.1f} ttft={r.get('ttft', 0):5.2f} "
                      f"first={r.get('first_text', 0):5.2f} tps={r['tps'] or 0:5.1f} total={r['total']:5.1f} "
                      f"mlx_peak={r.get('mlx_peak_gb')} pressure={r['pressure']} swap={r['swap_used_gb']}")
    o = pd.DataFrame(rows)
    RESULTS.mkdir(exist_ok=True)
    o.to_csv(RESULTS / f'bench_{args.tag}.csv', index=False, encoding='utf-8-sig')
    warm = o[o['round'] > 0] if args.rounds > 1 else o
    cols = ['search', 'ttft', 'first_text', 'tps', 'total', 'gen_tokens', 'prompt_tokens', 'prompt_tps']
    print(f"\n[중앙값, {'워밍업(1회차) 제외 ' if args.rounds > 1 else ''}{len(warm)}건]")
    print(warm.groupby('model')[[c for c in cols if c in warm]].median().round(2))
    print('\n전환(로딩) 초:', o.loc[o['load'].notna(), ['round', 'model', 'load']].round(1).values.tolist())
    print('최대 MLX 메모리(GB):', o['mlx_peak_gb'].max() if 'mlx_peak_gb' in o else None,
          '· 최악 압박:', o['pressure'].dropna().unique().tolist(), '· 최대 스왑(GB):', o['swap_used_gb'].max())
    print('잘림(finish=length):', o.groupby('model')['finish'].apply(lambda s: (s == 'length').sum()).to_dict())

if __name__ == '__main__':
    main()
