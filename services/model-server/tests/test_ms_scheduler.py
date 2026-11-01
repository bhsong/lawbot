# services/model-server/tests/test_ms_scheduler.py  →  GPU 스레드 스케줄러와 생성 엔진 (동시 추론, PR 3)
# 스케줄러: 가짜 모델 + 테스트용 순차 엔진 (ms_fakes.FakeEngine, 모델 파일 불필요)
# BatchEngine: 무작위 가중치의 작은 qwen3·qwen3_5 모델 + 실제 mlx_lm BatchGenerator. 순차 생성(generate_step)과 토큰 비교
import asyncio
import time
import types

import mlx.core as mx
import pytest
from mlx_lm.generate import generate_step
from mlx_lm.models import qwen3, qwen3_5
from mlx_lm.sample_utils import make_sampler

from lawbot_model_server.chat import THINK, BatchEngine
from lawbot_model_server.scheduler import ModelLoadError, QueueFull, Scheduler

from .ms_fakes import Compute, FakeChat, FakeEmbedder, registry


def ask(q):
    return [{'role': 'user', 'content': q}]


KW = {'max_tokens': 64, 'temperature': 0.0, 'top_p': None, 'enable_thinking': False}


async def collect(ticket, log=None, name=None):
    """('item'…)* → ('end') 이면 응답 목록, ('error', e)면 e를 올림. log에 (name, kind) 순서를 남긴다"""
    items = []
    while True:
        kind, value = await ticket.next()
        if log is not None:
            log.append((name, kind))
        if kind == 'item':
            items.append(value)
        elif kind == 'end':
            return items
        else:
            raise value


def text(items):
    return ''.join(r.text for r in items)


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


# ---- 스케줄러 (가짜 모델) ----
def test_two_streams_interleave_within_limit():
    FakeChat.per_prompt = {'q1': ['a'] * 6, 'q2': ['b'] * 6}
    FakeChat.delay = 0.003
    reg = registry()
    s = Scheduler(reg, max_concurrent=2)

    async def main():
        log = []
        a = s.chat('chat-a', ask('q1'), KW)
        b = s.chat('chat-a', ask('q2'), KW)
        ra, rb = await asyncio.gather(collect(a, log, 'a'), collect(b, log, 'b'))
        return log, ra, rb

    log, ra, rb = run(main())
    assert text(ra) == 'a' * 6 and text(rb) == 'b' * 6
    assert log.index(('b', 'item')) < log.index(('a', 'end'))  # 둘이 번갈아 진행
    assert reg.chat['chat-a'].max_open == 2
    assert Compute.overlaps == 0 and len(Compute.threads) == 1  # 연산은 GPU 스레드 하나에서 차례로
    assert s.pending() == 0


def test_requests_over_limit_wait_in_order():
    FakeChat.per_prompt = {f'q{i}': [str(i)] * 4 for i in range(3)}
    FakeChat.delay = 0.003
    reg = registry()
    s = Scheduler(reg, max_concurrent=2)

    async def main():
        log = []
        ts = [s.chat('chat-a', ask(f'q{i}'), KW) for i in range(3)]
        out = await asyncio.gather(*(collect(t, log, i) for i, t in enumerate(ts)))
        return log, out

    log, out = run(main())
    assert [text(r) for r in out] == ['0000', '1111', '2222']
    assert reg.chat['chat-a'].max_open == 2
    first_end = min(log.index((0, 'end')), log.index((1, 'end')))
    assert log.index((2, 'item')) > first_end  # 세 번째는 자리가 난 뒤 시작


def test_limit_one_is_sequential():
    FakeChat.per_prompt = {'q1': ['a'] * 3, 'q2': ['b'] * 3}
    reg = registry()
    s = Scheduler(reg, max_concurrent=1)

    async def main():
        log = []
        a, b = s.chat('chat-a', ask('q1'), KW), s.chat('chat-a', ask('q2'), KW)
        await asyncio.gather(collect(a, log, 'a'), collect(b, log, 'b'))
        return log

    log = run(main())
    assert log.index(('b', 'item')) > log.index(('a', 'end'))
    assert reg.chat['chat-a'].max_open == 1


