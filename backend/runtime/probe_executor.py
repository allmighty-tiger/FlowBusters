"""Backend-owned, append-only execution receipts and report provenance gate."""
import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def key_for(root, create=False):
    path = Path(root) / 'execution_keys' / 'receipt.key'
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open('xb') as stream:
                stream.write(os.urandom(32))
            path.chmod(0o600)
        except FileExistsError:
            pass
    return path.read_bytes()


def stamp():
    return datetime.now(timezone.utc).isoformat()


async def execute(script, report_dir, *, root, cwd, source='MUTATION_SCRIPT', triggered_by=None, timeout=30):
    script, report_dir, cwd = Path(script).resolve(), Path(report_dir), Path(cwd).resolve()
    code = script.read_bytes()
    digest = hashlib.sha256(code).hexdigest()
    identifier = script.stem + '-' + uuid.uuid4().hex
    directory = report_dir / 'executions'
    directory.mkdir(parents=True, exist_ok=True)
    harness = Path(__file__).with_name('probe_capture.py').resolve()
    started = stamp()
    with tempfile.TemporaryDirectory(prefix='fb-execution-') as temporary:
        trace = Path(temporary) / 'trace.jsonl'
        # Execute the saved source snapshot; never rewrite the original script.
        snapshot = Path(temporary) / script.name
        snapshot.write_bytes(code)
        command = [sys.executable, str(harness), str(snapshot), str(trace), str(cwd / 'scope.json'), str(script)]
        process = await asyncio.create_subprocess_exec(*command, cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        failure = None
        communication = asyncio.create_task(process.communicate())
        try:
            stdout, stderr = await asyncio.wait_for(asyncio.shield(communication), timeout)
        except asyncio.TimeoutError:
            process.kill()
            stdout, stderr = await communication
            failure = 'Execution timed out'
        events = []
        if trace.exists():
            for line in trace.read_text(encoding='utf-8').splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    failure = 'Incomplete HTTP trace'
        events.sort(key=lambda event: event['sequence'])
    parsed = None
    for line in stdout.decode('utf-8', errors='replace').splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                parsed = value
        except ValueError:
            pass
    if script.read_bytes() != code:
        failure = 'Script changed during execution'
    receipt = {'execution_id': identifier, 'script': script.name, 'source': source,
        'triggered_by': triggered_by, 'script_sha256': digest,
        'script_source_base64': base64.b64encode(code).decode(), 'command': command,
        'started_at': started, 'completed_at': stamp(), 'exit_code': process.returncode,
        'stdout': stdout.decode('utf-8', errors='replace'), 'stderr': stderr.decode('utf-8', errors='replace'),
        'stdout_base64': base64.b64encode(stdout).decode(), 'stderr_base64': base64.b64encode(stderr).decode(),
        'parsed_result': parsed, 'trace': events, 'error': failure}
    receipt['signature'] = hmac.new(key_for(root, True), canonical(receipt), hashlib.sha256).hexdigest()
    with (directory / f'{identifier}.json').open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, indent=2)
    return receipt


