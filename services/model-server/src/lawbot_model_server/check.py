# lawbot_model_server/check.py  →  uv run python -m lawbot_model_server.check
# .env의 모델 목록·경로를 모델을 올리지 않고 확인한다 (폴더·설정 파일만 봄). FAIL이 있으면 종료 코드 1
from lawbot_model_server import config, envfile

NEEDED = {
    'chat': ('config.json',),
    'rerank': ('config.json',),
    'embedding': ('config.json', 'config_sentence_transformers.json'),
}


def run():
    """[(수준, 메시지)]. 포트·토큰은 값 없이 있는지만"""
    out = []
    for name, fn in (('포트', config.port), ('토큰', config.token)):
        try:
            fn()
            out.append(('OK', f'{name} 설정됨'))
        except (RuntimeError, ValueError) as e:
            out.append(('FAIL', str(e)))
    try:
        entries = config.models()
        resident = config.max_resident()
        preload = config.preload(entries, resident)
    except (RuntimeError, ValueError) as e:
        return [*out, ('FAIL', str(e))]
    for e in entries:
        missing = [f for f in NEEDED[e.kind] if not (e.path / f).is_file()]
        if not e.path.is_dir():
            out.append(('FAIL', f'{e.kind} {e.id}: 폴더 없음 {e.path}'))
        elif missing:
            out.append(('FAIL', f'{e.kind} {e.id}: {", ".join(missing)} 없음 ({e.path})'))
        else:
            out.append(('OK', f'{e.kind} {e.id}: {e.path}'))
    out.append(('INFO', f'chat 상주 최대 {resident}개 · 시작할 때 {list(preload) or "없음"}'))
    return out


def main():
    envfile.load()
    out = run()
    for level, msg in out:
        print(f'{level:4} {msg}')
    raise SystemExit(1 if any(level == 'FAIL' for level, _ in out) else 0)


if __name__ == '__main__':
    main()
