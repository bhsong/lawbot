# tests/test_layers.py  →  패키지 의존 방향: 웹앱 런타임(lawbot)은 평가 도구(lawbot_tools)를 import하지 않는다
import subprocess
import sys

RUNTIME = (
    'app service api_client llm retriever reranker decree deploy security citation answer_format '
    'feedback feedback_ui manifest envfile paths check_deploy'
).split()


def test_runtime_does_not_import_tools():
    code = (
        'import sys\n'
        + ''.join(f'import lawbot.{m}\n' for m in RUNTIME)
        + "bad = sorted(m for m in sys.modules if m.split('.')[0] == 'lawbot_tools')\n"
        + 'print(bad)\n'
    )
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == '[]'


def test_tool_modules_left_runtime_package():
    import importlib.util

    for m in ('eval_v4', 'score_answers', 'refusal', 'feedback_review', 'feedback_export', 'log_summary'):
        assert importlib.util.find_spec(f'lawbot.{m}') is None and importlib.util.find_spec(f'lawbot_tools.{m}')
