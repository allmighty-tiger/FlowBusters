"""Backend-owned, append-only execution receipts and report provenance gate."""
import asyncio
import ast
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
import time
import uuid

from backend.runtime.ui_provenance import UIProvenanceError, validate_rule_reference


PROBE_CONTRACT_VERSION = 1
SUPPORTED_BUSINESS_INVARIANTS = {'sum_lte'}


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
        from backend.runtime.endpoint_catalog import load_endpoint_catalog
        catalog = load_endpoint_catalog(cwd, root)
        scope_path = cwd / 'scope.json'
        if catalog is not None:
            scope = json.loads(scope_path.read_text(encoding='utf-8'))
            scope['_endpoint_catalog'] = catalog['endpoints']
            scope_path = Path(temporary) / 'execution-scope.json'
            scope_path.write_text(json.dumps(scope), encoding='utf-8')
        # Execute the saved source snapshot; never rewrite the original script.
        snapshot = Path(temporary) / script.name
        snapshot.write_bytes(code)
        command = [sys.executable, str(harness), str(snapshot), str(trace), str(scope_path), str(script)]
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
    receipt['contract_validation'] = validate_probe_output(receipt, enforce_contract=True)
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


def _all_declared_captures(verification):
    """Return every transport capture declared by a versioned probe output."""
    declared = []
    setup = verification.get('setup') if isinstance(verification, dict) else None
    if isinstance(setup, list):
        declared.extend(setup)
    declared.extend(captures(verification))
    scenarios = verification.get('supplementary_scenarios') if isinstance(verification, dict) else None
    if isinstance(scenarios, dict):
        for scenario in scenarios.values():
            if not isinstance(scenario, dict):
                continue
            scenario_setup = scenario.get('setup')
            if isinstance(scenario_setup, list):
                declared.extend(scenario_setup)
            declared.extend(captures(scenario))
    return [capture for capture in declared if capture is not None]


def _path_shape(path):
    return (isinstance(path, list) and bool(path)
            and all(isinstance(part, str) and part for part in path))


def _exact_numeric_value(response, path):
    value = response
    for part in path:
        if not isinstance(value, dict) or part not in value:
            raise KeyError(part)
        value = value[part]
    if type(value) not in (int, float) or not __import__('math').isfinite(value):
        raise ValueError('Invariant value is not a finite number')
    return value


def preflight_script_contract(script):
    """Catch literal generated-output contract defects without running a probe.

    Dynamic verification construction is validated after execution. This gate is
    deliberately conservative: it rejects only defects visible in a literal
    verification dictionary and never guesses an invariant.
    """
    try:
        tree = ast.parse(Path(script).read_text(encoding='utf-8'), filename=str(script))
    except (OSError, SyntaxError, UnicodeError) as exc:
        return f'Probe contract preflight failed: {type(exc).__name__}: {exc}'
    assignments = {}
    functions = {}
    for item in ast.walk(tree):
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            returns = [n.value for n in ast.walk(item) if isinstance(n, ast.Return)]
            if len(returns) == 1:
                functions[item.name] = returns[0]
        if isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name):
                    assignments.setdefault(target.id, []).append(item.value)

    def dictionary_fields(value, seen=()):
        # Shape resolution only: never evaluate script code or infer values.
        if id(value) in seen:
            return {}
        seen = (*seen, id(value))
        if isinstance(value, ast.Await):
            return dictionary_fields(value.value, seen)
        if isinstance(value, ast.Name) and len(assignments.get(value.id, [])) == 1:
            return dictionary_fields(assignments[value.id][0], seen)
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            return dictionary_fields(functions.get(value.func.id), seen)
        fields = {}
        if isinstance(value, ast.Dict):
            for key, child in zip(value.keys, value.values):
                if key is None:
                    fields.update(dictionary_fields(child, seen))
                elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                    fields[key.value] = child
        return fields

    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        fields = dictionary_fields(node)
        if 'verification' in fields and 'supplementary_scenarios' in fields:
            return ('Probe output contract error: supplementary_scenarios must be '
                    'a named object inside verification, not a top-level result field')
        predicate = fields.get('predicate')
        if not isinstance(predicate, ast.Constant) or not isinstance(predicate.value, str):
            continue
        if predicate.value == 'business_rule_must_hold' and 'invariant' not in fields:
            return ('Probe output contract error: business_rule_must_hold requires '
                    'a supported executable invariant; use unsupported_business_rule '
                    'with unsupported_reason when no supported predicate applies')
        if predicate.value == 'business_rule_must_hold':
            violation = fields.get('violation')
            if isinstance(violation, ast.Dict):
                violation_fields = {}
                for key, value in zip(violation.keys, violation.values):
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        violation_fields[key.value] = value
                observed = violation_fields.get('observed')
                if (isinstance(observed, ast.Constant)
                        and type(observed.value) is not bool):
                    return ('Probe output contract error: literal violation.observed '
                            f'must be boolean, not {type(observed.value).__name__}; '
                            'compute it from the captured AFTER response before printing JSON')
        if predicate.value == 'unsupported_business_rule' and 'unsupported_reason' not in fields:
            return ('Probe output contract error: unsupported_business_rule requires '
                    'a concrete unsupported_reason')
    return None


