"""Keep the law-level scope aligned with the chatbot corpus."""

from pathlib import Path

import pytest

from law_monitor.config import ConfigError, load
from lawbot_common.law_scope import load_law_scope, scope_keywords
from lawbot_tools.build_articles import doc_order, load_scope

ROOT = Path(__file__).resolve().parents[3]


def test_monitor_tracks_exactly_the_chatbot_laws():
    scope = load_law_scope()
    selected = load_scope(ROOT / 'data/v5/scope_tax.csv')
    chatbot = {law for law, kind in doc_order() if kind == '법률'} | {law for law, _ in selected}
    assert {law.name for law in scope} == chatbot

    monitor = load()
    assert {(law.name, law.category) for law in monitor.laws} == {(law.folder, law.category) for law in scope}
    assert all(law.keywords for law in scope)
    assert monitor.prelegislation_keywords == scope_keywords(scope)


@pytest.mark.parametrize('override', ['laws: []', 'prelegislation: {keywords: []}'])
def test_monitor_config_cannot_override_law_scope(tmp_path, override):
    config = tmp_path / 'targets.yaml'
    config.write_text(override, encoding='utf-8')
    with pytest.raises(ConfigError, match=r'law_scope\.json'):
        load(config)
