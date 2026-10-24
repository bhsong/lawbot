# src/lawbot/check_deploy.py  →  uv run python -m lawbot.check_deploy [v1|v4]
# 웹앱이 쓸 조문 CSV·system·임베딩·MLX 모델 폴더를 모델을 올리지 않고 검사한다 (파일 헤더만 읽음)
# 실패 항목이 있으면 종료 코드 1. LoRA 병합 여부는 파일로 확정할 수 없어 '어댑터 파일 없음'까지만 본다
import hashlib
import json
import struct
import sys
from collections import Counter
from pathlib import Path
import pandas as pd
from lawbot.deploy import get_profile

EXPECTED_ARTICLES = {'v1': 130, 'v4': 415}
COLUMNS = {'v1': {'article_id', 'title', 'deleted', 'text'}, 'v4': {'law', 'article_id', 'title', 'deleted', 'text'}}
ADAPTER_FILES = ('adapters.safetensors', 'adapter_config.json', 'adapter_model.safetensors')
HIDDEN_SIZE = {2560: 'Qwen3-4B', 5120: 'Qwen3-14B'}       # config.json hidden_size로 크기 확인

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

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

def check_articles(p, out):
    if not p.articles.is_file():
        return out.append(('FAIL', f'조문 CSV 없음: {p.articles}'))
    law = pd.read_csv(p.articles, encoding='utf-8-sig')
    missing = COLUMNS[p.doc_style] - set(law.columns)
    if missing:
        return out.append(('FAIL', f'조문 CSV 열 없음 {sorted(missing)} (있는 열: {list(law.columns)})'))
    live = law[law['deleted'] == 0]
    key = ['law', 'article_id'] if p.doc_style == 'v4' else ['article_id']
    dup = live[live.duplicated(key, keep=False)]
    n_ok = len(live) == EXPECTED_ARTICLES[p.name]
    out.append(('OK' if n_ok else 'FAIL', f'비삭제 조문 {len(live)}개 (기대 {EXPECTED_ARTICLES[p.name]}) · sha256 {sha256(p.articles)[:12]}'))
    if len(dup):
        out.append(('FAIL', f'중복 조문 {len(dup)}행: {dup[key].head().values.tolist()}'))
    nums = live['article_no'] if 'article_no' in live else live['article_id']   # retriever와 같은 규칙
    bad = nums[~nums.astype(str).str.fullmatch(r'제\d+조(?:의\d+)?')]
    if len(bad):             # 법령명이 붙은 번호는 검색 문서·인용 대조에서 법령명이 두 번 들어감
        out.append(('FAIL', f"조문 번호가 '제N조' 형식이 아님 {len(bad)}행: {bad.head().tolist()} "
                            '(법령명이 들어 있으면 번호만 담은 article_no 열 추가)'))
    if p.doc_style == 'v4':
        out.append(('INFO', f"법령별 조문 수: {live['law'].value_counts().to_dict()}"))

def check_system_file(name, f, out):
    if not f.is_file():
        return out.append(('FAIL', f'{name} system 파일 없음: {f} (학습 노트북의 system 원문을 글자 그대로 저장)'))
    text = f.read_text(encoding='utf-8').strip()
    out.append(('OK', f'{name} system {len(text)}자 · sha256 {hashlib.sha256(text.encode()).hexdigest()[:12]} · '
                      f'첫 줄: {text.splitlines()[0][:60]}'))

def check_system(p, out):
    if p.system_file is None:
        from lawbot.llm import SYSTEM   # v1: 코드에 든 학습 system
        out.append(('OK', f'기본 system: llm.SYSTEM (v1) · sha256 {hashlib.sha256(SYSTEM.encode()).hexdigest()[:12]}'))
    else:
        check_system_file('기본', p.system_file, out)
    for spec in p.models:               # 모델별 system (Pro 간결형)
        if spec.system_file:
            check_system_file(spec.label, spec.system_file, out)
        out.append(('INFO', f"{spec.label}: 기본 답변 형식 {spec.fmt} · system "
                            f"{spec.system_file.name if spec.system_file else '기본'}"))

def check_emb(p, out):
    d = p.emb
    if not d.is_dir():
        return out.append(('FAIL', f'임베딩 폴더 없음: {d}'))
    dtypes = safetensors_dtypes(d)
    out.append(('OK' if dtypes.get('F16') else 'WARN', f'임베딩 {d.name}: dtype {dtypes} · {folder_gb(d):.2f}GB '
                '(앱은 fp16으로 올림. F32·BF16이면 노트북과 같은 정밀도인지 확인)'))
    st = d / 'config_sentence_transformers.json'
    prompts = json.loads(st.read_text())['prompts'] if st.is_file() else None
    out.append(('OK' if prompts and 'query' in prompts else 'FAIL', f"query 프롬프트: {(prompts or {}).get('query')!r} (노트북 검색 때와 같아야 함)"))
    for f in ('modules.json', 'tokenizer.json'):
        if not (d / f).is_file():
            out.append(('FAIL', f'임베딩 폴더에 {f} 없음'))
    out.append(('INFO', f'조문 인코딩 max_seq_length {p.max_seq} · 문서 형식 {p.doc_style}'))

def check_llm(spec, out):
    d = spec.path
    if not d.is_dir():
        return out.append(('FAIL', f'{spec.label}: 모델 폴더 없음 {d} (준비 전이면 이 모델만 선택 불가로 표시됨)'))
    cfg = json.loads((d / 'config.json').read_text())
    q = cfg.get('quantization') or cfg.get('quantization_config') or {}
    size = HIDDEN_SIZE.get(cfg.get('hidden_size'), f"hidden_size {cfg.get('hidden_size')}")
    out.append(('OK' if q.get('bits') == 4 else 'FAIL',
                f"{spec.label}: {cfg.get('model_type')} {size} · 양자화 {q or '없음'} · {folder_gb(d):.2f}GB"))
    adapters = [f for f in ADAPTER_FILES if (d / f).exists()]
    out.append(('FAIL' if adapters else 'OK', f'{spec.label}: 어댑터 파일 ' + (f'남아 있음 {adapters} (병합 전 폴더)' if adapters
                                                                         else '없음 (병합 산출물로 보임, 출력 대조로 확인)')))
    tc = d / 'tokenizer_config.json'
    has_template = (d / 'chat_template.jinja').is_file() or (tc.is_file() and 'chat_template' in json.loads(tc.read_text()))
    out.append(('OK' if (d / 'tokenizer.json').is_file() and has_template else 'FAIL',
                f'{spec.label}: tokenizer.json·chat template ' + ('있음' if has_template else '없음')))

def run(name=None):
    p = get_profile(name)
    out = [('INFO', f'프로필 {p.name}: {p.title}')]
    check_articles(p, out)
    check_system(p, out)
    check_emb(p, out)
    for spec in p.models:
        check_llm(spec, out)
    return out

def main():
    out = run(sys.argv[1] if len(sys.argv) > 1 else None)
    for level, msg in out:
        print(f'[{level:4}] {msg}')
    sys.exit(1 if any(level == 'FAIL' for level, _ in out) else 0)

if __name__ == '__main__':
    main()