def validate_probe_output(receipt, *, enforce_contract=False):
    """Validate backend-captured probe output without trusting an agent flag.

    New receipts opt into the versioned full-transport contract. Legacy receipts
    remain loadable and continue through the existing strict trace matcher.
    """
    result = {'version': PROBE_CONTRACT_VERSION, 'status': 'valid',
              'category': None, 'error': None}
    if receipt.get('error') or receipt.get('exit_code') != 0:
        result.update(status='invalid', category='process_error',
                      error=receipt.get('error') or 'Probe process exited non-zero')
        return result
    parsed = receipt.get('parsed_result')
    if not isinstance(parsed, dict):
        result.update(status='invalid', category='evidence_contract_error',
                      error='Probe output contract error: final stdout JSON object is required')
        return result
    verification = parsed.get('verification')
    if not isinstance(verification, dict):
        result.update(status='invalid', category='evidence_contract_error',
                      error='Probe output contract error: verification must be an object')
        return result
    if 'supplementary_scenarios' in parsed:
        result.update(
            status='invalid',
            category='evidence_contract_error',
            error=('Probe output contract error: supplementary_scenarios must be '
                   'a named object inside verification, not a top-level result field'))
        return result
    supplementary = verification.get('supplementary_scenarios')
    if (supplementary is not None
            and (not isinstance(supplementary, dict)
                 or any(not isinstance(scenario, dict)
                        for scenario in supplementary.values()))):
        result.update(
            status='invalid',
            category='evidence_contract_error',
            error=('Probe output contract error: verification.supplementary_scenarios '
                   'must be an object mapping unique scenario names to evidence objects'))
        return result
    predicate = verification.get('predicate')
    if predicate == 'business_rule_must_hold':
        invariant = verification.get('invariant')
        if not isinstance(invariant, dict):
            result.update(status='invalid', category='evidence_contract_error',
                          error=('Probe output contract error: business_rule_must_hold '
                                 'requires a supported executable invariant'))
            return result
        if invariant.get('operator') not in SUPPORTED_BUSINESS_INVARIANTS:
            result.update(status='invalid', category='evidence_contract_error',
                          error=('Probe output contract error: unsupported invariant operator; '
                                 'use unsupported_business_rule instead of inventing an invariant'))
            return result
        terms = invariant.get('terms')
        if not isinstance(terms, list) or not terms or not all(_path_shape(path) for path in terms):
            result.update(status='invalid', category='evidence_contract_error',
                          error='Probe output contract error: invariant terms need non-empty response-root paths')
            return result
        if not _path_shape(invariant.get('limit')):
            result.update(status='invalid', category='evidence_contract_error',
                          error='Probe output contract error: invariant limit needs a response-root path')
            return result
        violation = verification.get('violation')
        if not isinstance(violation, dict) or type(violation.get('observed')) is not bool:
            result.update(status='invalid', category='evidence_contract_error',
                          error='Probe output contract error: violation.observed must be boolean')
            return result
    elif predicate == 'unsupported_business_rule':
        reason = verification.get('unsupported_reason')
        if not isinstance(reason, str) or not reason.strip():
            result.update(status='invalid', category='evidence_contract_error',
                          error=('Probe output contract error: unsupported_business_rule '
                                 'requires a concrete unsupported_reason'))
            return result
        result['status'] = 'reviewable'
    chain = captures(verification)
    if len(chain) < 3 or any(not isinstance(capture, dict) for capture in chain):
        result.update(status='invalid', category='evidence_contract_error',
                      error='Probe output contract error: complete before/actions/after captures are required')
        return result
    scenarios = [('primary', verification)]
    supplementary = verification.get('supplementary_scenarios')
    if isinstance(supplementary, dict):
        scenarios.extend((str(name), scenario) for name, scenario in supplementary.items()
                         if isinstance(scenario, dict) and any(captures(scenario)))
    for name, scenario in scenarios:
        scenario_chain = captures(scenario)
        scenario_sequences = [capture.get('sequence') for capture in scenario_chain
                              if isinstance(capture, dict)]
        if (scenario_sequences and
                (not all(type(value) is int and value > 0 for value in scenario_sequences)
                 or scenario_sequences != sorted(scenario_sequences)
                 or len(set(scenario_sequences)) != len(scenario_sequences))):
            result.update(status='invalid', category='evidence_contract_error',
                          error=(f'Probe output contract error: {name} scenario capture '
                                 'sequences must be unique and increasing'))
            return result
    declared = _all_declared_captures(verification) if enforce_contract else chain
    sequences = [capture.get('sequence') for capture in declared if isinstance(capture, dict)]
    if (len(sequences) != len(declared) or not all(type(value) is int and value > 0 for value in sequences)
            or len(set(sequences)) != len(sequences)):
        result.update(status='invalid', category='evidence_contract_error',
                      error='Probe output contract error: capture sequences must be unique positive integers')
        return result
    trace = receipt.get('trace')
    if not isinstance(trace, list):
        trace = []
    by_sequence = {event.get('sequence'): event for event in trace if isinstance(event, dict)}
    if len(by_sequence) != len(trace):
        result.update(status='invalid', category='trace_mismatch',
                      error='Signed transport trace contains duplicate or missing sequence numbers')
        return result
    for capture in declared:
        sequence = capture['sequence']
        event = by_sequence.get(sequence)
        if event is None:
            result.update(status='invalid', category='evidence_contract_error',
                          error=f'Probe output contract error: capture sequence {sequence} is absent from the signed transport trace')
            return result
        request = capture.get('request') or {}
        request = {key: request.get(key) for key in ('method', 'url', 'body')}
        if (event.get('request') != request
                or normalize_response(event.get('response'), event.get('status_code'))
                != normalize_response(capture.get('response'), capture.get('status_code'))
                or not event.get('complete')):
            result.update(status='invalid', category='evidence_contract_error',
                          error=(f'Probe output contract error: capture sequence {sequence} '
                                 'does not match its signed transport event'))
            return result
    if predicate == 'business_rule_must_hold':
        after_response, _ = normalize_response(
            verification['after'].get('response'), verification['after'].get('status_code'))
        invariant = verification['invariant']
        try:
            for path in [*invariant['terms'], invariant['limit']]:
                _exact_numeric_value(after_response, path)
        except (KeyError, TypeError, ValueError):
            result.update(status='invalid', category='evidence_contract_error',
                          error=('Probe output contract error: invariant paths must resolve '
                                 'to finite numbers from the full AFTER response root'))
            return result
    if enforce_contract:
        unaccounted = sorted(set(by_sequence) - set(sequences))
        if unaccounted:
            result.update(status='invalid', category='evidence_contract_error',
                          error=('Probe output contract error: signed transport sequences '
                                 f'{unaccounted} are not represented in setup or scenario captures'))
            return result
    record = {'script': receipt.get('script'), 'source': receipt.get('source'),
              'triggered_by': receipt.get('triggered_by'), 'verification': deepcopy(verification),
              'url_tested': parsed.get('url_tested', parsed.get('url', ''))}
    error = trace_error(record, receipt)
    if error:
        category = ('evidence_contract_error' if error.startswith('Claimed invariant result contradicts captured state') or error in {
            'Business invariant has no supported executable predicate',
            'Invariant paths cannot be evaluated against captured state',
            'Claimed invariant result contradicts captured state',
        } else 'trace_mismatch')
        result.update(status='invalid', category=category, error=error)
    return result


