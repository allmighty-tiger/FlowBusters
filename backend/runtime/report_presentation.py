"""Read-only report projection. Never execute probes or change verification verdicts."""
import ast
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from backend.runtime.probe_executor import load_receipts, receipt_evidence_error, normalize_response


def _receipt_plan(receipt):
    raw = base64.b64decode(receipt['script_source_base64'], validate=True)
    if hashlib.sha256(raw).hexdigest() != receipt['script_sha256']:
        raise ValueError('Script digest mismatch')
    nodes = [n.value for n in ast.parse(raw).body if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == 'PROBE_PLAN' for t in n.targets)]
    if len(nodes) != 1:
        raise ValueError('Ambiguous plan')
    return ast.literal_eval(nodes[0])


def _draft_matches(draft, receipt, run):
    """Exact generation artifact AND receipt-embedded plan, not filename similarity."""
    name = draft.get('script')
    if not isinstance(name, str) or Path(name).name != name or not name.endswith('.plan.json'):
        return False
    if receipt['script'] != name[:-len('.plan.json')] + '.py':
        return False
    v = draft.get('verification') or {}
    if not isinstance(v, dict) or any(k in v for k in ('before', 'after', 'actions', 'action', 'setup')):
        return False
    if isinstance(v.get('violation'), dict) and type(v['violation'].get('observed')) is bool:
        return False
    try:
        plan = _receipt_plan(receipt)
        artifact = json.loads((run / 'mutations' / run.name / name).read_text(encoding='utf-8'))
        return (artifact == plan and v.get('predicate') == plan['predicate']
                and v.get('invariant') == plan.get('invariant'))
    except (OSError, ValueError, KeyError, TypeError, SyntaxError):
        return False


def _body(capture):
    return normalize_response(capture.get('response'), capture.get('status_code'))[0]


def _value(body, path):
    for key in path:
        if not isinstance(body, dict) or key not in body:
            raise ValueError('Missing exact path')
        body = body[key]
    return body


def _repeat_evidence(finding, receipt):
    """Narrow grouping: same evaluated fields and same successful repeated write.

    Each occurrence must measurably increase the evaluated amount. No grouping
    by CWE/title, inferred root cause, rejected requests or invented endpoints.
    """
    v = receipt['parsed_result']['verification']
    inv = finding.get('verification', {}).get('resolved_invariant') or v.get('invariant')
    if not isinstance(inv, dict) or inv.get('operator') != 'sum_lte' or len(inv.get('terms', [])) != 1:
        return None
    term, limit = inv['terms'][0], inv['limit']
    try:
        before, after = _body(v['before']), _body(v['after'])
        initial, total, maximum = _value(before, term), _value(after, term), _value(after, limit)
        if any(type(n) not in (int, float) for n in (initial, total, maximum)) or not total > maximum:
            return None
        previous = before
        writes = {}
        for a in v.get('actions', []):
            req = a.get('request') or {}
            body = _body(a)
            if (req.get('method') not in ('GET', 'HEAD', 'OPTIONS') and 200 <= a.get('status_code', 0) < 300):
                delta = _value(body, term) - _value(previous, term)
                if delta > 0:
                    key = json.dumps(req, sort_keys=True)
                    writes.setdefault(key, []).append((a, delta, previous))
            # Failed responses often contain only an error; retain last captured state.
            try:
                _value(body, term)
                previous = body
            except ValueError:
                pass
        repeated = [(key, items) for key, items in writes.items() if len(items) >= 2]
        if len(repeated) != 1:
            return None
        request_key, items = repeated[0]
        request = items[-1][0]['request']
        terminal = (v.get('inferred_invariant') or {}).get('terminal_state', [])
        terminal_values = [(row['path'], _value(after, row['path'])) for row in terminal]
        # Include exact resource IDs, limits and measured values, not just the URL.
        resource = before
        for key in term[:-1]:
            resource = resource[key]
        ids = {k: resource[k] for k in ('id', 'resource_id', 'tenant_id', 'principal_id') if k in resource}
        canonical = json.dumps([inv, terminal_values, request_key, ids, initial, total, maximum,
                                [item[1] for item in items]], sort_keys=True)
        segments = [s for s in urlsplit(request['url']).path.split('/') if s]
        completed = segments[-1:] == ['complete']
        resource_label = segments[-2].replace('-', ' ') if completed and len(segments) > 1 else 'operation'
        label = resource_label + ' completion' if completed else 'operation'
        double = len(items) == 2 and initial == 0 and total == 2 * maximum
        title = f'Repeated {label} causes a double payout' if completed and double else f'Repeated {label} increases the evaluated amount beyond its limit'
        prerequisite = None
        if len(terminal) == 1:
            prerequisite = _value(items[0][2], terminal[0]['path'])
        fixes = ([f'Reject completion unless the {resource_label} status is {prerequisite}.']
                 if completed and isinstance(prerequisite, str) else [])
        fixes += [f'Make {label} idempotent.',
                  f'Enforce {".".join(term)} <= {".".join(limit)} on the server.',
                  'Perform validation and state update atomically.']
        return canonical, {'title': title,
            'impact': f'The {label} endpoint accepted a second completion request and added the {resource_label} amount again.' if completed and double else 'A repeated successful request increased the evaluated amount beyond its recorded limit.',
            'original': maximum, 'returned': total, 'overpayment': total - maximum,
            'endpoint': request['method'] + ' ' + urlsplit(request['url']).path,
            'term': term, 'limit': limit, 'remediation': fixes,
            'grouping_basis': 'Same validated invariant, terminal-state fields and values, resource, successful repeated request and measured increments. Not a root-cause assertion.'}
    except (ValueError, TypeError, KeyError, IndexError):
        return None


