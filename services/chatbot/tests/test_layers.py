# tests/test_layers.py  →  패키지 의존 방향: 웹앱 런타임(lawbot)은 평가 도구(lawbot_tools)를 import하지 않는다
import ast
import subprocess
import sys

from lawbot.paths import ROOT

RUNTIME = (
    'web view settings service api_client llm retriever reranker decree deploy security citation answer_format '
    'feedback manifest envfile paths check_deploy'
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


def test_db_does_not_import_web_or_tools():
    code = """
import importlib
import pkgutil
import sys
import lawbot.db
for module in pkgutil.walk_packages(lawbot.db.__path__, 'lawbot.db.'):
    importlib.import_module(module.name)
print(sorted(m for m in sys.modules if m.split('.')[0] in {'fastapi', 'lawbot_tools'}))
"""
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == '[]'


def test_sqlite_imports_are_confined_to_db_and_stores():
    runtime = ROOT / 'services/chatbot/src/lawbot'
    tools = ROOT / 'tools/lawbot-tools/src/lawbot_tools'
    violations = []
    for root in (runtime, tools):
        for path in root.rglob('*.py'):
            if root == runtime and (path.is_relative_to(runtime / 'db') or path.stem.endswith('_store')):
                continue
            for node in ast.walk(ast.parse(path.read_bytes())):
                if isinstance(node, ast.Import):
                    modules = [name.name for name in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    modules = [node.module or '']
                else:
                    continue
                if any(module.split('.')[0] == 'sqlite3' for module in modules):
                    violations.append(f'{path.relative_to(ROOT)}:{node.lineno}')
    assert not violations
