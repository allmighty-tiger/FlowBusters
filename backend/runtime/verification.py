"""Conservative report gate. HTTP status never determines a verdict.

The first supported predicate is approved-item deletion. Snapshots must be
captured by a probe; this module evaluates evidence, it does not fetch state.
"""
from copy import deepcopy
import json
import os
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
    normalized_cwes = {str(cwe).upper() for cwe in cwes}
    for key in ('mutation_type', 'source'):
        val = str(record.get(key) or '').lower()
        if ('auth' in val or 'credential' in val or 'login' in val
                or 'access control' in val or 'idor' in val or 'bac' in val
                or 'role_swap' in val or 'role swap' in val):
            return True
    url = str(record.get('url_tested') or '').lower()
    if any(tag in url for tag in _AUTH_URLS):
        return True
    # A business-logic finding may cite missing authentication as a secondary
    # contributing weakness (for example CWE-841 + CWE-306). Do not route that
    # whole finding through the auth verifier and discard its state evidence.
    return bool(normalized_cwes) and normalized_cwes.issubset(_AUTH_CWES)


def load_report(path):
    """Join executor artifacts by stable ID, never by order or title."""
    data = json.loads(path.read_text(encoding="utf-8"))
    # execute_run preserves the pre-execution report beside the working report.
    # A previous normalized write may contain only the findings that survived an
    # older presentation filter, so restore missing draft records in memory. The
    # current report wins for records it still contains and no artifact is
    # rewritten.
    current_findings = [finding for finding in data.get('findings', [])
                        if isinstance(finding, dict)]
    current_by_id = {finding.get('id'): finding for finding in current_findings
                     if finding.get('id')}
    for draft_path in sorted(path.parent.glob('agent-draft-*.json'),
                             key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            draft = json.loads(draft_path.read_text(encoding='utf-8'))
            draft_findings = draft.get('findings')
            if not isinstance(draft_findings, list):
                continue
            merged, seen = [], set()
            for finding in draft_findings:
                if not isinstance(finding, dict):
                    continue
                identifier = finding.get('id')
                merged.append(current_by_id.get(identifier, deepcopy(finding)))
                if identifier:
                    seen.add(identifier)
            merged.extend(finding for finding in current_findings
                          if not finding.get('id') or finding.get('id') not in seen)
            data['findings'] = merged
            break
        except (OSError, ValueError, TypeError):
            continue
    setup_paths = [value.strip() for value in
                   os.environ.get('SETUP_PATHS', '/api/demo/reset').split(',')
                   if value.strip()]
    for parent in path.parents:
        scope_path = parent / 'scope.json'
        if not scope_path.exists():
            continue
        try:
            scope = json.loads(scope_path.read_text(encoding='utf-8'))
            setup_paths = list(dict.fromkeys([*setup_paths, *(scope.get('setup_paths') or [])]))
        except (OSError, ValueError, TypeError):
            pass
        break
    data['_setup_paths'] = setup_paths
    run_dir = path.parent.parent.parent if path.parent.parent.name == 'reports' else path.parent
    candidates_path = run_dir / 'flows' / path.parent.name / 'cross_flow_candidates.json'
    try:
        candidates = json.loads(candidates_path.read_text(encoding='utf-8'))
        data['_cross_flow_candidates'] = candidates.get('candidates', [])
    except (OSError, ValueError, TypeError, AttributeError):
        data['_cross_flow_candidates'] = []
    findings = data.setdefault('findings', [])
    artifacts = {}
    for artifact in sorted((path.parent / 'evidence').glob('*.json')):
        try:
            capture = json.loads(artifact.read_text(encoding="utf-8"))
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
    from backend.runtime.probe_executor import reconcile
    # Production reports are gated using backend-signed receipts, including
    # legacy reports. Never accept provenance flags stored in agent JSON.
    root = next((parent.parent for parent in path.parents if parent.name == 'runs'), path.parent)
    return normalize_report(reconcile(data, path.parent, root))


def _is_setup_path_centered(finding, setup_paths):
    """True only when the claimed security behavior is the configured fixture.

    Setup calls commonly bracket real probes, so their presence in evidence is
    not enough to exclude a finding. The claim title or tested URL must identify
    the setup endpoint itself as the scenario under test.
    """
    if not isinstance(finding, dict):
        return False
    claim = ' '.join(str(finding.get(key) or '') for key in ('title', 'url_tested')).lower()
    for path in setup_paths:
        normalized = str(path).strip().lower()
        segments = [part for part in normalized.strip('/').split('/') if part]
        aliases = {normalized, normalized.strip('/'), normalized.strip('/').replace('/', '-')}
        if len(segments) >= 2:
            aliases.add('-'.join(segments[-2:]))
        if any(alias and alias in claim for alias in aliases):
            return True
    return False


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
    if record.get('_provenance_error'):
        v = record.get('verification')
        return ('CHECK_ERROR' if record.get('error_message') or isinstance(v, dict) and v.get('error') else 'NEEDS_REVIEW', record['_provenance_error'])
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
    if isinstance(v, dict) and v.get('predicate') == 'business_rule_must_hold':
        return _classify_business_rule(record, v)
    if not isinstance(v, dict):
        outcome = str(record.get('original_outcome') or record.get('outcome') or '').upper()
        if outcome in ('ERROR', 'CHECK_ERROR'):
            return 'CHECK_ERROR', 'Probe execution failed.'
        if outcome in ('REJECTED', 'NOT_REPRODUCED'):
            return 'NOT_REPRODUCED', 'The executed probe did not reproduce the suspected violation.'
        return 'NEEDS_REVIEW', 'No supported state verification recorded.'
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


def _classify_business_rule(record, verification):
    """Validate a generic state-transition probe without relying on HTTP status.

    The probe must cite a rule observed in the UI/specification, capture complete
    before and after state reads, record every action, and explicitly describe
    whether the invariant was violated. This supports workflows such as
    refund-pending -> cancel -> complete without hard-coding one application.
    """
    before, after = verification.get('before'), verification.get('after')
    actions = verification.get('actions')
    if not isinstance(before, dict) or not isinstance(after, dict) or not isinstance(actions, list) or not actions:
        return 'NEEDS_REVIEW', 'Complete before, actions and after evidence are required.'
    captures = [before, *actions, after]
    sequences = [capture.get('sequence') for capture in captures]
    if not all(type(value) is int for value in sequences) or sequences != sorted(sequences) or len(set(sequences)) != len(sequences):
        return 'NEEDS_REVIEW', 'Evidence sequence is missing or inconsistent.'
    for snapshot in (before, after):
        if snapshot.get('status_code') != 200 or snapshot.get('complete') is not True:
            return 'CHECK_ERROR', 'Complete successful before and after state reads are required.'
        if not snapshot.get('request') or not snapshot.get('response'):
            return 'NEEDS_REVIEW', 'State snapshots must include captured requests and responses.'
    for action in actions:
        if not isinstance(action, dict) or not action.get('request') or not action.get('response'):
            return 'NEEDS_REVIEW', 'Every tested action must include its captured request and response.'
    violation = verification.get('violation')
    if not isinstance(violation, dict) or type(violation.get('observed')) is not bool:
        return 'NEEDS_REVIEW', 'A structured invariant-violation result is required.'
    description = str(violation.get('description') or '').strip()
    if not description:
        return 'NEEDS_REVIEW', 'The observed invariant result needs a concrete description.'
    # A complete authenticated negative execution is conclusive about this
    # attempted scenario even when the proposed rule came from weak analysis.
    # Rule provenance gates promotion of a violation, not recognition that the
    # control held and the invariant remained within bounds.
    if not violation['observed']:
        return 'NOT_REPRODUCED', description
    rule = verification.get('rule')
    if not isinstance(rule, dict):
        return 'NEEDS_REVIEW', 'The business rule needs verified provenance.'
    source = rule.get('source')
    if source in ('user', 'specification') and str(rule.get('reference') or '').strip():
        return 'CONFIRMED', description
    if source == 'observed_ui':
        if record.get('_rule_provenance_valid') is True:
            return 'CONFIRMED', description
        detail = str(record.get('_rule_provenance_error') or '').strip()
        reason = 'Observed-UI rule provenance did not validate against immutable source artifacts.'
        return 'NEEDS_REVIEW', reason + (f' {detail}' if detail else '')
    if source in ('api_state', 'agent_inference'):
        return 'NEEDS_REVIEW', 'API state and agent inference cannot serve as observed-UI business-rule provenance.'
    return 'NEEDS_REVIEW', 'The business rule needs a user, specification, or validated observed-UI reference.'


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
    """Identify an equivalent executed chain and invariant.

    CWE, resource, or similar prose is never sufficient: separate request bodies,
    action ordering, or evaluated invariants represent distinct checks.
    """
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
    verification = finding.get('verification')
    if not isinstance(verification, dict):
        return None
    actions = verification.get('actions')
    if not isinstance(actions, list):
        actions = [verification.get('action')] if isinstance(verification.get('action'), dict) else []
    if not actions or not isinstance(verification.get('invariant'), dict):
        return None
    action_chain = []
    for capture in actions:
        if not isinstance(capture, dict) or not isinstance(capture.get('request'), dict):
            return None
        request = capture['request']
        action_chain.append((
            str(request.get('method') or '').upper(),
            _resource_path(request.get('url')) or str(request.get('url') or ''),
            json.dumps(request.get('body'), sort_keys=True, separators=(',', ':'), default=str),
        ))
    invariant = json.dumps(verification['invariant'], sort_keys=True,
                           separators=(',', ':'), default=str)
    return (cwes[0], resource, tuple(action_chain), invariant)


_DUP_RANK = {'CONFIRMED': 4, 'NEEDS_REVIEW': 3, 'NOT_REPRODUCED': 2, 'NOT_EXECUTED': 1, 'CHECK_ERROR': 0}


def _collapse_duplicate_findings(findings):
    """Collapse only equivalent executed action chains and invariants."""
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
    execution_summary = data.pop('_execution_summary', None)
    setup_paths = data.pop('_setup_paths', [])
    partial_coverage = data.pop('_partial_coverage', [])
    data.pop('_cross_flow_candidates', None)
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
        actions = v.get('actions') if isinstance(v.get('actions'), list) else [v.get('action')]
        captures = [c for c in [v.get('before'), *actions, v.get('after')] if isinstance(c, dict)]
        if captures:
            finding['evidence'] = {
                'summary': finding['verification_reason'],
                'requests': [c.get('request', {}) for c in captures],
                'responses': [c.get('response', {}) for c in captures],
            }
            finding['actual_behavior'] = finding['verification_reason']
    excluded_findings = [finding for finding in findings
                         if _is_setup_path_centered(finding, setup_paths)]
    security_findings = [finding for finding in findings if finding not in excluded_findings]
    # Collapse only equivalent primary checks before counting security findings.
    findings = _collapse_duplicate_findings(security_findings)
    all_finding_by_id = {finding.get('id'): finding for finding in [*findings, *excluded_findings]
                         if finding.get('id')}
    for result in results:
        result.setdefault('original_outcome', result.get('outcome'))
        linked_finding = all_finding_by_id.get(result.get('finding_id'))
        original = str(result.get('original_outcome') or '').upper()
        if (linked_finding and original == 'CONFIRMED'
                and linked_finding.get('verification_status') == 'CONFIRMED'):
            result['outcome'] = 'CONFIRMED'
            result['verification_reason'] = linked_finding.get('verification_reason')
        else:
            result['outcome'], result['verification_reason'] = classify(result)
        if linked_finding in excluded_findings:
            result['presentation_status'] = 'EXCLUDED_SETUP_PATH'
            result['presentation_reason'] = ('Excluded setup-path scenario: the central tested behavior '
                                             'is authorized fixture setup, not application attack surface.')
    counts = {s: sum(f['verification_status'] == s for f in findings) for s in ('CONFIRMED', 'NEEDS_REVIEW', 'NOT_REPRODUCED', 'NOT_EXECUTED', 'CHECK_ERROR')}
    excluded_setup = [{
        'id': finding.get('id'),
        'title': finding.get('title'),
        'script': finding.get('script'),
        'execution_id': finding.get('execution_id'),
        'status': 'EXCLUDED_SETUP_PATH',
        'reason': ('Excluded setup-path scenario: /api/demo/reset is authorized fixture setup, '
                   'not application attack surface.'),
    } for finding in excluded_findings]
    data['findings'], data['results'] = findings, results
    data['partial_coverage'] = partial_coverage
    data['excluded_setup_executions'] = excluded_setup
    deduplicated_ids = {
        identifier for finding in findings for identifier in finding.get('deduplicated_from', [])
        if identifier
    }
    controls_held = sum(result.get('outcome') == 'NOT_REPRODUCED' for result in results)
    visible_control_executions = {
        finding.get('execution_id') for finding in findings
        if finding.get('verification_status') == 'NOT_REPRODUCED'
        and finding.get('execution_id')
    }
    unrepresented_controls = sum(
        result.get('outcome') == 'NOT_REPRODUCED'
        and result.get('execution_id') not in visible_control_executions
        for result in results)
    # Some agent drafts already collapse findings before backend loading and
    # preserve only deduplicated_from IDs, not a finding_id on the corresponding
    # result row. Count only the overlap supported by both facts: an explicit
    # deduplication ID and a completed negative execution not represented by a
    # visible control finding. Do not guess which ID maps to which script.
    deduplicated_results = min(len(deduplicated_ids), unrepresented_controls)
    if not isinstance(execution_summary, dict) or execution_summary.get('derived') is not True:
        # normalize_report is also used by isolated unit-level callers. Never
        # reuse agent summary values; absent a receipt reconciliation, expose
        # only the normalized rows available to this trusted caller.
        execution_summary = {
            'planned_executions': 0,
            'execution_attempts': len(results),
            'completed_executions': 0,
            'pending_execution': 0,
            'missing_receipts': 0,
            'execution_errors': sum(result.get('outcome') == 'CHECK_ERROR' for result in results),
            'trace_mismatches': 0,
            'untrusted_receipts': 0,
        }
    data['summary'] = {'reported_findings': len(findings), 'finding_count': len(findings),
        'bugs_found': counts['CONFIRMED'], 'confirmed': counts['CONFIRMED'],
        'critical_findings': sum(f['verification_status'] == 'CONFIRMED'
                                 and str(f.get('severity') or '').lower() == 'critical'
                                 for f in findings),
        'potential_critical_findings': sum(f['verification_status'] == 'NEEDS_REVIEW'
                                           and str(f.get('severity') or '').lower() == 'critical'
                                           for f in findings),
        'needs_review': counts['NEEDS_REVIEW'], 'not_reproduced': counts['NOT_REPRODUCED'],
        'not_executed': counts['NOT_EXECUTED'] + sum(r['outcome'] == 'NOT_EXECUTED' and r.get('finding_id') not in {f['id'] for f in findings} for r in results),
        'rejected': controls_held, 'controls_held': controls_held,
        'deduplicated_findings': len(deduplicated_ids),
        'deduplicated_execution_results': deduplicated_results,
        'deduplicated_primary_chains': len(deduplicated_ids),
        'partial_coverage': len(partial_coverage),
        'excluded_setup_executions': len(excluded_setup),
        'unlinked_execution_results': sum(not result.get('finding_id') for result in results),
        'execution_result_counts': {
            status.lower(): sum(result.get('outcome') == status for result in results)
            for status in ('CONFIRMED', 'NEEDS_REVIEW', 'NOT_REPRODUCED', 'NOT_EXECUTED', 'CHECK_ERROR')
        },
        'errors': sum(r['outcome'] == 'CHECK_ERROR' and r.get('finding_id') not in {f['id'] for f in findings} for r in results) + counts['CHECK_ERROR'],
        **{key: execution_summary.get(key, 0) for key in (
            'planned_executions', 'execution_attempts', 'completed_executions',
            'pending_execution', 'missing_receipts', 'execution_errors',
            'trace_mismatches', 'untrusted_receipts')},
    }
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
