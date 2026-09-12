"""Conservative report gate. HTTP status never determines a verdict.

The first supported predicate is approved-item deletion. Snapshots must be
captured by a probe; this module evaluates evidence, it does not fetch state.
"""
from copy import deepcopy
import json
import re
from urllib.parse import urlsplit

# Finding sources produced by the deterministic backend probe, not the LLM crew.
# These are authoritative: the rule is the app's own captured behavior (a replay
# of the recorded happy path), so complete before/action/after evidence is
# sufficient to CONFIRM without a separate user/specification reference.
_DETERMINISTIC_SOURCES = ('STATE_LOCK_PROBE',)


def load_report(path):
    """Join executor artifacts by stable ID, never by order or title."""
    data = json.loads(path.read_text())
    findings = data.setdefault('findings', [])
    artifacts = {}
    for artifact in sorted((path.parent / 'evidence').glob('*.json')):
        try:
            capture = json.loads(artifact.read_text())
            if capture.get('source') != 'VERIFIED_DELETE' or not capture.get('id'):
                continue
            artifacts.setdefault(capture['id'], []).append(capture)
        except (OSError, ValueError, AttributeError):
            continue
    # Deterministic backend-probe findings are authoritative and carry their
    # own complete before/action/after evidence. They must never be overwritten
    # by LLM crew evidence artifacts, even when the two share a finding ID
    # (the probe was historically numbered as the next F-NNN, which could
    # collide with a crew evidence/<id>.json). Guard by ID.
    _deterministic_ids = {f.get('id') for f in findings
                          if isinstance(f, dict) and f.get('source') in _DETERMINISTIC_SOURCES}
    for fid, captures in artifacts.items():
        if fid in _deterministic_ids:
            continue
        matches = [f for f in findings if f.get('id') == fid]
        if len(captures) != 1 or len(matches) > 1:
            for match in matches:
                match['verification'] = {'error': 'Ambiguous evidence ID; capture not selected.'}
            continue
        capture = captures[0]
        if matches:
            matches[0].update(capture)
        else:
            findings.append(capture)
        for result in data.get('results', []):
            if result.get('finding_id') == fid:
                result['verification'] = deepcopy(capture['verification'])
    return normalize_report(data)


def classify(record):
    v = record.get('verification')
    execution = record.get('execution') or (v.get('execution') if isinstance(v, dict) else None)
    if record.get('error_message') or (isinstance(v, dict) and v.get('error')):
        return 'CHECK_ERROR', 'State verification failed: ' + str(record.get('error_message') or v['error'])
    if isinstance(execution, dict) and execution.get('status') == 'NOT_EXECUTED':
        if isinstance(v, dict) and v.get('action'):
            return 'NEEDS_REVIEW', 'Execution status conflicts with captured action; review evidence.'
        reason = execution.get('missing_precondition')
        if isinstance(reason, str) and reason.strip():
            return 'NOT_EXECUTED', reason
        return 'NEEDS_REVIEW', 'Not-executed status lacks a missing precondition.'
    if not isinstance(v, dict):
        return ('CHECK_ERROR', 'Probe execution failed.') if record.get('error_message') or record.get('outcome') in ('ERROR', 'CHECK_ERROR') else ('NEEDS_REVIEW', 'No supported state verification recorded.')
    if v.get('error'):
        return 'CHECK_ERROR', 'State verification failed: ' + str(v['error'])
    if v.get('predicate') != 'approved_item_must_remain':
        return 'NEEDS_REVIEW', 'Unsupported verification predicate.'
    rule = v.get('rule', {})
    # A deterministic probe re-derives the rule from the app's own captured
    # behavior, so a self-referenced rule is legitimate and needs no external
    # confirmation. The LLM crew, by contrast, must cite a user/specification
    # rule before it can confirm.
    trusted_source = record.get('source') in _DETERMINISTIC_SOURCES
    if not trusted_source and (
            not isinstance(rule, dict) or rule.get('source') not in ('user', 'specification') or not rule.get('reference')):
        return 'NEEDS_REVIEW', 'The business rule needs an explicit user or specification reference.'
    before, after, action = (v.get(k) for k in ('before', 'after', 'action'))
    if not all(isinstance(x, dict) for x in (before, after, action)):
        return 'NEEDS_REVIEW', 'Before, action and after evidence are required.'
    for snapshot in (before, after):
        if snapshot.get('status_code') != 200 or snapshot.get('complete') is not True:
            return 'CHECK_ERROR', 'A complete successful state read is required; 404 is not deletion proof.'
        if not isinstance(snapshot.get('item_ids'), list) or not snapshot.get('request') or not snapshot.get('response'):
            return 'NEEDS_REVIEW', 'State snapshot lacks captured request/response or item IDs.'
    for key in ('resource_id', 'principal_id'):
        if not before.get(key) or before.get(key) != after.get(key) or before.get(key) != action.get(key):
            return 'NEEDS_REVIEW', 'Resource and authenticated principal must match across all observations.'
    times = [x.get('sequence') for x in (before, action, after)]
    if not all(type(t) is int for t in times) or not times[0] < times[1] < times[2]:
        return 'NEEDS_REVIEW', 'Evidence sequence is missing or inconsistent.'
    item = action.get('item_id')
    if before.get('state') != 'APPROVED' or after.get('state') != 'APPROVED' or item not in before['item_ids']:
        return 'NEEDS_REVIEW', 'Approved-state preconditions were not established or changed.'
    if action.get('method') != 'DELETE' or not action.get('request') or not action.get('response'):
        return 'NEEDS_REVIEW', 'Captured deletion action is required.'
    if item not in after['item_ids']:
        return 'CONFIRMED', 'Captured state reads show removal of an item while the resource remained approved.'
    return 'NOT_REPRODUCED', 'The item remained present in the captured post-action state.'


