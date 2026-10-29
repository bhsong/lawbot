# src/lawbot/model_server.py  →  uv run lawbot-model-server
# 임베딩·리랭커·답변 모델을 한 번만 올려 운영·개발 웹앱이 함께 쓰는 내부 모델 서버 (docs/plans/model-server.md)
# 127.0.0.1에만 열고(포트는 .env의 LAWBOT_MODEL_SERVER_PORT, 기본값 없음), 모든 요청에 토큰(LAWBOT_MODEL_TOKEN)이 필요하다
# 모델은 service.load_app(local=True)로 올린다: 로딩·검증·LRU는 웹앱 로컬 모드와 같은 코드
# 모델 연산은 전역 락으로 한 번에 하나만 (혼자 쓰는 전제, MLX·MPS 동시 실행을 피함). 질문 내용은 로그에 남기지 않는다
# 이 모듈은 gradio를 import하지 않는다 (tests/test_model_server.py가 검사)
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from lawbot import envfile, service
from lawbot.security import SERVER_NAME, model_server_port, model_token

API_VERSION = 1                      # 요청·응답 형식을 바꾸면 올린다 (remote.py가 다르면 시작을 거부)
MAX_BODY = 2 * 1024 * 1024           # 요청 본문 최대 크기 (rank_all 질문 2,075개가 들어감)
TOKEN_MIN = 16
GEN_FIELDS = ('text', 'generation_tps', 'prompt_tokens', 'prompt_tps', 'generation_tokens', 'peak_memory',
              'finish_reason')       # service.ask가 읽는 응답 속성

ROUTES = {('GET', '/info'), ('GET', '/articles'), ('POST', '/search'), ('POST', '/rank_all'), ('POST', '/rerank'),
          ('POST', '/load'), ('POST', '/generate')}   # /health는 따로 (로딩 중에도 응답)

class BadRequest(ValueError):
    """입력 오류 (400)"""

def plain(o):
    """numpy 값을 JSON용 파이썬 기본 타입으로"""
    if hasattr(o, 'tolist'):         # np.ndarray, np.float32·int64·str_ 등
        return o.tolist()
    raise TypeError(f'JSON으로 바꿀 수 없는 값: {type(o).__name__}')

def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, default=plain).encode('utf-8')

def field(body, name, kind, default=...):
    value = body.get(name, default)
    if value is ...:
        raise BadRequest(f'필드 없음: {name}')
    if not isinstance(value, kind) or isinstance(value, bool) and kind is int:
        raise BadRequest(f'필드 형식 오류: {name}')
    return value

def positive(body, name, default=...):
    n = field(body, name, int, default)
    if n < 1:
        raise BadRequest(f'{name}는 1 이상')
    return n

def str_list(body, name):
    values = field(body, name, list)
    if not all(isinstance(v, str) for v in values):
        raise BadRequest(f'필드 형식 오류: {name}')
    return values

def dict_list(body, name):
    values = field(body, name, list)
    if not all(isinstance(v, dict) for v in values):
        raise BadRequest(f'필드 형식 오류: {name}')
    return values

def error_base(e):
    """웹앱이 같은 계열 예외로 올릴 수 있게 OSError·ValueError 여부를 함께 보낸다 (app.py의 로딩 실패 안내)"""
    return 'OSError' if isinstance(e, OSError) else 'ValueError' if isinstance(e, ValueError) else None

def mlx_memory():
    """MLX active/peak/cache (GB). MLX가 없거나 잴 수 없으면 None (mlx 0.32: mx.get_*_memory)"""
    try:
        import mlx.core as mx
        return {name: round(getattr(mx, f'get_{name}_memory')() / 1e9, 2) for name in ('active', 'peak', 'cache')}
    except Exception:
        return None

