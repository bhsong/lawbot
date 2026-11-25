"""Safe process contract between the calendar publisher and its callers."""

import json
import re
from subprocess import CompletedProcess

DEPLOYMENT_ID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
PUBLISH_DEADLINE = 480
PUBLISH_CALLER_TIMEOUT = 540
FAILURES = frozenset(
    {
        'cleanup_failed',
        'cleanup_timeout',
        'export_failed',
        'input_mismatch',
        'invalid_arguments',
        'invalid_environment',
        'invalid_pages_config',
        'invalid_pages_credentials',
        'invalid_paths',
        'missing_inputs',
        'production_mismatch',
        'project_mismatch',
        'publish_io',
        'publish_timeout',
        'upload_failed',
        'upload_receipt_invalid',
        'upload_timeout',
        'verify_failed',
        'wrangler_unavailable',
    }
)


def read_report(proc: CompletedProcess[str]) -> dict:
    failed = {'status': 'failed', 'error': 'publish_failed'}
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return failed
    if not isinstance(payload, dict):
        return failed
    identifier = payload.get('deploymentId')
    if identifier is not None and (not isinstance(identifier, str) or not DEPLOYMENT_ID.fullmatch(identifier)):
        return failed
    files, deleted = payload.get('files'), payload.get('deleted')
    if type(files) is not int or files < 0 or type(deleted) is not int or deleted < 0:
        return failed
    counts = {'deploymentId': identifier, 'files': files, 'deleted': deleted}
    if proc.returncode != 0:
        error = payload.get('error')
        if payload.get('status') == 'failed' and isinstance(error, str) and error in FAILURES:
            return counts | {'status': 'failed', 'error': error}
        return failed | counts
    if payload.get('status') == 'ok' and identifier is not None:
        return {'status': 'ok', **counts}
    if payload.get('status') == 'skipped' and payload.get('reason') == 'pages_not_configured' and identifier is None:
        return {'status': 'skipped', 'reason': 'pages_not_configured', **counts}
    return failed
