# src/lawbot/log_summary.py  →  uv run python -m lawbot.log_summary [logs/requests.jsonl] [--caller lawbot-app] [--since 2026-11-01]
# 웹앱 요청 로그(JSONL, service.log_request)를 요약한다: 상태별 건수, 단계별 시간 중앙값·p95(성공 요청), 오류 유형별 건수
# 로그에는 질문 원문·답변이 없다. 모델을 올리지 않는다
import argparse
import json
from pathlib import Path

import pandas as pd

from lawbot.service import MS_FIELDS, REQUEST_LOG, request_log_path


def load(path):
    """JSONL → DataFrame. 깨진 줄(쓰는 중에 멈춘 마지막 줄 등)은 건너뛰고 개수를 함께 돌려준다"""
    rows, bad = [], 0
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            bad += 1
    return pd.DataFrame(rows), bad


def summarize(d):
    """요약 줄 목록"""
    out = [f'요청 {len(d)}건 · ' + ' · '.join(f'{k} {v}' for k, v in d['status'].value_counts().items())]
    ok = d[d['status'] == 'ok']
    for (mode, model), g in ok.groupby(['mode', 'model']):
        out.append(f'[{mode} · {model}] 성공 {len(g)}건')
        for k in MS_FIELDS:
            if f'{k}_ms' not in g:  # 하위법령 필드가 생기기 전 로그 (decree_search_ms 등)
                continue
            s = g[f'{k}_ms'].dropna()
            if len(s):
                out.append(
                    f'  {k:10} 중앙값 {s.median() / 1000:6.2f}초 · p95 {s.quantile(0.95) / 1000:6.2f}초 ({len(s)}건)'
                )
        if 'n_decree' in g and g['n_decree'].notna().any():
            n = g['n_decree'].dropna()
            out.append(f'  시행령·시행규칙 첨부 평균 {n.mean():.2f}개 · 없음 {(n == 0).mean():.0%} ({len(n)}건)')
        if g['peak_gb'].notna().any():
            out.append(f'  peak_gb    최대 {g["peak_gb"].max():.1f}GB')
        if (g['finish'] == 'length').any():
            out.append(f'  최대 길이 잘림 {int((g["finish"] == "length").sum())}건')
    err = d[d['status'] == 'error']
    if len(err):
        out.append('오류 유형: ' + ' · '.join(f'{k} {v}' for k, v in err['error_type'].value_counts().items()))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description='웹앱 요청 로그(JSONL) 요약')
    ap.add_argument('path', nargs='?', default=None, help=f'로그 파일 (기본: LAWBOT_REQUEST_LOG 또는 {REQUEST_LOG})')
    ap.add_argument('--caller', default=None, help='실행한 명령으로 거름 (예: lawbot-app, 평가 실행 제외)')
    ap.add_argument('--since', default=None, help='이 날짜(YYYY-MM-DD) 이후 요청만')
    a = ap.parse_args(argv)
    path = request_log_path() if a.path is None else Path(a.path)
    if path is None or not path.is_file():
        raise SystemExit(f'요청 로그가 없습니다: {path}')
    d, bad = load(path)
    if a.caller and len(d):
        d = d[d['caller'] == a.caller]
    if a.since and len(d):
        d = d[d['ts'] >= a.since]
    print(f'[log_summary] {path}' + (f' · 읽지 못한 줄 {bad}' if bad else ''))
    if not len(d):
        print('해당 요청이 없습니다')
        return
    for line in summarize(d):
        print(line)


if __name__ == '__main__':
    main()
