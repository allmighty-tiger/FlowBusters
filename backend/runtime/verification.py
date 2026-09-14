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

# Credential/authorization / broken-access-control findings (auth bypass, weak or
# empty credentials, token-acceptance, IDOR/BAC). The crew labels these
# inconsistently (source is free text, e.g. "AUTH_CHECK" or
# "auth_check + script re-execution"), so we detect them robustly by CWE,
# mutation_type, url or source — never by exact source match.
_AUTH_CWES = ('CWE-287', 'CWE-288', 'CWE-300', 'CWE-306', 'CWE-352', 'CWE-862', 'CWE-863', 'CWE-639')
_AUTH_URLS = ('/login', '/auth', '/signin', '/sign-in', '/token', '/session')


def _is_auth_finding(record):
    """True for credential / broken-access-control findings, detected robustly
    because the LLM crew emits a free-text source. Match on CWE, mutation_type,
    source, or the tested URL."""
    cwes = record.get('cwe') or []
    if not isinstance(cwes, (list, tuple, set)):
        cwes = [cwes]  # tolerate a single CWE given as a scalar
    for cwe in cwes:
        if str(cwe).upper() in _AUTH_CWES:
            return True
    for key in ('mutation_type', 'source'):
        val = str(record.get(key) or '').lower()
        if ('auth' in val or 'credential' in val or 'login' in val
                or 'access control' in val or 'idor' in val or 'bac' in val
                or 'role_swap' in val or 'role swap' in val):
            return True
    url = str(record.get('url_tested') or '').lower()
    if any(tag in url for tag in _AUTH_URLS):
        return True
    return False


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


_TOKEN_KEYS = ('token', 'access_token', 'session_token', 'session', 'jwt', 'auth_token')


def _is_credential_response(resp):
    """True if a captured response carries a usable credential (a token/session
    value). A login that issues one for the supplied credentials is the core of
    an auth bypass, regardless of 200 vs 201."""
    if not isinstance(resp, dict):
        return False
    body = resp.get('response_body', resp)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (ValueError, TypeError):
            return False
    if not isinstance(body, dict):
        return False
    if any(k in body for k in _TOKEN_KEYS):
        return True
    return any('token' in str(k).lower() and val for k, val in body.items())


_REREAD_KEYS = ('independent_state_read', 'state_read', 're_read', 'recheck', 'verification_read', 'post_read')
# Structured keys whose truthy value demonstrates a real effect (not a bare 200).
_SIGNAL_KEYS = ('object_gone', 'state_changed', 'item_removed', 'actually_changed', 'changed', 'delta', 'before_after_delta')


def _state_change_signal(value):
    """Detect a demonstrated state change in an evidence blob (dict/str). Returns a
    short reason string or None. Scans explicitly for re-read fields and for
    structured effect flags (object_gone: true, before/after delta, ...), so the
    check is format-agnostic rather than keyed on one field name the LLM may
    rename. A bare 200 / prose summary without an effect is NOT a signal."""
    if isinstance(value, dict):
        for k in _REREAD_KEYS:
            val = value.get(k)
            if (isinstance(val, str) and val.strip()) or (isinstance(val, dict) and val):
                return 'independent re-read: %s' % (val.strip()[:200] if isinstance(val, str) else 'present')
        for k in _SIGNAL_KEYS:
            if value.get(k) in (True, 'true', 1, '1'):
                return 'demonstrated effect (%s=true)' % k
        # A before/after pair whose item sets or state differ is a real change.
        before, after = value.get('before'), value.get('after')
        if isinstance(before, dict) and isinstance(after, dict):
            if before.get('item_ids') != after.get('item_ids') or before.get('state') != after.get('state'):
                return 'before/after state differs'
        return None
    if isinstance(value, str) and value.strip():
        # A string that is really JSON (e.g. a probe's response_snippet) may carry
        # structured effect flags — parse and re-check it as a dict.
        s = value.strip()
        if s[0] in '{[':
            try:
                return _state_change_signal(json.loads(s))
            except (ValueError, TypeError):
                pass
        # Otherwise a prose re-read mentions a re-read explicitly.
        for k in _REREAD_KEYS:
            if k in value:
                return 'independent re-read present'
        return None
    return None