def info(app):
    rr = app.reranker
    return {'api_version': API_VERSION,
            'profile': {'name': app.profile.name, 'doc_style': app.profile.doc_style},
            'models': list(app.models.specs), 'loaded': list(app.models.loaded),
            'max_resident': app.models.max_resident,
            'reranker': {'candidates': rr.candidates} if rr else None,
            'n_articles': len(app.retriever.ids), 'memory': mlx_memory()}

def articles(app):
    R = app.retriever
    laws = getattr(R, 'laws', None)
    return {'ids': list(R.ids), 'titles': list(R.titles), 'docs': list(R.docs),
            'laws': None if laws is None else list(laws)}

def model_key(app, body):
    key = field(body, 'model', str)
    if key not in app.models.specs:
        raise BadRequest(f'없는 모델: {key!r} (서버 모델 {list(app.models.specs)})')
    return key

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'    # 응답마다 연결을 닫는다 (스트림 끝 = 연결 종료)
    server_version = 'lawbot-model-server'
    sys_version = ''

    def log_message(self, format, *args):   # 기본 접근 로그 대신 handle_one에서 한 줄 (질문 내용 없음)
        pass

    def send_json(self, status, obj):
        data = dumps(obj)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, status, kind, message, base=None):
        self.send_json(status, {'error': {'type': kind, 'message': message, 'base': base}})

    def authorized(self):
        got = (self.headers.get('Authorization') or '').encode('utf-8')
        return hmac.compare_digest(got, f'Bearer {self.server.token}'.encode('utf-8'))

    def read_body(self):
        """JSON 객체. 크기 초과는 None(413을 이미 보냄), 형식 오류는 BadRequest"""
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            raise BadRequest('Content-Length 형식 오류') from None
        if n < 0:                        # 음수면 rfile.read가 연결이 끊길 때까지 기다림
            raise BadRequest('Content-Length 형식 오류')
        if n > MAX_BODY:
            self.send_error_json(413, 'PayloadTooLarge', f'요청 본문이 {MAX_BODY}바이트를 넘습니다')
            return None
        try:
            body = json.loads(self.rfile.read(n) or b'null')
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BadRequest('JSON 형식 오류') from None
        if not isinstance(body, dict):
            raise BadRequest('요청 본문은 JSON 객체')
        return body

    def do_GET(self):
        self.handle_one('GET')

    def do_POST(self):
        self.handle_one('POST')

    def handle_one(self, method):
        t0, self.model, status = time.perf_counter(), None, 200
        try:
            status = self.route(method)
        except BadRequest as e:
            status = 400
            self.send_error_json(400, 'BadRequest', str(e))
        except (BrokenPipeError, ConnectionResetError):
            status = 499                 # 응답 중 클라이언트가 끊음
        except Exception as e:           # 모델 로딩·연산 오류: 타입을 함께 보내 웹앱이 같은 안내를 하게 함
            status = 500
            try:
                self.send_error_json(500, type(e).__name__, str(e), error_base(e))
            except OSError:
                pass
        finally:
            if self.path != '/health':
                print(f'[model-server] {method} {self.path} {self.model or "-"} {status} '
                      f'{time.perf_counter() - t0:.2f}s', flush=True)

    def route(self, method):
        if not self.authorized():        # 토큰 값은 응답·로그에 남기지 않음
            self.send_error_json(401, 'Unauthorized', '모델 서버 토큰이 맞지 않습니다 (LAWBOT_MODEL_TOKEN)')
            return 401
        app = self.server.lawbot
        if method == 'GET' and self.path == '/health':
            self.send_json(200, {'ready': app is not None})
            return 200
        if (method, self.path) not in ROUTES:
            self.send_error_json(404, 'NotFound', f'{method} {self.path}')
            return 404
        body = self.read_body() if method == 'POST' else {}
        if body is None:
            return 413
        if app is None:
            self.send_error_json(503, 'NotReady', '모델을 불러오는 중입니다')
            return 503
        if self.path == '/info':
            self.send_json(200, info(app))
            return 200
        if self.path == '/articles':
            self.send_json(200, articles(app))
            return 200
        if self.path == '/generate':
            return self.generate(app, body)
        with self.server.model_lock:     # 모델 연산은 한 번에 하나
            out = self.compute(app, body)
        self.send_json(200, out)
        return 200

    def compute(self, app, body):
        if self.path == '/search':
            return {'hits': app.retriever.search(field(body, 'question', str), k=positive(body, 'k'))}
        if self.path == '/rank_all':
            return {'rank': app.retriever.rank_all(str_list(body, 'questions'))}
        if self.path == '/rerank':
            if app.reranker is None:
                raise BadRequest('모델 서버의 리랭커가 꺼져 있습니다')
            return {'hits': app.reranker.rerank(field(body, 'question', str), dict_list(body, 'candidates'),
                                                top_k=positive(body, 'top_k'))}
        self.model = model_key(app, body)        # /load
        _, load_s = app.models.get(self.model)
        return {'load_s': load_s}

    def generate(self, app, body):
        """NDJSON 스트림: 토큰마다 한 줄. 생성 중 오류는 마지막 줄 {"error": …}
        클라이언트가 끊으면(조기 종료·정지 버튼) 다음 쓰기에서 멈추고 락을 푼다"""
        self.model = model_key(app, body)
        question, hits = field(body, 'question', str), dict_list(body, 'hits')
        system = field(body, 'system', str)
        if not system.strip():           # 웹앱이 항상 보낸다 (서버의 기본 system을 쓰지 않음)
            raise BadRequest('system이 비어 있습니다')
        max_tokens = positive(body, 'max_tokens', 1024)
        with self.server.model_lock:
            llm, _ = app.models.get(self.model)
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
            self.end_headers()
            gen = llm.stream(question, hits, max_tokens=max_tokens, system=system)
            try:
                for r in gen:
                    self.wfile.write(dumps({k: getattr(r, k, None) for k in GEN_FIELDS}) + b'\n')
            except (BrokenPipeError, ConnectionResetError):
                return 499
            except Exception as e:
                self.wfile.write(dumps({'error': {'type': type(e).__name__, 'message': str(e),
                                                  'base': error_base(e)}}) + b'\n')
                return 500
            finally:
                gen.close()              # 생성기를 닫아 생성을 멈춤 (끊긴 경우)
        return 200