def receipt_evidence_error(record, receipt):
    """Apply the new output gate only to receipts created with that contract."""
    if not receipt:
        return 'No authenticated execution receipt for this finding'
    declared = receipt.get('contract_validation')
    if isinstance(declared, dict) and declared.get('version') == PROBE_CONTRACT_VERSION:
        validation = validate_probe_output(receipt, enforce_contract=True)
        if validation.get('status') == 'invalid':
            return validation.get('error')
    return trace_error(record, receipt)


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
            if (not invariant['terms']
                    or type(verification.get('violation', {}).get('observed')) is not bool
                    or violated != verification.get('violation', {}).get('observed')):
                return (f'Claimed invariant result contradicts captured state: scenario=primary; '
                        f'AFTER sequence={after.get("sequence")}; '
                        f'terms={invariant["terms"]} values={[value for value, _ in resolved_terms]}; '
                        f'limit={invariant["limit"]} value={limit_value}; '
                        f'claimed={verification.get("violation", {}).get("observed")!r}; '
                        f'recomputed={violated!r}')
            verification['resolved_invariant'] = {
                'operator': 'sum_lte',
                'terms': [path for _, path in resolved_terms],
                'limit': resolved_limit,
            }
        except (TypeError, ValueError, KeyError):
            return 'Invariant paths cannot be evaluated against captured state'
    # Supplementary evidence is not a separate verdict. If it declares an
    # invariant, however, its result must match its own authenticated AFTER.
    for name, scenario in (verification.get('supplementary_scenarios') or {}).items():
        if not isinstance(scenario, dict) or 'invariant' not in scenario:
            continue
        isolated = deepcopy(scenario)
        isolated['predicate'] = 'business_rule_must_hold'
        isolated.pop('supplementary_scenarios', None)
        scenario_receipt = {**receipt, 'parsed_result': {'verification': deepcopy(isolated)}}
        scenario_record = {**record, 'verification': isolated, 'url_tested': ''}
        error = trace_error(scenario_record, scenario_receipt)
        if error:
            return error.replace('scenario=primary;', f'scenario={name};') if 'scenario=primary;' in error else f'{error} (scenario={name})'
    return None