def _confirmed_by_crew(record, linked_results=()):
    """CONFIRMED when the crew self-verified (verdict CONFIRMED) AND documented a
    real state change / independent re-read — in the finding's evidence OR in the
    evidence of a probe it linked (a finding may delegate its proof to the script
    that demonstrated it). Returns (True, reason) or (False, None). A bare token/200
    with no demonstrable effect does NOT confirm."""
    if str(record.get('verdict') or '').strip().upper() != 'CONFIRMED':
        return False, None
    # 1) The finding's own evidence.
    ev = record.get('evidence')
    if isinstance(ev, dict):
        sig = _state_change_signal(ev)
        if sig:
            return True, 'Crew verdict CONFIRMED; %s.' % sig
    # 2) The linked probe result's output (where the crew often records the proof).
    for r in linked_results:
        if not isinstance(r, dict):
            continue
        for field in ('response_snippet', 'evidence'):
            sig = _state_change_signal(r.get(field))
            if sig:
                script = (r.get('script') or 'linked probe')
                return True, 'Crew verdict CONFIRMED; demonstrated by %s (%s).' % (script, sig)
    return False, None


def _classify_auth(record, linked_results=()):
    """Verify a credential / broken-access-control finding. Either of these
    reaches CONFIRMED:
      1. The crew self-verified (verdict CONFIRMED) AND demonstrated a real state
         change / independent re-read — in the finding's evidence or the linked
         probe that proved it.
      2. The captured request/response trail shows a login that issued a usable
         credential (a recompute path independent of the crew's own verdict).
    Otherwise the trail is insufficient -> NEEDS_REVIEW.
    """
    ok, reason = _confirmed_by_crew(record, linked_results)
    if ok:
        return 'CONFIRMED', reason

    requests, responses = [], []
    ev = record.get('evidence')
    if isinstance(ev, dict):
        requests = ev.get('requests') or []
        responses = ev.get('responses') or []
    v = record.get('verification')
    if not (requests or responses) and isinstance(v, dict):
        requests = requests or (v.get('requests') or [])
        responses = responses or (v.get('responses') or [])

    by_label = {}
    for resp in responses:
        if isinstance(resp, dict) and resp.get('label') is not None:
            by_label.setdefault(resp['label'], resp)

    for req in requests:
        if not isinstance(req, dict):
            continue
        method = str(req.get('method', '')).upper()
        url = str(req.get('url', '')).lower()
        # An authorize step is a POST to a login/auth/token endpoint.
        if method != 'POST' or not any(tag in url for tag in ('login', 'auth', 'signin', 'sign-in', 'token', 'session')):
            continue
        resp = by_label.get(req.get('label'))
        if resp is None or not _is_credential_response(resp):
            continue
        sc = int(resp.get('status_code', 0) or 0)
        if not 200 <= sc < 300:
            continue
        # Impact: did a subsequent protected/state-changing call succeed with it?
        impact = None
        for r in responses:
            if not isinstance(r, dict) or r is resp:
                continue
            if not 200 <= int(r.get('status_code', 0) or 0) < 300:
                continue
            rl = str(r.get('label', '')).lower()
            if any(tag in rl for tag in ('dashboard', 'reset', 'update', 'delete', 'browse', 'read', 'protected', 'state', 'orders')):
                impact = r.get('label')
                break
        return ('CONFIRMED',
                'Captured login accepted the supplied credentials and issued a usable session token (HTTP %s); token was accepted by %s.' %
                (sc, (impact or 'a subsequent request')))

    return ('NEEDS_REVIEW',
            'Authentication/authorization finding has no crew-independent re-read and no captured login trail showing a usable credential; review manually.')


def classify(record, linked_results=()):
    v = record.get('verification')
    execution = record.get('execution') or (v.get('execution') if isinstance(v, dict) else None)
    if record.get('error_message') or (isinstance(v, dict) and v.get('error')):
        return 'CHECK_ERROR', 'State verification failed: ' + str(record.get('error_message') or v['error'])
    # Credential/authorization (and broken-access-control) findings verify from
    # a re-read / request-response trail rather than the state-removal
    # predicate. Error cases above still take precedence; everything else for an
    # auth finding routes to its own path.
    if _is_auth_finding(record):
        return _classify_auth(record, linked_results)
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
    if not isinstance(value, str):
        return None  # a bare resource id (int/float) is not a URL path
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
        finding['verification_status'], finding['verification_reason'] = classify(finding, linked)
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
