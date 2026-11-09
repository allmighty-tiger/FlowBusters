"""Backend-authenticated preflight decisions, distinct from HTTP receipts."""
import hashlib
import hmac
import json
from pathlib import Path


def save_rejections(run, flow, root, errors):
    from backend.runtime.probe_executor import canonical, key_for
    run = Path(run)
    rows = []
    for script, error in errors.items():
        path = Path(script)
        rows.append({'script': path.name, 'relative_path': path.relative_to(run).as_posix(),
                     'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'error': error,
                     'status': 'CHECK_ERROR', 'execution_status': 'NOT_EXECUTED'})
    data = {'schema_version': 1, 'run_id': run.name, 'flow': flow, 'rejections': rows}
    data['signature'] = hmac.new(key_for(root, True), canonical(data), hashlib.sha256).hexdigest()
    path = run / 'preflight_rejections.json'
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2)


def load_rejections(run, root):
    from backend.runtime.probe_executor import canonical, key_for
    run = Path(run).resolve(); path = run / 'preflight_rejections.json'
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding='utf-8')); signature = data.pop('signature', '')
    if (data.get('run_id') != run.name or data.get('schema_version') != 1 or
        not hmac.compare_digest(signature, hmac.new(key_for(root), canonical(data), hashlib.sha256).hexdigest())):
        raise ValueError('Preflight rejection authentication failed')
    for row in data['rejections']:
        script = (run / row['relative_path']).resolve()
        if not script.is_relative_to(run) or hashlib.sha256(script.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Rejected probe changed after preflight')
    return data['rejections']


def assessment_status(summary):
    planned = int(summary.get('planned_executions', 0))
    executed = int(summary.get('execution_attempts', 0))
    skipped = int(summary.get('preflight_rejected', 0))
    pending = max(0, planned - executed - skipped)
    failed = int(summary.get('execution_errors', 0)) + skipped
    status = 'FAILED' if pending or not planned else 'COMPLETED_WITH_ERRORS' if failed else 'COMPLETED'
    return {'status': status, 'planned': planned, 'executed': executed,
            'skipped': skipped, 'failed': failed, 'pending': pending}


def status_message(status):
    return (f"Assessment {status['status']}: planned {status['planned']}, executed {status['executed']}, "
            f"skipped {status['skipped']}, failed {status['failed']}, pending {status['pending']}")