def create_server(app, port, token):
    """127.0.0.1:port에 연 서버 (port 0이면 빈 포트). app이 None이면 /health가 ready:false (로딩 중)
    로딩이 끝나면 server.lawbot에 넣는다"""
    if len(token or '') < TOKEN_MIN:
        raise RuntimeError(f'LAWBOT_MODEL_TOKEN이 없거나 {TOKEN_MIN}자 미만이라 실행을 거부합니다')
    server = ThreadingHTTPServer((SERVER_NAME, port), Handler)
    server.daemon_threads = True
    server.lawbot, server.token, server.model_lock = app, token, threading.Lock()
    return server

def main():
    envfile.load()                       # 웹앱과 같은 .env를 읽어도 모델 서버는 항상 로컬 모드 (load_app(local=True))
    token = model_token(TOKEN_MIN)       # 토큰·포트가 없으면 모델 로딩 전에 실행 거부
    port = model_server_port()
    server = create_server(None, port, token)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f'[model-server] http://{SERVER_NAME}:{port} 대기 중 (모델 불러오는 동안 /health ready:false)', flush=True)
    t0 = time.perf_counter()
    try:
        server.lawbot = service.load_app(local=True)
    except Exception as e:               # launchd KeepAlive가 다시 띄움 (ThrottleInterval 간격)
        print(f'[model-server] 모델 불러오기 실패: {type(e).__name__}: {e}', flush=True)
        server.shutdown()
        server.server_close()
        raise SystemExit(1) from e
    print(f'[model-server] ready · 불러오기 {time.perf_counter() - t0:.1f}초 · {info(server.lawbot)}', flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()

if __name__ == '__main__':
    main()