def _is_stale_unexecuted_draft(verification):
    """Recognize a pre-execution plan without accepting claimed captures."""
    if not isinstance(verification, dict):
        return False
    execution = verification.get('execution')
    if not isinstance(execution, dict) or execution.get('status') != 'NOT_EXECUTED':
        return False
    return not any(key in verification for key in ('before', 'action', 'actions', 'after'))


def _candidate_declares_separate_scenarios(candidate):
    actions = ((candidate.get('before_action_after') or {}).get('actions')
               if isinstance(candidate, dict) else None)
    if not isinstance(actions, list):
        return False
    text = ' '.join(str(action).lower() for action in actions)
    return 'sub-scenario' in text or 'separate reset' in text


def _partial_coverage_for(finding, candidates):
    """Expose declared but non-atomic supplementary captures without verdicts."""
    verification = finding.get('verification')
    if not isinstance(verification, dict):
        return []
    rule = verification.get('rule') or {}
    reference = str(rule.get('reference') or '') if isinstance(rule, dict) else ''
    match = __import__('re').search(r'\bXF-\d+\b', reference)
    candidate_id = match.group(0) if match else None
    candidate = next((item for item in candidates
                      if isinstance(item, dict) and item.get('id') == candidate_id), None)
    scenarios = verification.get('supplementary_scenarios')
    # Authenticated supplementary captures are coverage data even when an old
    # agent draft omitted a machine-readable candidate link.  Candidate text
    # is never required to keep such non-verdict evidence visible.
    if not isinstance(scenarios, dict):
        return []
    labels = {
        'cancel_after_price_adjustment': 'Cancel after price adjustment',
        'refund_amount_tamper': 'Refund amount override',
    }
    partial = []
    for scenario_id, scenario in scenarios.items():
        if not isinstance(scenario, dict):
            missing = ['before/actions/after', 'invariant', 'violation']
        else:
            missing = []
            if not (isinstance(scenario.get('before'), dict)
                    and isinstance(scenario.get('actions'), list) and scenario['actions']
                    and isinstance(scenario.get('after'), dict)):
                missing.append('complete before/actions/after')
            if not isinstance(scenario.get('invariant'), dict):
                missing.append('independent invariant')
            violation = scenario.get('violation')
            if not isinstance(violation, dict) or type(violation.get('observed')) is not bool:
                missing.append('independent violation result')
            from backend.runtime.verification import _endpoint_coverage_error
            endpoint_error = _endpoint_coverage_error(scenario)
            if endpoint_error:
                missing.append('available required endpoints (' + endpoint_error + ')')
        if not missing:
            # A complete supplementary record must still be authenticated by an
            # independently attributed trace before it can become a verdict.
            continue
        partial.append({
            'id': f"{finding.get('id', 'finding')}:{scenario_id}",
            'scenario_id': scenario_id,
            'title': labels.get(scenario_id, str(scenario_id).replace('_', ' ').capitalize()),
            'source_finding_id': finding.get('id'),
            'candidate_id': candidate_id,
            'execution_id': finding.get('execution_id'),
            'script': finding.get('script'),
            'status': 'PARTIAL_COVERAGE',
            'reason': ('Captured supplementary trace is not an independent tested verdict; missing '
                       + ', '.join(missing) + '.'),
            'before': deepcopy(scenario.get('before')) if isinstance(scenario, dict) else None,
            'actions': deepcopy(scenario.get('actions', [])) if isinstance(scenario, dict) else [],
            'after': deepcopy(scenario.get('after')) if isinstance(scenario, dict) else None,
        })
    return partial