def present_report(report, report_path, root):
    """Projection of an already classified report; receipt checks are read-only."""
    data = deepcopy(report)
    path = Path(report_path)
    run = path.parent.parent.parent
    receipts = load_receipts(path.parent, root)
    valid = {}
    for eid, r in receipts.items():
        record = dict(script=r['script'], source=r['source'], triggered_by=r.get('triggered_by'),
                      verification=(r.get('parsed_result') or {}).get('verification'))
        if receipt_evidence_error(record, r) is None:
            valid[eid] = r
    attached = {}
    remaining = []
    for draft in data.get('unexecuted_hypotheses') or []:
        matches = [eid for eid, r in valid.items() if _draft_matches(draft, r, run)]
        if len(matches) == 1:
            attached.setdefault(matches[0], []).append(draft)
        else:
            remaining.append(draft)
    data['unexecuted_hypotheses'] = remaining
    data['summary']['unexecuted_hypotheses'] = len(remaining)
    findings = {f['execution_id']: f for f in data.get('findings', []) if f.get('execution_id')}
    probes = []
    for index, result in enumerate(data.get('results') or []):
        eid = result.get('execution_id')
        finding = findings.get(eid) or result
        probe = deepcopy(finding)
        drafts = deepcopy(finding.get('associated_agent_drafts') or [])
        drafts.extend(d for d in attached.get(eid, []) if d not in drafts)
        probe.update(label=f'Probe {index + 1:02}', verdict=result.get('outcome'),
                     script=valid[eid]['script'] if eid in valid else result.get('script'),
                     execution_id=eid, associated_agent_drafts=drafts)
        reason = str(probe.get('verification_reason') or '')
        probe['display_reason'] = reason
        verification = (valid.get(eid, {}).get('parsed_result') or {}).get('verification') or {}
        if 'final state still has pending workflow' in reason:
            probe['display_reason'] = 'The workflow remained pending; terminal behavior was not tested.'
        elif verification.get('predicate') == 'unsupported_business_rule':
            probe['display_reason'] = 'Executed, but this hypothesis has no supported executable predicate. The outcome requires review.'
        elif result.get('outcome') == 'NOT_REPRODUCED':
            inv = verification.get('invariant') or {}
            try:
                body = _body(verification['after'])
                terms, limit = inv['terms'], inv['limit']
                total = sum(_value(body, p) for p in terms)
                maximum = _value(body, limit)
                probe['display_reason'] = f'{" + ".join(p[-1] for p in terms)} = {total:g}; {limit[-1]} = {maximum:g}. The tested amount bound was not exceeded.'
            except (ValueError, KeyError, TypeError):
                pass
        if eid in receipts:
            r = receipts[eid]
            probe['receipt_metadata'] = {k: r.get(k) for k in ('execution_id', 'script', 'script_sha256', 'source', 'exit_code', 'started_at', 'finished_at', 'contract_validation')}
            probe['raw_http_trace'] = r.get('trace', [])
            probe['receipt_path'] = f'reports/{run.name}/executions/{eid}.json'
        if eid in findings:
            findings[eid]['associated_agent_drafts'] = drafts
        probes.append(probe)
    groups = {}
    for probe in probes:
        if probe['verdict'] != 'CONFIRMED':
            continue
        receipt = valid.get(probe['execution_id'])
        evidence = _repeat_evidence(probe, receipt) if receipt else None
        key, detail = evidence if evidence else ('single:' + str(probe['execution_id']), {
            'title': probe.get('title'), 'impact': probe.get('normalized_issue') or probe.get('verification_reason'),
            'remediation': []})
        issue = groups.setdefault(key, dict(detail, id='OBS-' + hashlib.sha256(key.encode()).hexdigest()[:12], probes=[]))
        issue['probes'].append(probe['label'])
        issue.setdefault('execution_ids', []).append(probe['execution_id'])
    data['report_presentation'] = {'version': 1, 'probes': probes, 'issues': list(groups.values()),
        'counts': {status: sum(p['verdict'] == status for p in probes) for status in
                   ('CONFIRMED', 'NEEDS_REVIEW', 'NOT_REPRODUCED', 'CHECK_ERROR', 'INSUFFICIENT_EVIDENCE')},
        'coverage_notes': remaining}
    # This is an observed-impact grouping, not a claim of unique root causes.
    # Keep the old API metric for legacy consumers, but the new UI must not use
    # it: only the narrow observed-impact groups above are presented as issues.
    data['summary']['observed_security_issues'] = len(groups)
    return data
