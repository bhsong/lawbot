"""Run the real web server with a synthetic bundle boundary for Chromium tests."""

import sys

from lawbot import cache_carry
from lawbot.cache_seed import Bundle
from lawbot.web import main


def fixture_bundle(path):
    if path.read_text(encoding='utf-8') != 'synthetic P07 bundle':
        raise ValueError('invalid synthetic bundle')
    return Bundle([], frozenset())


if __name__ == '__main__':
    cache_carry.load_bundle = fixture_bundle
    main(sys.argv[1:])