def _resource_path(value):
    """Normalize a resource identifier to a bare path, dropping the host and any
    trailing /<child-collection>/<id> so a parent and its child URL agree on the
    same key (e.g. /api/dashboard/1/parts/3 -> /api/dashboard/1)."""
    if not value:
        return None
    path = urlsplit(value).path if '://' in value else value
    # Strip one trailing /<collection>/<id> pair, but only when the remainder
    # still looks like a resource (ends in /<word>/<id>) — so a child URL
    # /api/dashboard/1/parts/3 -> /api/dashboard/1, while the parent
    # /api/dashboard/1 is left intact (its /dashboard/1 is the resource, not a child).
    m = re.match(r'^(.+)/\w+/\d+$', path)
    if m and re.search(r'/\w+/\d+$', m.group(1)):
        path = m.group(1)
    return path or None


def _dedup_key(finding):
    """A key identifying the same underlying bug across sources, or None if the
    finding can't be grouped (no CWE / no resolvable resource). Same primary CWE
    AND same target resource => same violation."""
    cwes = finding.get('cwe')
    if not isinstance(cwes, list) or not cwes:
        return None
    v = finding.get('verification')
    resource = _resource_path(v['before'].get('resource_id')) \
        if (isinstance(v, dict) and isinstance(v.get('before'), dict)) else None
    if not resource:
        resource = _resource_path(finding.get('url_tested'))
    if not resource:
        return None
    return (cwes[0], resource)


_DUP_RANK = {'CONFIRMED': 4, 'NEEDS_REVIEW': 3, 'NOT_REPRODUCED': 2, 'NOT_EXECUTED': 1, 'CHECK_ERROR': 0}


def _collapse_duplicate_findings(findings):
    """Collapse findings that describe the same violation (same primary CWE +
    same resource), keeping the strongest verdict and folding the rest into its
    `deduplicated_from` list. Findings that can't be keyed are always kept."""
    groups = {}
    for f in findings:
        k = _dedup_key(f)
        if k is not None:
            groups.setdefault(k, []).append(f)
    drop = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda f: -_DUP_RANK.get(f.get('verification_status'), 0))
        keep = group[0]
        keep.setdefault('deduplicated_from', []).extend(x.get('id') for x in group[1:])
        drop.update(x.get('id') for x in group[1:])
    return [f for f in findings if f.get('id') not in drop]


