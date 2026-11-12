"""Public feed: approved latest versions only, without local paths or reviewer names."""

import json
import os
import sqlite3
import tempfile
from pathlib import Path

from erp_updates import store

SCHEMA_VERSION = 1


def build(conn: sqlite3.Connection) -> dict:
    items = []
    for row in store.rows(conn):
        version, data = row['version'], row['data']
        if version['state'] != 'approved':
            continue
        attachments = [
            {
                'title': entry['title'],
                'pdfUrl': entry['pdfUrl'],
                'sha256': row['files'].get(entry['pdfUrl'], {}).get('sha256'),
                'bytes': row['files'].get(entry['pdfUrl'], {}).get('bytes'),
            }
            for entry in data['attachments']
        ]
        items.append(
            {
                **data,
                'id': row['id'],
                'attachments': attachments,
                'contentSha256': version['content_sha256'],
                'collectedAt': version['collected_at'],
                'reviewedAt': version['reviewed_at'],
            }
        )
    titles = {item['id']: item['title'] for item in items}
    history = [
        {
            'itemId': entry['item_id'],
            'sourceKey': entry['source_key'],
            'title': titles[entry['item_id']],
            'at': entry['at'],
            'action': entry['action'],
            'origin': entry['origin'],
        }
        for entry in store.history(conn, list(titles))
    ]
    related = sorted({item['lawName'] for item in items if item['kind'] in ('form', 'article')})
    return {
        'schemaVersion': SCHEMA_VERSION,
        'generatedAt': store.now(),
        'items': items,
        'relatedLaws': related,
        'history': history,
    }


def write(feed: dict, out: Path) -> None:
    """Replace `out` atomically so the calendar never reads a partial file."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        'w', encoding='utf-8', dir=out.parent, prefix=f'.{out.name}.', delete=False
    ) as tmp:
        json.dump(feed, tmp, ensure_ascii=False, indent=2)
        tmp.write('\n')
    os.replace(tmp.name, out)
