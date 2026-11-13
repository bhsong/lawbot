"""Offline bridge that captures the same validated payloads as the public API."""

import argparse
from datetime import datetime
from pathlib import Path
from typing import get_args

from law_calendar import news
from law_calendar.server import erp_updates
from lawbot_common.erp_daily import replace_file
from lawbot_common.erp_news import Agency
from lawbot_common.erp_rules import RULE_FEED_MAX_BYTES, RuleSnapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('database', 'erp', 'rules', 'news', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--cutoff', required=True, type=datetime.fromisoformat)
    parser.add_argument('--without-law-news', action='store_true')
    args = parser.parse_args()
    if args.cutoff.utcoffset() is None:
        parser.error('Use a timezone-aware snapshot cutoff')
    with args.rules.open('rb') as stream:
        data = stream.read(RULE_FEED_MAX_BYTES + 1)
    if len(data) > RULE_FEED_MAX_BYTES:
        parser.error('ERP rule snapshot exceeds the size limit')
    rules = RuleSnapshot.model_validate_json(data).model_dump(mode='json')
    payload = {
        'erp': erp_updates(args.erp),
        'rules': rules,
        'news': {
            agency: news.response(
                args.database, args.news, agency, 10, now=args.cutoff, use_law_feed=not args.without_law_news
            )
            for agency in get_args(Agency)
        },
    }
    if payload['erp']['state'] != 'ok' or any(
        entry['state'] not in ('ok', 'link_only', 'unavailable') for entry in payload['news'].values()
    ):
        parser.error('ERP public input is unavailable')
    replace_file(args.out, payload)


if __name__ == '__main__':
    main()
