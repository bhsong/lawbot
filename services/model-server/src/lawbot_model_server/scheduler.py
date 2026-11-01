# lawbot_model_server/scheduler.py  →  GPU 스레드 하나가 모든 모델 연산을 맡는다 (MLX·MPS를 여러 스레드에서 동시에 돌리지 않음)
# HTTP 핸들러는 call()·chat()으로 작업을 넣고 Ticket(asyncio 큐)으로 결과를 받는다: ('item', x)* → ('end', None) / ('error', e)
# 루프 한 바퀴: 수신함 비우기 → embeddings·rerank 같은 한 번짜리 작업 실행 → 자리가 있으면 chat 요청을 엔진에 넣기
#               → 생성 한 스텝 → 끊긴 요청 빼기
# 동시 생성 상한은 max_concurrent. 다른 chat 모델 요청이 맨 앞에 오면 진행 중 생성이 끝난 뒤 모델을 바꾼다 (수신 순서 유지)
import asyncio
import queue
import threading
from collections import deque

MAX_CONCURRENT_LIMIT = 16


class ModelLoadError(RuntimeError):
    """chat 모델을 불러오지 못함 (폴더 없음·형식 오류). 원래 예외는 __cause__ (api.py가 예외 이름을 code로 보냄)"""


class Ticket:
    """요청 하나의 결과 통로. GPU 스레드가 put, 이벤트 루프가 next. cancel() 뒤에는 GPU 스레드가 다음 바퀴에 버린다"""

    def __init__(self):
        self.loop, self.q = asyncio.get_running_loop(), asyncio.Queue()
        self.cancelled = threading.Event()

    def put(self, kind, value=None):
        try:
            self.loop.call_soon_threadsafe(self.q.put_nowait, (kind, value))
        except RuntimeError:  # 이벤트 루프가 이미 닫힘 (서버 종료·요청 끝)
            pass

    async def next(self):
        return await self.q.get()

    def cancel(self):
        self.cancelled.set()


def default_engine(model, max_concurrent):
    """모델이 만드는 생성 엔진 (ChatModel은 BatchEngine, 테스트의 가짜 모델은 FakeEngine)"""
    return model.engine(max_concurrent)


class Scheduler:
    def __init__(self, registry, max_concurrent=1, make_engine=default_engine):
        if not 1 <= max_concurrent <= MAX_CONCURRENT_LIMIT:
            raise ValueError(f'max_concurrent는 1–{MAX_CONCURRENT_LIMIT}: {max_concurrent}')
        self.registry, self.max_concurrent, self.make_engine = registry, max_concurrent, make_engine
        self.inbox = queue.Queue()
        self.calls, self.waiting = deque(), deque()  # GPU 스레드만 만짐
        self.engine, self.engine_model = None, None
        self.thread, self.start_lock = None, threading.Lock()
        self._counts = (0, 0, 0)  # (한 번짜리 작업, 대기 chat, 생성 중) - 다른 스레드에서 읽기용

    # ---- 이벤트 루프 쪽 ----
    def call(self, fn):
        """fn()을 GPU 스레드에서 실행. 결과는 ('item', 값) → ('end', None)"""
        t = Ticket()
        self._submit(('call', t, fn))
        return t

    def chat(self, model_id, messages, kw):
        """생성 요청. 토큰마다 ('item', 응답), 끝나면 ('end', None)"""
        t = Ticket()
        self._submit(('chat', t, (model_id, messages, kw)))
        return t

    def pending(self):
        """아직 끝나지 않은 작업 수 (대략값: 테스트·로그용)"""
        return sum(self._counts) + self.inbox.qsize()

    def _submit(self, job):
        with self.start_lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name='gpu', daemon=True)
                self.thread.start()
        self.inbox.put(job)

    # ---- GPU 스레드 ----
    def _busy(self):
        return bool(self.calls or self.waiting or (self.engine is not None and len(self.engine)))

    def _receive(self):
        """일이 없으면 새 작업이 올 때까지 기다리고, 있으면 쌓인 것만 가져온다"""
        block = not self._busy()
        while True:
            try:
                kind, t, payload = self.inbox.get(block=block)
            except queue.Empty:
                return
            (self.calls if kind == 'call' else self.waiting).append((t, payload))
            block = False

    def _run_calls(self):
        while self.calls:
            t, fn = self.calls.popleft()
            if t.cancelled.is_set():
                continue
            try:
                value = fn()
            except Exception as e:
                t.put('error', e)
                continue
            t.put('item', value)
            t.put('end')

    def _close_engine(self):
        """닫다가 오류가 나도 엔진은 버린다 (GPU 스레드가 죽으면 모든 요청이 영원히 기다림)"""
        engine, self.engine, self.engine_model = self.engine, None, None
        if engine is not None:
            try:
                engine.close()
            except Exception:
                pass

    def _admit(self):
        while self.waiting:
            t, (model_id, messages, kw) = self.waiting[0]
            if t.cancelled.is_set():
                self.waiting.popleft()
                continue
            if self.engine is not None and self.engine_model != model_id:
                if len(self.engine):  # 다른 모델이 생성 중: 끝날 때까지 뒤 요청도 기다림 (수신 순서)
                    return
                self._close_engine()
            if self.engine is not None and len(self.engine) >= self.max_concurrent:
                return
            self.waiting.popleft()
            if self.engine is None:
                try:
                    model, _ = self.registry.get(model_id, 'chat')  # 처음 쓰는 모델은 여기서 불러옴 (LRU 교체)
                except Exception as e:
                    err = ModelLoadError(str(e))
                    err.__cause__ = e
                    t.put('error', err)
                    continue
                try:
                    self.engine = self.make_engine(model, self.max_concurrent)
                except Exception as e:
                    t.put('error', e)
                    continue
                self.engine_model = model_id
            try:
                self.engine.add(t, messages, kw)
            except Exception as e:
                t.put('error', e)

    def _step(self):
        if self.engine is None or not len(self.engine):
            return
        for t, kind, value in self.engine.step():
            t.put(kind, value)

    def _drop_cancelled(self):
        if self.engine is None:
            return
        for t in self.engine.tickets():
            if t.cancelled.is_set():
                self.engine.remove(t)

    def _fail_engine(self, e):
        """엔진 자체의 예상 못 한 오류: 진행 중 요청을 모두 실패로 알리고 엔진을 버림"""
        for t in self.engine.tickets() if self.engine is not None else []:
            t.put('error', e)
        self._close_engine()

    def _run(self):
        while True:
            try:
                self._receive()
                self._run_calls()
                self._drop_cancelled()
                self._admit()
                self._step()
            except Exception as e:  # 어떤 오류에도 스레드는 산다
                self._fail_engine(e)
            self._counts = (len(self.calls), len(self.waiting), len(self.engine) if self.engine is not None else 0)