def load_receipts(report_dir, root):
    receipts = {}
    try:
        key = key_for(root)
    except OSError:
        return receipts
    for path in sorted((Path(report_dir) / 'executions').glob('*.json')):
        try:
            receipt = json.loads(path.read_text(encoding='utf-8'))
            signature = receipt.pop('signature')
            expected = hmac.new(key, canonical(receipt), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                continue
            receipts[receipt['execution_id']] = receipt
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return receipts


def captures(verification):
    if not isinstance(verification, dict):
        return []
    actions = verification.get('actions')
    if not isinstance(actions, list):
        actions = [verification['action']] if isinstance(verification.get('action'), dict) else []
    return [verification.get('before'), *actions, verification.get('after')]


def normalize_response(response, status=None):
    """Return the canonical response body/status used for signed-trace matching.

    ``actual`` is a capture envelope only when it is the object's sole key. An
    API payload that happens to contain ``actual`` alongside other fields is
    data and must remain intact.
    """
    if isinstance(response, dict) and 'status_code' in response and 'body' in response:
        status = status if status is not None else response['status_code']
        response = response['body']
        if isinstance(response, str):
            try:
                response = json.loads(response)
            except ValueError:
                pass
    if isinstance(response, dict) and set(response) == {'actual'}:
        response = response['actual']
    return response, status


def resolve_numeric_path(response, path):
    """Resolve an invariant path exactly or through one unambiguous envelope."""
    if not isinstance(path, list) or not path or any(not isinstance(part, str) for part in path):
        raise ValueError('Invariant paths must be non-empty arrays of object keys')

    missing = object()

    def exact(root):
        value = root
        for part in path:
            if not isinstance(value, dict) or part not in value:
                return missing
            value = value[part]
        return value

    direct = exact(response)
    envelope = None
    fallback = missing
    if isinstance(response, dict) and len(response) == 1:
        envelope, child = next(iter(response.items()))
        if isinstance(child, dict):
            fallback = exact(child)

    if direct is not missing:
        # Do not silently accept a path whose envelope-relative interpretation
        # points at different data. Exact still wins when there is no conflict.
        if fallback is not missing and fallback != direct:
            raise ValueError('Exact and envelope-relative invariant paths conflict')
        value, resolved = direct, list(path)
    elif fallback is not missing:
        value, resolved = fallback, [envelope, *path]
    else:
        raise KeyError('Invariant path does not resolve')

    if type(value) not in (int, float) or not __import__('math').isfinite(value):
        raise ValueError('Invariant value is not a finite number')
    return value, resolved


def trace_error(record, receipt):
    if receipt.get('error') or receipt.get('exit_code') != 0:
        return 'Executor failed or timed out'
    if record.get('script') != receipt.get('script') or record.get('source', 'MUTATION_SCRIPT') != receipt.get('source'):
        return 'Execution source does not match attributed script/component'
    if record.get('triggered_by') != receipt.get('triggered_by'):
        return 'Follow-up attribution differs from execution receipt'
    parsed = receipt.get('parsed_result') or {}
    verification = record.get('verification')
    claimed_verification = deepcopy(verification) if isinstance(verification, dict) else verification
    if isinstance(claimed_verification, dict):
        claimed_verification.pop('resolved_invariant', None)
    if not isinstance(verification, dict) or claimed_verification != parsed.get('verification'):
        return 'Finding verification differs from raw script output'
    chain = captures(verification)
    trace = receipt.get('trace', [])
    import re
    for url in re.findall(r'https?://[^\s,]+', str(record.get('url_tested') or '')):
        if not any(event.get('request', {}).get('url') == url for event in trace):
            return 'Attributed endpoint was not executed by this script'
    if len(chain) < 3 or any(not isinstance(capture, dict) for capture in chain):
        return 'Complete before/actions/after trace is required'
    matched = []
    for capture in chain:
        request = capture.get('request') or {}
        request = {key: request.get(key) for key in ('method', 'url', 'body')}
        response, status = normalize_response(capture.get('response'), capture.get('status_code'))
        candidates = [event for event in trace
                      if event.get('request') == request
                      and normalize_response(event.get('response'), event.get('status_code')) == (response, status)
                      and event.get('sequence') == capture.get('sequence')
                      and event.get('complete')]
        if len(candidates) != 1:
            return 'Claimed request/response is absent or ambiguous in the HTTP trace'
        matched.append(candidates[0])
    ids = [event['sequence'] for event in matched]
    if ids != sorted(ids) or len(set(ids)) != len(ids):
        return 'Claimed action order contradicts execution'
    before, after = matched[0], matched[-1]
    if before['request']['method'] != 'GET' or after['request']['method'] != 'GET':
        return 'Independent before and after GET state reads are required'
    # Windows' monotonic clock can report identical ticks for adjacent calls.
    # Equality is therefore not evidence of overlap; only a strict reversal is.
    if before['completed_ns'] > matched[1]['started_ns'] or max(e['completed_ns'] for e in matched[1:-1]) > after['started_ns']:
        return 'State reads do not bracket all tested actions'
    actual_actions = [event['sequence'] for event in trace if before['sequence'] < event['sequence'] < after['sequence']
                      and event['request']['method'] not in ('GET', 'HEAD', 'OPTIONS')]
    claimed_actions = [event['sequence'] for event in matched[1:-1] if event['request']['method'] not in ('GET', 'HEAD', 'OPTIONS')]
    if actual_actions != claimed_actions:
        return 'State-changing requests were omitted from finding evidence'
    # Recompute financial/range invariants against captured state, never a flag.
    if verification.get('predicate') == 'business_rule_must_hold':
        invariant = verification.get('invariant') or {}
        try:
            if invariant.get('operator') != 'sum_lte':
                return 'Business invariant has no supported executable predicate'
            resolved_terms = [resolve_numeric_path(after['response'], path) for path in invariant['terms']]
            limit_value, resolved_limit = resolve_numeric_path(after['response'], invariant['limit'])
            violated = sum(value for value, _ in resolved_terms) > limit_value
            if not invariant['terms'] or violated != verification.get('violation', {}).get('observed'):
                return 'Claimed invariant result contradicts captured state'
            verification['resolved_invariant'] = {
                'operator': 'sum_lte',
                'terms': [path for _, path in resolved_terms],
                'limit': resolved_limit,
            }
        except (TypeError, ValueError, KeyError):
            return 'Invariant paths cannot be evaluated against captured state'
    return None


def reconcile(data, report_dir, root):
    """Never trust report-provided provenance flags or reconstructed result rows."""
    receipts = load_receipts(report_dir, root)
    findings = data.get('findings') or []
    for finding in findings:
        finding.pop('_provenance_error', None)
        # Older cross-flow prompts used CROSS_FLOW for analytical origin. Bind
        # only when the backend has exactly one signed mutation receipt for the
        # named script; never guess between reruns or verification probes.
        if not finding.get('execution_id') and finding.get('script') and finding.get('source') == 'CROSS_FLOW':
            candidates = [receipt for receipt in receipts.values()
                          if receipt.get('script') == finding['script']
                          and receipt.get('source') == 'MUTATION_SCRIPT']
            if len(candidates) == 1:
                finding['analysis_source'] = 'CROSS_FLOW'
                finding['source'] = 'MUTATION_SCRIPT'
                finding['execution_id'] = candidates[0]['execution_id']
        receipt = receipts.get(finding.get('execution_id'))
        finding['_provenance_error'] = trace_error(finding, receipt) if receipt else 'No authenticated execution receipt for this finding'
        finding['execution_trace'] = deepcopy(receipt.get('trace', [])) if receipt else []
    results = []
    for identifier, receipt in receipts.items():
        parsed = receipt.get('parsed_result') or {}
        row = {'script': receipt['script'], 'source': receipt['source'],
            'triggered_by': receipt.get('triggered_by'), 'execution_id': identifier,
            'mutation_type': parsed.get('mutation_type', ''), 'outcome': parsed.get('outcome', 'NEEDS_REVIEW'),
            'url_tested': parsed.get('url_tested', parsed.get('url', '')),
            'status_code': parsed.get('status_code'), 'response_snippet': parsed.get('response_body_snippet'),
            'verification': deepcopy(parsed.get('verification')), 'parsed_result': deepcopy(parsed),
            'error_message': receipt.get('error') or ('Script execution failed' if receipt['exit_code'] else None)}
        row['_provenance_error'] = trace_error(row, receipt)
        linked = [f for f in findings if f.get('execution_id') == identifier]
        if len(linked) == 1:
            row['finding_id'] = linked[0]['id']
        results.append(row)
    data['results'] = results
    return data


async def execute_run(run_dir, flow, root, progress):
    run_dir = Path(run_dir)
    reports = run_dir / 'reports' / flow
    reports.mkdir(parents=True, exist_ok=True)
    findings_path = reports / 'findings.json'
    draft = json.loads(findings_path.read_text(encoding='utf-8')) if findings_path.exists() else {'findings': []}
    with (reports / f'agent-draft-{uuid.uuid4().hex}.json').open('x', encoding='utf-8') as stream:
        json.dump(draft, stream, indent=2)
    scripts = sorted((run_dir / 'mutations' / flow).glob('*.py'))
    # Extra verification probes are separate programs/receipts, never relabelled mutations.
    extra = run_dir / 'verification_probes' / flow
    scripts += sorted(extra.glob('*.py'))
    for script in scripts:
        source = 'VERIFICATION_PROBE' if script.parent == extra else 'MUTATION_SCRIPT'
        matching = [f for f in draft.get('findings', []) if f.get('script') == script.name
                    and (f.get('source', 'MUTATION_SCRIPT') == source
                         or source == 'MUTATION_SCRIPT' and f.get('source') == 'CROSS_FLOW')]
        triggered_by = matching[0].get('triggered_by') if len(matching) == 1 else None
        progress(f'Executing {script.name} with transport capture')
        receipt = await execute(script, reports, root=root, cwd=run_dir, source=source, triggered_by=triggered_by)
        parsed = receipt.get('parsed_result') or {}
        if not matching and parsed.get('verification'):
            finding = {'id': f'EXEC-{receipt["execution_id"]}', 'title': parsed.get('title', script.stem),
                'script': script.name, 'source': source, 'cwe': [], 'severity': 'Not assessed'}
            draft.setdefault('findings', []).append(finding)
            matching = [finding]
        for finding in matching:
            if source == 'MUTATION_SCRIPT' and finding.get('source') == 'CROSS_FLOW':
                finding['analysis_source'] = 'CROSS_FLOW'
                finding['source'] = source
            finding['execution_id'] = receipt['execution_id']
            finding['triggered_by'] = receipt.get('triggered_by')
            # Claimed captures must agree with stdout. Empty drafts can acquire raw evidence.
            if not finding.get('verification'):
                finding['verification'] = deepcopy(parsed.get('verification'))
    findings_path.write_text(json.dumps(draft, indent=2), encoding='utf-8')


async def execute_state_lock(run_dir, flow, root, target_url):
    """Optional backend component uses the same recorded execution boundary."""
    from backend.runtime.state_lock_probe import find_child_mutation_target, merge_finding
    run_dir = Path(run_dir)
    target = find_child_mutation_target(run_dir / 'flows' / flow / 'recording.har')
    if not target:
        return None
    reports = run_dir / 'reports' / flow
    script = reports / ('backend-state-lock-' + uuid.uuid4().hex + '.py')
    helper = Path(__file__).with_name('state_lock_probe.py').read_text(encoding='utf-8')
    wrapper = '''
original = ns['_do_request']
events = []
def captured(method, url, *args, **kwargs):
    status, body = original(method, url, *args, **kwargs)
    def decode(value):
        if isinstance(value, bytes): value = value.decode('utf-8', errors='replace')
        try: return json.loads(value)
        except (ValueError, TypeError): return value
    events.append({'sequence': len(events) + 1, 'status_code': status, 'complete': status is not None,
                   'request': {'method': method, 'url': url, 'body': decode(kwargs.get('data'))},
                   'response': decode(body)})
    return status, body
ns['_do_request'] = captured
finding = ns['run_probe'](target, target_url)
if finding:
    v = finding['verification']
    matches = [i for i, e in enumerate(events) if e['request']['method'] == 'DELETE' and e['request']['url'] == v['action']['request']['url']]
    if len(matches) == 1:
        i = matches[0]
        before = next((e for e in reversed(events[:i]) if e['request']['method'] == 'GET'), None)
        after = next((e for e in events[i+1:] if e['request']['method'] == 'GET'), None)
        if before and after:
            v['before'].update(before)
            v['action'].update(events[i])
            v['after'].update(after)
    finding['script'] = script_name
    print(json.dumps(finding))
else:
    print(json.dumps({'outcome': 'NEEDS_REVIEW', 'reason': 'No complete state-lock finding produced'}))
'''
    code = ('import json\nns = {"__name__": "captured_state_lock"}\nexec(' + repr(helper) + ', ns)\n'
            + 'target = ' + repr(target) + '\ntarget_url = ' + repr(target_url)
            + '\nscript_name = ' + repr(script.name) + '\n' + wrapper)
    with script.open('x', encoding='utf-8') as stream:
        stream.write(code)
    receipt = await execute(script, reports, root=root, cwd=run_dir, source='STATE_LOCK_PROBE')
    finding = receipt.get('parsed_result')
    if not finding or not finding.get('verification'):
        return None
    finding = deepcopy(finding)
    finding['execution_id'] = receipt['execution_id']
    return merge_finding(run_dir, flow, finding, target_url)