def test_calls_run_between_generation_steps():
    """긴 생성 중에도 embeddings·rerank가 생성이 끝나기 전에 응답한다"""
    FakeChat.chunks, FakeChat.delay = ['x'] * 300, 0.002
    reg = registry()
    s = Scheduler(reg, max_concurrent=1)

    async def main():
        log = []
        t = s.chat('chat-a', ask('q'), KW)
        first = await t.next()
        assert first[0] == 'item'
        rest = asyncio.ensure_future(collect(t, log, 'chat'))  # 스트림을 계속 받는 중에
        c = s.call(lambda: reg.fixed['emb'].encode(['x']))
        k, v = await c.next()
        assert k == 'item' and v.shape == (1, 8)
        assert await c.next() == ('end', None)
        done_before = rest.done()
        await rest
        return done_before

    assert run(main()) is False
    assert Compute.overlaps == 0


def test_call_error_is_reported():
    s = Scheduler(registry(), max_concurrent=1)

    async def main():
        t = s.call(lambda: 1 / 0)
        return await t.next()

    kind, value = run(main())
    assert kind == 'error' and isinstance(value, ZeroDivisionError)


def test_cancel_frees_slot_and_stops_generation():
    FakeChat.per_prompt = {'long': ['x'] * 2000, 'short': ['y'] * 2}
    FakeChat.delay = 0.002
    reg = registry()
    s = Scheduler(reg, max_concurrent=1)

    async def main():
        a = s.chat('chat-a', ask('long'), KW)
        assert (await a.next())[0] == 'item'
        b = s.chat('chat-a', ask('short'), KW)
        a.cancel()
        return await collect(b)

    assert text(run(main())) == 'yy'
    m = reg.chat['chat-a']
    assert m.closed and m.yielded < 100 and m.open == 0
    assert s.pending() == 0


def test_cancelled_waiting_request_never_starts():
    FakeChat.per_prompt = {'a': ['x'] * 20, 'b': ['y']}
    FakeChat.delay = 0.002
    reg = registry()
    s = Scheduler(reg, max_concurrent=1)

    async def main():
        a = s.chat('chat-a', ask('a'), KW)
        assert (await a.next())[0] == 'item'
        b = s.chat('chat-a', ask('b'), KW)
        b.cancel()
        await collect(a)
        await asyncio.sleep(0.05)

    run(main())
    assert [c['messages'][-1]['content'] for c in reg.chat['chat-a'].calls] == ['a']
    assert s.pending() == 0


def test_model_switch_waits_for_running_and_keeps_order():
    """상주 1개: chat-a 생성 중 chat-b 요청이 오면 a가 끝난 뒤 바꾸고, 그 뒤에 온 a 요청은 b 다음"""
    FakeChat.per_prompt = {'a1': ['1'] * 8, 'b': ['b'] * 3, 'a2': ['2'] * 3}
    FakeChat.delay = 0.003
    loads = []
    reg = registry(max_resident=1, loads=loads)
    s = Scheduler(reg, max_concurrent=4)

    async def main():
        log = []
        a1 = s.chat('chat-a', ask('a1'), KW)
        assert (await a1.next())[0] == 'item'
        b = s.chat('chat-b', ask('b'), KW)
        a2 = s.chat('chat-a', ask('a2'), KW)
        await asyncio.gather(collect(a1, log, 'a1'), collect(b, log, 'b'), collect(a2, log, 'a2'))
        return log

    log = run(main())
    assert log.index(('b', 'item')) > log.index(('a1', 'end'))
    assert log.index(('a2', 'item')) > log.index(('b', 'end'))
    assert [p.name for p in loads if p.name in ('a', 'b')] == ['a', 'b', 'a']


def test_error_in_one_stream_does_not_stop_others():
    FakeChat.per_prompt = {'bad': ['x', RuntimeError('생성 실패')], 'ok': ['o'] * 5}
    FakeChat.delay = 0.002
    s = Scheduler(registry(), max_concurrent=2)

    async def main():
        bad, ok = s.chat('chat-a', ask('bad'), KW), s.chat('chat-a', ask('ok'), KW)
        return await asyncio.gather(collect(bad), collect(ok), return_exceptions=True)

    bad, ok = run(main())
    assert isinstance(bad, RuntimeError) and text(ok) == 'ooooo'