def normalize_report(report):
    """Return a copy; preserve raw artifacts and supplied IDs."""
    data = deepcopy(report)
    results = data.get('results') or []
    findings = data.get('findings')
    if not isinstance(findings, list):
        findings = [dict(r, title=r.get('script', 'Legacy finding'), source='MUTATION_SCRIPT', evidence=r.get('response_snippet') or '', cwe=[], severity='Not assessed') for r in results if r.get('outcome') == 'BUG_FOUND']
        findings += [dict(o, title=o.get('finding', 'Legacy observation'), source='ANALYSIS') for o in data.get('additional_observations', [])]
    for i, finding in enumerate(findings):
        finding.setdefault('id', f'F-{i+1:03}')
        linked = [r for r in results if r.get('finding_id') == finding['id']]
        if 'verification' not in finding and len(linked) == 1:
            finding['verification'] = deepcopy(linked[0].get('verification'))
            if 'execution' not in finding and linked[0].get('execution'):
                finding['execution'] = deepcopy(linked[0]['execution'])
            if linked[0].get('error_message'):
                finding['error_message'] = linked[0]['error_message']
        finding['verification_status'], finding['verification_reason'] = classify(finding)
        # The LLM crew can emit a bare non-dict verification (e.g. the string
        # "CONFIRMED"); classify() already degrades those to NEEDS_REVIEW. Guard
        # here so a malformed verification cannot crash the whole report render
        # (which would also hide a well-formed deterministic probe finding).
        v = finding.get('verification')
        if not isinstance(v, dict):
            v = {}
        if isinstance(v.get('execution'), dict) and 'execution' not in finding:
            finding['execution'] = deepcopy(v['execution'])
        captures = [v[k] for k in ('before', 'action', 'after') if isinstance(v.get(k), dict)]
        if captures:
            finding['evidence'] = {
                'summary': finding['verification_reason'],
                'requests': [c.get('request', {}) for c in captures],
                'responses': [c.get('response', {}) for c in captures],
            }
            finding['actual_behavior'] = finding['verification_reason']
    # Collapse multi-source duplicates of the same violation before counting, so the
    # findings list and summary reflect distinct bugs, not repeated reports of one.
    findings = _collapse_duplicate_findings(findings)
    for result in results:
        result.setdefault('original_outcome', result.get('outcome'))
        result['outcome'], result['verification_reason'] = classify(result)
    counts = {s: sum(f['verification_status'] == s for f in findings) for s in ('CONFIRMED', 'NEEDS_REVIEW', 'NOT_REPRODUCED', 'NOT_EXECUTED', 'CHECK_ERROR')}
    data['findings'], data['results'] = findings, results
    data['summary'] = {**(data.get('summary') or {}), 'reported_findings': len(findings),
        'bugs_found': counts['CONFIRMED'], 'confirmed': counts['CONFIRMED'],
        'needs_review': counts['NEEDS_REVIEW'], 'not_reproduced': counts['NOT_REPRODUCED'],
        'not_executed': counts['NOT_EXECUTED'] + sum(r['outcome'] == 'NOT_EXECUTED' and r.get('finding_id') not in {f['id'] for f in findings} for r in results),
        'rejected': sum(r['outcome'] == 'NOT_REPRODUCED' for r in results),
        'errors': sum(r['outcome'] == 'CHECK_ERROR' and r.get('finding_id') not in {f['id'] for f in findings} for r in results) + counts['CHECK_ERROR']}
    data['verification_schema_version'] = 2
    return data


def capture_deletion_check(read_state, delete_item, *, rule, resource_id, principal_id, item_id):
    """Adapter helper: read_state returns captured normalized state; delete_item
    returns captured request/response even for HTTP errors (do not raise on 500).
    Callbacks use the authorized app's endpoints and same authenticated session.
    """
    verification = {'predicate': 'approved_item_must_remain', 'rule': rule}
    try:
        before = read_state()
        verification['before'] = {**before, 'sequence': 1}
    except Exception as exc:
        verification['error'] = f'Before-state read failed: {type(exc).__name__}'
        return {'verification': verification}
    # Never mutate if the authorized scenario preconditions were not observed.
    if (before.get('status_code') != 200 or not isinstance(before.get('item_ids'), list)
            or before.get('resource_id') != resource_id or before.get('principal_id') != principal_id):
        verification['error'] = 'Before-state read is unsuccessful or inconsistent.'
        return {'verification': verification}
    if (before.get('complete') is not True or before.get('state') != 'APPROVED'
            or item_id not in before['item_ids']):
        verification['execution'] = {
            'status': 'NOT_EXECUTED',
            'missing_precondition': 'A complete collection containing the target item in APPROVED state is required.',
            'next_step': 'Prepare an authorized isolated approved resource containing the item, then rerun the check.'}
        return {'verification': verification}
    try:
        action = delete_item()
        verification['action'] = {**action, 'sequence': 2, 'method': 'DELETE',
            'resource_id': resource_id, 'principal_id': principal_id, 'item_id': item_id}
    except Exception as exc:
        verification['error'] = f'Action capture failed: {type(exc).__name__}'
    # Even a failed action may have changed state. Always attempt the read.
    try:
        verification['after'] = {**read_state(), 'sequence': 3}
    except Exception as exc:
        verification['error'] = f'After-state read failed: {type(exc).__name__}'
    return {'verification': verification}