def reconcile(data, report_dir, root):
    """Never trust report-provided provenance flags or reconstructed result rows."""
    receipts = load_receipts(report_dir, root)
    report_dir = Path(report_dir).resolve()
    run_dir = report_dir.parent.parent if report_dir.parent.name == 'reports' else report_dir.parent
    flow = report_dir.name
    from backend.runtime.candidate_correction import load_gate
    gate = load_gate(run_dir, root)
    data.pop('unverified_candidates', None)
    data.pop('validated_ai_candidates', None)
    if gate is not None:
        data['unverified_candidates'] = gate['unverified_coverage']
        data['validated_ai_candidates'] = len(gate['valid_candidate_ids'])
        data['findings'] = [f for f in data.get('findings', [])
                            if f.get('script') in gate['allowed_scripts']
                            or any(receipt.get('source') == 'BACKEND_COVERAGE'
                                   and receipt.get('script') == f.get('script')
                                   for receipt in receipts.values())]
    planned = [
        *(('BACKEND_COVERAGE', path.name) for path in sorted((run_dir / 'backend_coverage' / flow).glob('*.py'))),
        *(('MUTATION_SCRIPT', path.name) for path in sorted((run_dir / 'mutations' / flow).glob('*.py'))),
        *(('VERIFICATION_PROBE', path.name) for path in sorted((run_dir / 'verification_probes' / flow).glob('*.py'))),
    ]
    terminal_receipts = {
        identifier: receipt for identifier, receipt in receipts.items()
        if isinstance(receipt.get('completed_at'), str) and receipt['completed_at'].strip()
        and type(receipt.get('exit_code')) is int
    }
    if gate is not None:
        planned = [p for p in planned if p[0] == 'BACKEND_COVERAGE' or p[1] in gate['allowed_scripts']]

    def attach_rule_provenance(record):
        record.pop('_rule_provenance_valid', None)
        record.pop('_rule_provenance_error', None)
        record.pop('_validated_rule_provenance', None)
        verification = record.get('verification')
        if (not isinstance(verification, dict)
                or verification.get('predicate') != 'business_rule_must_hold'):
            return
        rule = verification.get('rule')
        if not isinstance(rule, dict) or rule.get('source') != 'observed_ui':
            return
        try:
            record['_validated_rule_provenance'] = validate_rule_reference(rule, run_dir, root=root)
            record['_rule_provenance_valid'] = True
        except UIProvenanceError as exc:
            record['_rule_provenance_valid'] = False
            record['_rule_provenance_error'] = str(exc)

    findings = data.get('findings') or []
    for finding in findings:
        finding.pop('_provenance_error', None)
        finding.pop('deduplicated_from', None)
        finding.pop('deduplicated_into', None)
        # Older cross-flow prompts used CROSS_FLOW for analytical origin. Bind
        # only when the backend has exactly one signed mutation receipt for the
        # named script; never guess between reruns or verification probes.
        if not finding.get('execution_id') and finding.get('script'):
            candidates = [receipt for receipt in receipts.values()
                          if receipt.get('script') == finding['script']
                          and (receipt.get('source') == finding.get('source')
                               or finding.get('source') == 'CROSS_FLOW'
                               and receipt.get('source') == 'MUTATION_SCRIPT')]
            if len(candidates) == 1:
                if finding.get('source') == 'CROSS_FLOW':
                    finding['analysis_source'] = 'CROSS_FLOW'
                    finding['source'] = 'MUTATION_SCRIPT'
                finding['execution_id'] = candidates[0]['execution_id']
        receipt = receipts.get(finding.get('execution_id'))
        parsed_verification = ((receipt.get('parsed_result') or {}).get('verification')
                               if receipt else None)
        if isinstance(parsed_verification, dict) and (
                not isinstance(finding.get('verification'), dict)
                or _is_stale_unexecuted_draft(finding.get('verification'))):
            if 'verification' in finding:
                finding['agent_draft_verification'] = deepcopy(finding['verification'])
            finding['verification'] = deepcopy(parsed_verification)
        if (isinstance(parsed_verification, dict)
                and isinstance(finding.get('execution'), dict)
                and finding['execution'].get('status') == 'NOT_EXECUTED'):
            finding['agent_draft_execution'] = deepcopy(finding.pop('execution'))
        finding['_provenance_error'] = receipt_evidence_error(finding, receipt)
        from backend.runtime.coverage_plans import receipt_probe_origin
        finding['probe_origin'] = receipt_probe_origin(receipt) if receipt else 'UNKNOWN'
        finding['execution_trace'] = deepcopy(receipt.get('trace', [])) if receipt else []
        attach_rule_provenance(finding)
    partial_coverage = []
    cross_flow_candidates = data.get('_cross_flow_candidates')
    if not isinstance(cross_flow_candidates, list):
        cross_flow_candidates = []
    for finding in findings:
        partial_coverage.extend(_partial_coverage_for(finding, cross_flow_candidates))
    data['_partial_coverage'] = partial_coverage
    results = []
    for identifier, receipt in receipts.items():
        parsed = receipt.get('parsed_result') or {}
        row = {'script': receipt['script'], 'source': receipt['source'],
            'triggered_by': receipt.get('triggered_by'), 'execution_id': identifier,
            'title': parsed.get('title', ''),
            'mutation_type': parsed.get('mutation_type', ''), 'outcome': parsed.get('outcome', 'NEEDS_REVIEW'),
            'url_tested': parsed.get('url_tested', parsed.get('url', '')),
            'status_code': parsed.get('status_code'), 'response_snippet': parsed.get('response_body_snippet'),
            'verification': deepcopy(parsed.get('verification')), 'parsed_result': deepcopy(parsed),
            'error_message': receipt.get('error') or ('Script execution failed' if receipt['exit_code'] else None)}
        from backend.runtime.coverage_plans import receipt_probe_origin
        row['probe_origin'] = receipt_probe_origin(receipt)
        validation = (validate_probe_output(receipt, enforce_contract=True)
                      if isinstance(receipt.get('contract_validation'), dict)
                      and receipt['contract_validation'].get('version') == PROBE_CONTRACT_VERSION
                      else None)
        row['evidence_validation_status'] = validation.get('status') if validation else 'legacy'
        row['evidence_error_category'] = validation.get('category') if validation else None
        row['_provenance_error'] = receipt_evidence_error(row, receipt)
        attach_rule_provenance(row)
        linked = [f for f in findings if f.get('execution_id') == identifier]
        if len(linked) == 1:
            row['finding_id'] = linked[0]['id']
        results.append(row)
    terminal_plans = {
        (receipt.get('source'), receipt.get('script'))
        for receipt in terminal_receipts.values()
    }
    pending = sum(plan not in terminal_plans for plan in planned)
    executor_failures = {
        identifier for identifier, receipt in terminal_receipts.items()
        if receipt.get('error') or receipt.get('exit_code') != 0
    }
    parse_failures = {
        identifier for identifier, receipt in terminal_receipts.items()
        if not isinstance(receipt.get('parsed_result'), dict)
    }
    contract_failures = {
        row['execution_id'] for row in results
        if row.get('evidence_error_category') == 'evidence_contract_error'
    }
    # trace_error also evaluates supported business invariants. An unsupported
    # predicate is a verdict/provenance limitation, not a transport trace
    # mismatch and must not turn a completed authenticated execution into an
    # execution error.
    semantic_failures = {
        'Business invariant has no supported executable predicate',
        'Invariant paths cannot be evaluated against captured state',
        'Claimed invariant result contradicts captured state',
    }
    trace_failures = {
        row['execution_id'] for row in results
        if row['execution_id'] in terminal_receipts
        and row['execution_id'] not in executor_failures
        and row['execution_id'] not in parse_failures
        and row['execution_id'] not in contract_failures
        and row.get('_provenance_error')
        and not row['_provenance_error'].startswith('Claimed invariant result contradicts captured state')
        and row['_provenance_error'] not in semantic_failures
    }
    raw_receipt_count = len(list((report_dir / 'executions').glob('*.json')))
    data['_execution_summary'] = {
        'derived': True,
        'planned_executions': len(planned),
        'execution_attempts': len(receipts),
        'completed_executions': len(terminal_receipts),
        'pending_execution': pending,
        'missing_receipts': pending,
        'process_errors': len(executor_failures),
        'evidence_contract_errors': len(parse_failures | contract_failures),
        'execution_errors': len(executor_failures | parse_failures | contract_failures | trace_failures),
        'trace_mismatches': len(trace_failures),
        'untrusted_receipts': max(0, raw_receipt_count - len(receipts)),
    }
    data['results'] = results
    return data


