# lawbot_model_server/server.py  →  uv run lawbot-model-server
# 127.0.0.1:<LAWBOT_MODEL_SERVER_PORT>에 먼저 열고(/health ready:false), 모델을 다 불러오면 요청을 받는다
# 불러오기 실패는 종료 코드 1 (launchd KeepAlive가 ThrottleInterval 간격으로 다시 띄움)
import threading
import time

import uvicorn

from lawbot_model_server import api, config, envfile
from lawbot_model_server.registry import Registry


def build_server(app, port):
    """127.0.0.1 고정 (port 0이면 빈 포트). uvicorn 접근 로그 대신 api.Guard가 한 줄씩 (질문 내용 없음)"""
    return uvicorn.Server(
        uvicorn.Config(app, host=config.HOST, port=port, log_level='warning', access_log=False, lifespan='off')
    )


def load_models(app, cfg, registry_cls=Registry):
    """embedding·rerank 전부 + preload chat 모델. 성공하면 app.state.registry에 넣는다"""
    t0 = time.perf_counter()
    reg = registry_cls(cfg.models, max_resident=cfg.max_resident)
    reg.load_fixed()
    reg.preload(cfg.preload)
    app.state.registry = reg
    print(
        f'[model-server] ready · 불러오기 {time.perf_counter() - t0:.1f}초 · '
        + ', '.join(f'{m["id"]}({m["type"]}{", 상주" if m["loaded"] else ""})' for m in reg.listing()),
        flush=True,
    )


def main():
    envfile.load()
    cfg = config.load()  # 포트·토큰·모델 목록이 없으면 모델을 올리기 전에 실행 거부
    app = api.create_app(cfg.token)
    server = build_server(app, cfg.port)
    failed = []

    def loader():
        try:
            load_models(app, cfg)
        except Exception as e:
            print(f'[model-server] 모델 불러오기 실패: {type(e).__name__}: {e}', flush=True)
            failed.append(e)
            server.should_exit = True

    threading.Thread(target=loader, daemon=True).start()
    print(
        f'[model-server] http://{config.HOST}:{cfg.port} 대기 중 (모델 불러오는 동안 /health ready:false)', flush=True
    )
    server.run()
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
