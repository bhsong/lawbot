# tests/chat_support.py  →  질문 하나를 새 화면 API의 이벤트 흐름(web.chat_events)으로 끝까지 돌려 화면에 남는 상태를 본다
# (예전 Gradio chat 함수의 단계별 출력을 검사하던 테스트가 같은 기대값을 이 결과로 본다)
import types

from lawbot import web


def run(app, question, model='fast', fmt='full', remember=None):
    """→ text(마지막 화면 글), sources(묶음 목록), src(조문 카드 글), stats, status, error(안내 문구), kind, done, events"""
    r = types.SimpleNamespace(text='', sources=None, stats='', status=None, error=None, kind=None, done=None, events=[])
    for event, data in web.chat_events(app, question, model, fmt, remember):
        r.events.append((event, data))
        if event in ('answer', 'done'):
            r.text = data['text']
        if event == 'done':
            r.done = data
        elif event == 'sources':
            r.sources = data['groups']
        elif event == 'stats':
            r.stats = data['text']
        elif event == 'status':
            r.status = data['text']
        elif event == 'error':
            r.kind, r.error = data['kind'], data['message']
    r.src = sources_text(r.sources or [])
    return r


def sources_text(groups):
    """조문 카드 묶음을 글 하나로: 묶음 제목, 카드마다 '제목 · 점수' 줄과 본문"""
    out = []
    for g in groups:
        if g['label']:
            out.append(g['label'])
        out += [f'{i["title"]} · {i["scores"]}\n{i["body"]}' for i in g['items']]
    return '\n\n'.join(out)
