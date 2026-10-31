# src/lawbot/feedback_ui.py  →  피드백 깃발의 터치 기기 보정 (app.main이 demo.launch(css=…, js=…)로 넘김)
# Gradio 6.30.0 기본: 깃발에 마우스를 올릴 때만(.extra-feedback:hover) 이유 목록이 보여 터치로는 고를 수 없다
# 마우스 기기는 Gradio 기본 동작 그대로. 터치 기기(hover: none)에서만 깃발을 탭하면 목록을 버튼 줄 아래 한 줄로 연다
# 주의: Gradio 내부 이름(.extra-feedback, .extra-feedback-options, aria-label="Feedback")에 기댄다.
#       gradio를 올리면 맥 브라우저·휴대폰에서 화면을 다시 확인할 것 (깨져도 마우스 기기는 기본 동작으로 쓸 수 있음)
# - Svelte가 다시 그릴 때 class를 덮어쓰므로 열림 상태는 목록 요소의 inline style로 둔다
# - launch(css=…) 안의 @media는 적용되지 않아 터치 구분은 <html>의 lawbot-touch class로 한다
# - 터치에서는 click이 오지 않아 pointerdown(캡처 단계)을 쓴다

CSS = """
html.lawbot-touch .extra-feedback-options {
  position: static !important;
  flex-direction: row !important;
  flex-wrap: wrap;
  gap: 4px;
}
"""

JS = """
() => {
  if (!window.matchMedia('(hover: none)').matches) return;
  document.documentElement.classList.add('lawbot-touch');
  const OPTIONS = '.extra-feedback-options';
  const closeAll = (keep) => document.querySelectorAll(OPTIONS).forEach((o) => { if (o !== keep) o.style.display = ''; });
  document.addEventListener('pointerdown', (e) => {
    const t = e.target instanceof Element ? e.target : null;
    if (!t) return;
    const flag = t.closest('[aria-label="Feedback"]');
    if (flag) {
      const box = flag.closest('.extra-feedback');
      const opts = box && box.querySelector(OPTIONS);
      if (!opts) return;
      const open = opts.style.display === 'flex';
      closeAll(opts);
      opts.style.display = open ? '' : 'flex';
      return;
    }
    if (!t.closest(OPTIONS)) closeAll(null);
  }, true);
}
"""

def launch_options(fb):
    """demo.launch에 더할 인자. 피드백이 꺼져 있으면(fb=None) 아무것도 넣지 않는다"""
    return {'css': CSS, 'js': JS} if fb is not None else {}
