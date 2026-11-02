# lawbot_model_server/check.py  →  uv run python -m lawbot_model_server.check
# .env의 모델 목록·폴더를 모델을 올리지 않고 확인한다 (설정 파일·safetensors 헤더만 읽음). FAIL이 있으면 종료 코드 1
# 챗봇의 이전 check_deploy에 있던 모델 폴더 검사를 옮김. LoRA 병합 여부는 파일로 확정할 수 없어 '어댑터 파일 없음'까지만 본다
import json
import struct
from collections import Counter
from pathlib import Path

from lawbot_model_server import config, envfile

ADAPTER_FILES = ('adapters.safetensors', 'adapter_config.json', 'adapter_model.safetensors')
HIDDEN_SIZE = {2560: 'Qwen3-4B', 5120: 'Qwen3-14B'}  # config.json hidden_size로 크기 확인


def safetensors_dtypes(folder):
    """폴더 안 safetensors 텐서의 dtype별 개수 (헤더만 읽음)"""
    n = Counter()
    for f in sorted(Path(folder).glob('*.safetensors')):
        with f.open('rb') as fh:
            size = struct.unpack('<Q', fh.read(8))[0]
            header = json.loads(fh.read(size))
        n.update(v['dtype'] for k, v in header.items() if k != '__metadata__')
    return dict(n)


def folder_gb(folder):
    return sum(f.stat().st_size for f in Path(folder).rglob('*') if f.is_file()) / 1e9


def read_json(f):
    return json.loads(f.read_text(encoding='utf-8')) if f.is_file() else {}


def mlx_summary(d):
    cfg = read_json(d / 'config.json')
    q = cfg.get('quantization') or cfg.get('quantization_config') or {}
    hidden = cfg.get('hidden_size')
    size = HIDDEN_SIZE.get(hidden, f'hidden_size {hidden}') if isinstance(hidden, int) else f'hidden_size {hidden}'
    return cfg, q, f'{cfg.get("model_type")} {size} · 양자화 {q or "없음"} · {folder_gb(d):.2f}GB'


def check_chat(e, out):
    d, name = e.path, f'chat {e.id}'
    _, q, desc = mlx_summary(d)
    out.append(('OK' if q.get('bits') == 4 else 'WARN', f'{name}: {desc}'))
    adapters = [f for f in ADAPTER_FILES if (d / f).exists()]
    out.append(
        (
            'FAIL' if adapters else 'OK',
            f'{name}: 어댑터 파일 '
            + (f'남아 있음 {adapters} (병합 전 폴더)' if adapters else '없음 (병합 산출물로 보임, 출력 대조로 확인)'),
        )
    )
    tc = d / 'tokenizer_config.json'
    has_template = (d / 'chat_template.jinja').is_file() or 'chat_template' in read_json(tc)
    out.append(
        (
            'OK' if (d / 'tokenizer.json').is_file() and has_template else 'FAIL',
            f'{name}: tokenizer.json·chat template ' + ('있음' if has_template else '없음'),
        )
    )


def check_embedding(e, out):
    d, name = e.path, f'embedding {e.id}'
    dtypes = safetensors_dtypes(d)
    out.append(
        (
            'OK' if dtypes.get('F16') else 'WARN',
            f'{name}: dtype {dtypes} · {folder_gb(d):.2f}GB (fp16으로 올림. F32·BF16이면 노트북과 같은 정밀도인지 확인)',
        )
    )
    prompts = read_json(d / 'config_sentence_transformers.json').get('prompts')
    out.append(
        (
            'OK' if prompts and 'query' in prompts else 'FAIL',
            f'{name}: query 프롬프트 {(prompts or {}).get("query")!r} (노트북 검색 때와 같아야 함)',
        )
    )
    for f in ('modules.json', 'tokenizer.json'):
        if not (d / f).is_file():
            out.append(('FAIL', f'{name}: {f} 없음'))


def check_rerank(e, out):
    d, name = e.path, f'rerank {e.id}'
    cfg, q, desc = mlx_summary(d)
    out.append(('OK' if cfg.get('model_type') == 'qwen3' and q.get('bits') == 4 else 'FAIL', f'{name}: {desc}'))
    vocab = read_json(d / 'tokenizer.json').get('model', {}).get('vocab', {})
    out.append(
        (
            'OK' if {'yes', 'no'} <= set(vocab) else 'FAIL',
            f'{name}: tokenizer.json ' + ('yes/no 토큰 있음' if vocab else '없음 또는 어휘 없음'),
        )
    )
    if not list(d.glob('*.safetensors')):
        out.append(('FAIL', f'{name}: safetensors 없음'))


CHECKS = {'chat': check_chat, 'embedding': check_embedding, 'rerank': check_rerank}
NEEDED = {'chat': 'config.json', 'rerank': 'config.json', 'embedding': 'config_sentence_transformers.json'}


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
        concurrent = config.max_concurrent()
    except (RuntimeError, ValueError) as e:
        return [*out, ('FAIL', str(e))]
    for e in entries:
        if not e.path.is_dir():
            out.append(('FAIL', f'{e.kind} {e.id}: 폴더 없음 {e.path}'))
        elif not (e.path / NEEDED[e.kind]).is_file():
            out.append(('FAIL', f'{e.kind} {e.id}: {NEEDED[e.kind]} 없음 ({e.path})'))
        else:
            out.append(('OK', f'{e.kind} {e.id}: {e.path}'))
            CHECKS[e.kind](e, out)
    out.append(('INFO', f'chat 상주 최대 {resident}개 · 시작할 때 {list(preload) or "없음"}'))
    out.append(('INFO', f'동시 생성 최대 {concurrent}개 (mlx_lm BatchGenerator)'))
    return out


def main():
    envfile.load()
    out = run()
    for level, msg in out:
        print(f'[{level:4}] {msg}')
    raise SystemExit(1 if any(level == 'FAIL' for level, _ in out) else 0)


if __name__ == '__main__':
    main()