def test_model_load_error_keeps_cause():
    def broken(path):
        raise FileNotFoundError(f'모델 폴더 없음: {path}')

    reg = registry()
    reg.loaders['chat'] = broken
    s = Scheduler(reg, max_concurrent=2)

    async def main():
        return await s.chat('chat-a', ask('q'), KW).next()

    kind, e = run(main())
    assert kind == 'error' and isinstance(e, ModelLoadError) and isinstance(e.__cause__, FileNotFoundError)
    assert s.pending() == 0


def test_engine_creation_error_fails_request_and_thread_survives():
    """엔진을 못 만들면 그 요청만 오류. GPU 스레드는 살아서 다음 요청을 처리한다 (죽으면 요청이 영원히 기다림)"""
    made = []

    def make_engine(model, n):
        made.append(n)
        if len(made) == 1:
            raise AttributeError('engine 없음')
        return model.engine(n)

    s = Scheduler(registry(), max_concurrent=2, make_engine=make_engine)

    async def main():
        first = await s.chat('chat-a', ask('q'), KW).next()
        return first, await collect(s.chat('chat-a', ask('q'), KW))

    (kind, e), items = run(main())
    assert kind == 'error' and isinstance(e, AttributeError)
    assert text(items) == '【결론】예고해야 합니다.' and s.thread.is_alive() and s.pending() == 0


def test_engine_close_error_on_model_switch_thread_survives():
    """모델을 바꾸며 엔진을 닫다 오류가 나도 스레드는 산다. 새 모델 요청은 그대로 처리"""

    class BadClose(FakeChat):
        def engine(self, n):
            eng = super().engine(n)

            def close():
                raise RuntimeError('close 실패')

            eng.close = close
            return eng

    s = Scheduler(registry(chat=BadClose), max_concurrent=1)

    async def main():
        a = await collect(s.chat('chat-a', ask('q'), KW))
        b = await collect(s.chat('chat-b', ask('q'), KW))
        return a, b

    a, b = run(main())
    assert text(a) == text(b) == '【결론】예고해야 합니다.' and s.thread.is_alive()


def test_engine_comes_from_model():
    """엔진은 모델의 engine(상한)으로 만든다 (실제 ChatModel은 BatchEngine). 모델을 바꾸기 전까지 하나를 계속 쓴다"""
    reg = registry()
    s = Scheduler(reg, max_concurrent=3)

    async def main():
        a = await collect(s.chat('chat-a', ask('q'), KW))
        b = await collect(s.chat('chat-a', ask('q'), KW))
        return a, b

    a, b = run(main())
    assert text(a) == text(b) == '【결론】예고해야 합니다.' and reg.chat['chat-a'].engines == [3]


def test_queue_limit_rejects_new_chat_and_frees_after():
    # 생성 중 1 + 대기 2(상한)면 다음 요청은 바로 거부 (서버 대기열이 끝없이 늘지 않게). 대기가 빠지면 다시 받음
    FakeChat.per_prompt = {f'q{i}': [str(i)] * 20 for i in range(5)}
    FakeChat.delay = 0.003
    s = Scheduler(registry(), max_concurrent=1, max_queued=2)

    async def main():
        a = s.chat('chat-a', ask('q0'), KW)
        first = await a.next()  # 첫 토큰: a는 생성 중 (대기열에서 빠짐)
        b, c = s.chat('chat-a', ask('q1'), KW), s.chat('chat-a', ask('q2'), KW)
        with pytest.raises(QueueFull, match='2개'):
            s.chat('chat-a', ask('q3'), KW)
        ra, rb, rc = await asyncio.gather(collect(a), collect(b), collect(c))
        d = s.chat('chat-a', ask('q4'), KW)
        return first, ra, rb, rc, await collect(d)

    first, ra, rb, rc, rd = run(main())
    assert first[0] == 'item' and (text(rb), text(rc), text(rd)) == ('1' * 20, '2' * 20, '4' * 20)
    assert s.queued() == 0


