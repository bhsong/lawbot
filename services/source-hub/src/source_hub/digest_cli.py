"""Private CLI settings and wiring for the ERP Gmail delivery job."""

import json
import os
from datetime import datetime, time

import httpx

from lawbot_common.erp_daily import read_snapshot
from source_hub import digest, digest_model, gmail
from source_hub.daily import daily_lock


def run(args, http: httpx.Client, now: datetime) -> dict:
    if args.settings.is_symlink() or args.settings.stat().st_mode & 0o077:
        raise ValueError('ERP email settings file must be private (0600)')
    try:
        cfg = json.loads(args.settings.read_text(encoding='utf-8'))
        if not isinstance(cfg, dict):
            raise TypeError
        for field in ('enabled', 'send_unchanged', 'model_enabled'):
            if field in cfg and type(cfg[field]) is not bool:
                raise TypeError
        if not args.preview and not cfg.get('enabled', False):
            return {'status': 'disabled'}
        page = cfg['erp_page']
        limit = cfg.get('item_limit', 20)
        if not isinstance(page, str) or type(limit) is not int or not 1 <= limit <= 100:
            raise TypeError
        if not args.preview:
            recipients = cfg['recipients']
            sender = cfg['sender']
            if (
                not isinstance(recipients, dict)
                or not all(isinstance(v, str) for v in recipients.values())
                or not isinstance(sender, str)
            ):
                raise TypeError
            settings = digest.Settings(
                page,
                sender,
                recipients,
                time.fromisoformat(cfg.get('send_at_kst', '07:30')),
                cfg.get('send_unchanged', True),
                limit,
            )
            secrets = [
                os.environ[name]
                for name in ('ERP_GMAIL_CLIENT_ID', 'ERP_GMAIL_CLIENT_SECRET', 'ERP_GMAIL_REFRESH_TOKEN')
            ]
            if not all(secrets):
                raise KeyError
    except KeyError, ValueError, TypeError, AttributeError, IndexError:
        raise ValueError('Check the private ERP email configuration and Gmail credentials') from None
    if args.preview:
        snapshot = read_snapshot(args.snapshot)
        summaries = digest.read_morning(snapshot, args.monitor_home) if args.monitor_home else None
        mail = digest.render(snapshot, page, limit=limit, summaries=summaries)
        return {
            'status': 'preview',
            'snapshot_hash': snapshot['snapshotHash'],
            'subject': mail.subject,
            'text': mail.text,
        }
    if args.outbox.resolve() in (args.database.resolve(), args.settings.resolve()):
        raise ValueError('Use an outbox separate from collector settings and the private configuration file')
    provider = gmail.Gmail(http, gmail.Credentials(*secrets))
    with httpx.Client(timeout=300, trust_env=False, follow_redirects=False) as model_http:
        enrich = None
        if cfg.get('model_enabled', False):
            url = os.environ.get('LAWBOT_MODEL_SERVER')
            token = os.environ.get('LAWBOT_MODEL_TOKEN')
            if url and token:
                try:
                    model = digest_model.Model(model_http, url, token)
                except ValueError:
                    model = None
                if model:
                    enrich = lambda snapshot: model.summarize(snapshot, preflight=True)
        return digest.deliver(
            args.snapshot,
            args.outbox,
            daily_lock(args.database),
            settings,
            now=now,
            send=provider.send,
            monitor_home=args.monitor_home,
            enrich=enrich,
        )
