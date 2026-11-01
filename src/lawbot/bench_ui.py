# src/lawbot/bench_ui.py  →  (다른 터미널에서 lawbot-app 실행 중) uv run python -m lawbot.bench_ui --tag m32_v1_ui
# 실행 중인 웹앱에 로그인해 /gradio_api/call/chat 스트림을 받으며 '질문 전송 → 첫 글자 도착' 시간을 잰다
# 서버 측 측정(bench_models)에 Gradio 큐·전송 지연이 더해진 값. 브라우저 그리기 시간(수십 ms)은 포함하지 않음
# 접속 대상은 127.0.0.1:<웹앱 포트> (security.server_port: .env의 LAWBOT_PORT). 계정은 웹앱과 같은 LAWBOT_USER/LAWBOT_PW 환경 변수
import argparse
import json
import os
import time

import httpx
import pandas as pd

from lawbot import envfile
from lawbot.bench_models import QUESTIONS
from lawbot.paths import RESULTS
from lawbot.security import SERVER_NAME, server_port


def ask_timed(c, question, model_key, fmt):
    t0 = time.perf_counter()
    r = c.post('/gradio_api/call/chat', json={'data': [question, model_key, fmt]})
    r.raise_for_status()
    first = last = None
    answer, stats, error = '', '', None
    with c.stream('GET', f"/gradio_api/call/chat/{r.json()['event_id']}", timeout=600) as s:
        event = None
        for line in s.iter_lines():
            if line.startswith('event: '):
                event = line.removeprefix('event: ')
            elif line.startswith('data: '):
                data = json.loads(line.removeprefix('data: '))
                if event == 'error':
                    error = data
                    break
                if isinstance(data, list) and data and data[0]:
                    answer, stats = data[0], data[-1]
                    if first is None and not answer.startswith('⏳'):
                        first = time.perf_counter() - t0
                last = time.perf_counter() - t0
    return {'model': model_key, 'fmt': fmt, 'question': question, 'ui_first_text': first, 'ui_done': last,
            'server_stats': stats, 'error': error, 'answer': answer}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', required=True)
    ap.add_argument('--models', default='fast,pro')
    ap.add_argument('--fmt', default='concise', choices=['concise', 'full'])
    args = ap.parse_args()
    pd.set_option('display.width', 200, 'display.max_columns', None)
    envfile.load()                       # 웹앱과 같은 .env (포트·계정)
    user, pw = os.environ.get('LAWBOT_USER', ''), os.environ.get('LAWBOT_PW', '')
    if not user.strip() or not pw.strip():
        raise SystemExit('LAWBOT_USER/LAWBOT_PW 환경 변수가 필요합니다 (웹앱과 같은 값)')
    rows = []
    with httpx.Client(base_url=f'http://{SERVER_NAME}:{server_port()}', timeout=30) as c:
        if c.post('/login', data={'username': user, 'password': pw}).status_code != 200:
            raise SystemExit('로그인 실패')
        for q in QUESTIONS:
            for key in args.models.split(','):
                rows.append(ask_timed(c, q, key, args.fmt))
                r = rows[-1]
                print(f"{key:4} 첫 글자 {r['ui_first_text'] or 0:5.1f}초 · 완료 {r['ui_done'] or 0:5.1f}초 · {r['server_stats']}")
    o = pd.DataFrame(rows)
    RESULTS.mkdir(exist_ok=True)
    o.to_csv(RESULTS / f'bench_ui_{args.tag}.csv', index=False, encoding='utf-8-sig')
    print(o.groupby('model')[['ui_first_text', 'ui_done']].median().round(2))
    print('15초 안에 첫 글자:', o.groupby('model')['ui_first_text'].apply(lambda s: f'{(s <= 15).sum()}/{len(s)}').to_dict())

if __name__ == '__main__':
    main()