def test_cancelled_waiting_requests_leave_queue():
    FakeChat.per_prompt = {'q0': ['a'] * 30, 'q1': ['b'], 'q2': ['c']}
    FakeChat.delay = 0.003
    s = Scheduler(registry(), max_concurrent=1, max_queued=1)

    async def main():
        a = s.chat('chat-a', ask('q0'), KW)
        await a.next()
        b = s.chat('chat-a', ask('q1'), KW)
        b.cancel()  # 정지 버튼: 대기 중에 끊김
        await collect(a)
        return await collect(s.chat('chat-a', ask('q2'), KW))  # 끊긴 요청은 자리를 차지하지 않음

    assert text(run(main())) == 'c' and s.queued() == 0


@pytest.mark.parametrize('n', [0, 17])
def test_limit_range(n):
    with pytest.raises(ValueError, match='max_concurrent'):
        Scheduler(registry(), max_concurrent=n)


def test_scheduler_thread_starts_lazily():
    s = Scheduler(registry(), max_concurrent=1)
    assert s.thread is None

    async def main():
        return await collect(s.chat('chat-a', ask('q'), KW))

    run(main())
    assert s.thread is not None and s.thread.daemon


def test_embedder_slow_call_delays_stream_but_not_reorders():
    """embeddings는 생성 스텝 사이에 끼어든다: 그동안 스트림이 멈췄다가 이어서 같은 글을 낸다"""
    FakeChat.chunks, FakeChat.delay = ['x'] * 20, 0.001
    FakeEmbedder.delay = 0.05
    reg = registry()
    s = Scheduler(reg, max_concurrent=1)

    async def main():
        t = s.chat('chat-a', ask('q'), KW)
        await t.next()
        c = s.call(lambda: reg.fixed['emb'].encode(['x']))
        t0 = time.perf_counter()
        items = await collect(t)
        await c.next()
        return items, time.perf_counter() - t0

    items, took = run(main())
    assert text(items) == 'x' * 19 and took >= 0.05


# ---- BatchEngine (작은 실제 MLX 모델 + mlx_lm BatchGenerator) ----
V = 97
EOS = 0


class CharDetok:
    """토큰 하나 = 글자 하나 (가 + t). mlx_lm 스트리밍 detokenizer와 같은 메서드"""

    def __init__(self):
        self.tokens, self.shown = [], 0

    def add_token(self, t):
        self.tokens.append(t)

    def finalize(self):
        pass

    @property
    def last_segment(self):
        s = ''.join(chr(0xAC00 + t) for t in self.tokens)
        out, self.shown = s[self.shown :], len(s)
        return out


class CharTok:
    bos_token = None

    def __init__(self, eos=(EOS,)):
        self.eos_token_ids = set(eos)
        self.encoded = []

    def encode(self, text, add_special_tokens=True):
        self.encoded.append(add_special_tokens)
        return [1 + ord(c) % (V - 1) for c in text]

    @property
    def detokenizer(self):
        return CharDetok()


def tiny(kind='qwen3'):
    mx.random.seed(0)
    if kind == 'qwen3':
        args = qwen3.ModelArgs(
            model_type='qwen3',
            hidden_size=64,
            num_hidden_layers=2,
            intermediate_size=128,
            num_attention_heads=4,
            rms_norm_eps=1e-6,
            vocab_size=V,
            num_key_value_heads=2,
            max_position_embeddings=4096,
            head_dim=16,
            tie_word_embeddings=True,
            rope_theta=10000.0,
        )
        m = qwen3.Model(args)
    else:  # 27B와 같은 구조: linear attention(ArraysCache) + full attention(KVCache)
        tc = dict(
            model_type='qwen3_5_text',
            hidden_size=64,
            intermediate_size=128,
            num_hidden_layers=4,
            num_attention_heads=4,
            num_key_value_heads=2,
            vocab_size=V,
            head_dim=16,
            linear_num_value_heads=4,
            linear_num_key_heads=2,
            linear_key_head_dim=16,
            linear_value_head_dim=16,
            full_attention_interval=2,
            tie_word_embeddings=True,
        )
        m = qwen3_5.Model(qwen3_5.ModelArgs(model_type='qwen3_5', text_config=tc))
    mx.eval(m.parameters())
    return m