def _readable_scenario_name(script):
    words = Path(script).stem.replace('_', '-').split('-')
    while words and words[0].isdigit():
        words.pop(0)
    label = ' '.join(word for word in words if word).strip() or Path(script).stem
    return label[0].upper() + label[1:] if label else 'Unnamed scenario'


async def execute_run(run_dir, flow, root, progress, execution_progress=None):
    run_dir = Path(run_dir)
    reports = run_dir / 'reports' / flow
    reports.mkdir(parents=True, exist_ok=True)
    findings_path = reports / 'findings.json'
    draft = json.loads(findings_path.read_text(encoding='utf-8')) if findings_path.exists() else {'findings': []}
    with (reports / f'agent-draft-{uuid.uuid4().hex}.json').open('x', encoding='utf-8') as stream:
        json.dump(draft, stream, indent=2)
    backend_coverage = run_dir / 'backend_coverage' / flow
    if list(backend_coverage.glob('*.py')):
        from backend.runtime.coverage_plans import prepare_coverage_probes
        authorized = set(prepare_coverage_probes(run_dir, flow, root))
        if set(backend_coverage.glob('*.py')) != authorized:
            raise ValueError('Unrecognized backend coverage script; generation authority cannot be established')
    scripts = sorted(backend_coverage.glob('*.py')) + sorted((run_dir / 'mutations' / flow).glob('*.py'))
    # Extra verification probes are separate programs/receipts, never relabelled mutations.
    extra = run_dir / 'verification_probes' / flow
    scripts += sorted(extra.glob('*.py'))
    completed = 0
    attempts = 0
    process_errors = 0
    contract_errors = 0
    trace_mismatches = 0
    total = len(scripts)
    from backend.runtime.endpoint_catalog import load_endpoint_catalog, preflight_endpoints
    endpoint_catalog = load_endpoint_catalog(run_dir, root)
    from backend.runtime.candidate_correction import load_gate
    gate = load_gate(run_dir, root)
    if gate is not None:
        scripts = [p for p in scripts if p.parent == backend_coverage or p.name in gate['allowed_scripts']]
        total = len(scripts)
    # Validate the entire finished plan before executing even the first probe.
    for planned_script in scripts:
        error = (preflight_script_contract(planned_script)
                 or preflight_endpoints(planned_script, endpoint_catalog))
        if error:
            raise ValueError(f'Probe plan preflight failed: {planned_script.name}: {error}')
    for index, script in enumerate(scripts, start=1):
        source = ('BACKEND_COVERAGE' if script.parent == backend_coverage else
                  'VERIFICATION_PROBE' if script.parent == extra else 'MUTATION_SCRIPT')
        matching = [f for f in draft.get('findings', []) if f.get('script') == script.name
                    and (f.get('source', 'MUTATION_SCRIPT') == source
                         or source == 'MUTATION_SCRIPT' and f.get('source') == 'CROSS_FLOW')]
        triggered_by = matching[0].get('triggered_by') if len(matching) == 1 else None
        scenario_name = _readable_scenario_name(script)
        preflight_error = preflight_script_contract(script)
        if not preflight_error:
            preflight_error = preflight_endpoints(script, endpoint_catalog)
        if preflight_error:
            contract_errors += 1
            if execution_progress is not None:
                execution_progress({
                    'stage': 'contract_failed',
                    'message': f'Probe {index}/{total} contract validation failed: {scenario_name}',
                    'technical_detail': f'Script: {script.name}; {preflight_error}',
                })
            else:
                progress(f'Probe contract validation failed for {script.name}: {preflight_error}')
            break
        if execution_progress is not None:
            execution_progress({
                'stage': 'started',
                'message': f'Executing probe {index}/{total}: {scenario_name}',
                'technical_detail': f'Script: {script.name}',
            })
        else:
            progress(f'Executing {script.name} with transport capture')
        probe_started = time.monotonic()
        attempts += 1
        try:
            receipt = await execute(
                script, reports, root=root, cwd=run_dir, source=source,
                triggered_by=triggered_by)
        except Exception:
            if execution_progress is not None:
                execution_progress({
                    'stage': 'failed',
                    'message': (f'Probe {index}/{total} failed: {scenario_name} '
                                f'({time.monotonic() - probe_started:.1f}s)'),
                    'technical_detail': f'Script: {script.name}',
                })
            raise
        completed += 1
        parsed = receipt.get('parsed_result') or {}
        validation = validate_probe_output(receipt, enforce_contract=True)
        category = validation.get('category')
        process_errors += int(category == 'process_error')
        contract_errors += int(category == 'evidence_contract_error')
        trace_mismatches += int(category == 'trace_mismatch')
        failed = validation.get('status') == 'invalid'
        if execution_progress is not None:
            outcome = ('process failed' if category == 'process_error'
                       else 'evidence validation failed' if category == 'evidence_contract_error'
                       else 'trace validation failed' if category == 'trace_mismatch'
                       else 'completed')
            execution_progress({
                'stage': 'failed' if failed else 'completed',
                'message': (f'Probe {index}/{total} {outcome}: {scenario_name} '
                             f'({time.monotonic() - probe_started:.1f}s)'),
                'technical_detail': (f'Script: {script.name}; '
                                     f'execution_id: {receipt.get("execution_id", "missing")}; '
                                     f'{validation.get("error") or "evidence contract valid"}'),
            })
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
        # A terminal receipt has already been persisted. Stop before executing
        # more generated code when its output violates the evidence contract.
        if failed:
            break
    findings_path.write_text(json.dumps(draft, indent=2), encoding='utf-8')
    if execution_progress is not None:
        attempt_noun = 'attempt' if attempts == 1 else 'attempts'
        pending = max(0, total - attempts)
        receipt_noun = 'receipt' if completed == 1 else 'receipts'
        summary_label = ('Execution stopped' if pending and
                         (process_errors or contract_errors or trace_mismatches)
                         else 'Execution complete')
        execution_progress({
            'stage': 'summary',
            'message': (f'{summary_label}: {attempts}/{total} probes attempted; '
                        f'{completed} authenticated terminal {receipt_noun}; '
                        f'{process_errors} process errors; {contract_errors} evidence-contract errors; '
                        f'{trace_mismatches} trace mismatches; {pending} pending'),
            'technical_detail': ('Counts derived from terminal backend execution receipts; '
                                 f'{attempts} execution {attempt_noun}'),
        })


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
            represented = {before['sequence'], events[i]['sequence'], after['sequence']}
            v['setup'] = [event for event in events if event['sequence'] not in represented]
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