def chat_model(model, tok=None, think=None):
    """ChatModel과 같은 속성 (model·tok·think·prompt). 프롬프트는 마지막 질문 그대로"""
    return types.SimpleNamespace(
        model=model,
        tok=tok or CharTok(),
        think=think,
        prompt=lambda messages, enable_thinking=False: messages[-1]['content'],
    )


def reference(model, tok, prompt, max_tokens):
    """stream_generate와 같은 규칙: eos면 그 토큰을 빼고 stop, max_tokens면 마지막 토큰 포함 length"""
    toks = []
    for t, _ in generate_step(
        mx.array(tok.encode(prompt)), model, max_tokens=max_tokens, sampler=make_sampler(temp=0.0)
    ):
        t = int(t)
        if t in tok.eos_token_ids:
            return toks, 'stop'
        toks.append(t)
        if len(toks) == max_tokens:
            return toks, 'length'
    raise AssertionError


def drive(engine, requests, late=None, steps_before_late=3):
    """requests: [(이름, 질문, kw)]. 모든 요청이 끝날 때까지 step. late는 몇 스텝 뒤에 넣는다 (연속 배치)"""
    out = {n: [] for n, _, _ in requests + ([late] if late else [])}
    ended, step = set(), 0
    for n, q, kw in requests:
        engine.add(n, ask(q), kw)
    while len(ended) < len(out):
        for ticket, kind, value in engine.step():
            if kind == 'item':
                out[ticket].append(value)
            elif kind == 'end':
                ended.add(ticket)
            else:
                raise value
        step += 1
        if late and step == steps_before_late:
            engine.add(late[0], ask(late[1]), late[2])
    return out


def chars(toks):
    return ''.join(chr(0xAC00 + t) for t in toks)


@pytest.mark.parametrize('kind', ['qwen3', 'qwen3_5'])
def test_batch_engine_matches_sequential_tokens(kind):
    """배치 1·3개와 중간 투입 모두 순차 생성과 같은 글 (작은 fp32 모델; 27B 4비트는 -m models·val로 확인)"""
    m = tiny(kind)
    tok = CharTok(eos=())
    prompts = {'p1': '해고 예고', 'p2': '임금', 'p3': '연차 유급휴가는 몇 일인가요' * 3}
    kw = KW | {'max_tokens': 20}
    ref = {n: reference(m, tok, q, 20) for n, q in prompts.items()}
    for reqs, late in (
        ([('p1', prompts['p1'], kw)], None),
        ([(n, q, kw) for n, q in prompts.items()], None),
        ([('p1', prompts['p1'], kw)], ('p3', prompts['p3'], kw)),
    ):
        eng = BatchEngine(chat_model(m, tok), 4)
        out = drive(eng, reqs, late)
        for n, items in out.items():
            assert text(items) == chars(ref[n][0])
            assert items[-1].finish_reason == 'length' and items[-1].generation_tokens == 20
        assert len(eng) == 0
        eng.close()


def test_batch_engine_stop_token_not_shown():
    m = tiny()
    toks, _ = reference(m, CharTok(eos=()), '해고 예고', 20)
    eos = toks[5]  # 여섯 번째로 나오는 토큰을 eos로: 그 앞에서 멈춰야 함
    first = toks.index(eos)
    tok = CharTok(eos=(eos,))
    out = drive(BatchEngine(chat_model(m, tok), 2), [('a', '해고 예고', KW | {'max_tokens': 20})])['a']
    assert text(out) == chars(toks[:first])
    assert out[-1].finish_reason == 'stop' and out[-1].text == '' and out[-1].generation_tokens == first + 1


def test_batch_engine_measurements_and_prompt_encoding():
    m = tiny()
    tok = CharTok(eos=())
    out = drive(BatchEngine(chat_model(m, tok), 2), [('a', '해고', KW | {'max_tokens': 4})])['a']
    last = out[-1]
    assert last.prompt_tokens == 2 and last.generation_tokens == 4
    assert last.prompt_tps > 0 and last.generation_tps > 0 and last.peak_memory > 0
    assert tok.encoded == [True]  # bos_token이 없으면 stream_generate처럼 add_special_tokens=True


def test_batch_engine_hides_thinking_per_request():
    """생각 끔 요청만 생각 블록을 숨긴다 (요청마다 상태가 따로)"""

    class ScriptGen:
        """BatchGenerator 대역: uid마다 정해진 토큰을 한 스텝에 하나씩"""

        def __init__(self, scripts):
            self.scripts, self.uids, self.count = scripts, {}, 0

        def insert(self, prompts, max_tokens, samplers):
            uid, self.count = self.count, self.count + 1
            self.uids[uid] = list(self.scripts[uid])
            return [uid]

        def next(self):
            out = []
            for uid, rest in list(self.uids.items()):
                t = rest.pop(0)
                out.append(types.SimpleNamespace(uid=uid, token=t, finish_reason='length' if not rest else None))
                if not rest:
                    del self.uids[uid]
            return [], out

        def close(self):
            pass

    class WordDetok(CharDetok):
        WORDS = ['<think>', '생각', '</think>\n\n', '답']

        @property
        def last_segment(self):
            s = ''.join(self.WORDS[t] for t in self.tokens)
            out, self.shown = s[self.shown :], len(s)
            return out

    tok = CharTok(eos=())
    tok.__class__ = type('WordTok', (CharTok,), {'detokenizer': property(lambda self: WordDetok())})
    script = [0, 1, 2, 3]
    gen = ScriptGen([script, script])
    eng = BatchEngine(chat_model(None, tok, THINK), 2, make_generator=lambda: gen)
    out = drive(eng, [('off', 'q', KW), ('on', 'q', KW | {'enable_thinking': True})])
    assert text(out['off']) == '답' and text(out['on']) == '<think>생각</think>\n\n답'


def test_batch_engine_shares_sampler_and_removes_and_fails_together():
    calls = {'insert': [], 'made': 0}

    class Gen:
        def __init__(self):
            calls['made'] += 1
            self.n, self.removed, self.fail = 0, [], False

        def insert(self, prompts, max_tokens, samplers):
            calls['insert'].append((max_tokens, samplers[0]))
            self.n += 1
            return [self.n - 1]

        def next(self):
            if self.fail:
                raise RuntimeError('Metal 오류')
            return [], []

        def remove(self, uids):
            self.removed += uids

        def close(self):
            pass

    gens = []
    eng = BatchEngine(chat_model(None), 4, make_generator=lambda: gens.append(Gen()) or gens[-1])
    eng.add('a', ask('q'), KW)
    eng.add('b', ask('q'), KW | {'max_tokens': 7})
    eng.add('c', ask('q'), KW | {'temperature': 0.7, 'top_p': 0.9})
    eng.add('e', ask('q'), KW | {'temperature': 0.7, 'top_p': 0.9})
    (ma, sa), (mb, sb), (mc, sc), (me, se) = calls['insert']
    assert (ma, mb) == ([64], [7]) and sa is sb and sc is se and sc is not sa  # 같은 설정은 sampler 공유
    eng.remove('b')
    assert gens[0].removed == [1] and len(eng) == 3
    gens[0].fail = True
    events = eng.step()
    assert sorted(t for t, k, _ in events if k == 'error') == ['a', 'c', 'e'] and len(eng) == 0
    eng.add('d', ask('q'), KW)  # 오류 뒤에는 생성기를 새로 만든다
    assert calls['made'] == 2


def test_batch_engine_max_concurrent_sizes_generator(monkeypatch):
    import lawbot_model_server.chat as chat_mod

    seen = {}

    class Fake:
        def __init__(self, model, **kw):
            seen.update(kw)

    monkeypatch.setattr(chat_mod, 'BatchGenerator', Fake)
    eng = BatchEngine(chat_model('MODEL', CharTok(eos=(5, 9))), 3)
    eng._generator()
    assert seen['completion_batch_size'] == 3 and seen['prefill_batch_size'] == 3
    assert sorted(seen['stop_tokens']) == [[5], [9]]
