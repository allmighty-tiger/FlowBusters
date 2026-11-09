"""Compile AI-selected atomic plans before execution; never edit executed evidence."""
import ast
import base64
import json
import math
from pathlib import Path
from urllib.parse import urlsplit


def read_plan(script, *, validate=True):
    tree = ast.parse(Path(script).read_text(encoding='utf-8'))
    plans = [n.value for n in tree.body if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == 'PROBE_PLAN' for t in n.targets)]
    if not plans:
        return None
    if len(plans) != 1:
        raise ValueError('Exactly one PROBE_PLAN is required')
    plan = ast.literal_eval(plans[0])
    if validate:
        validate_plan(plan)
    return plan


def validate_plan(plan):
    if not isinstance(plan, dict) or plan.get('schema_version') not in (1, 2):
        raise ValueError('PROBE_PLAN schema_version must be 1 or 2')
    if plan.get('schema_version') == 2:
        from backend.runtime.probe_contract import validate_combination
        validate_combination(plan)
    if not isinstance(plan.get('title'), str) or not plan['title'].strip():
        raise ValueError('PROBE_PLAN needs a claim title')
    origin = urlsplit(str(plan.get('origin', '')))
    if (origin.scheme not in ('http', 'https') or not origin.netloc or origin.path
            or origin.query or origin.fragment or origin.username or origin.password):
        raise ValueError('PROBE_PLAN needs an exact origin without a path')
    for key in ('setup', 'state_read'):
        if key == 'setup' and plan.get(key) is None:
            continue
        if (not isinstance(plan.get(key), str) or not plan[key].startswith('/')
                or plan[key].startswith('//')):
            raise ValueError('PROBE_PLAN needs setup and state_read root paths')
    actions = plan.get('actions')
    if not isinstance(actions, list) or not actions:
        raise ValueError('PROBE_PLAN needs ordered actions')
    for action in actions:
        if (not isinstance(action, dict) or action.get('method') not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')
                or not isinstance(action.get('path'), str) or not action['path'].startswith('/')
                or action['path'].startswith('//') or 'body' not in action):
            raise ValueError('Each action needs method, root path and explicit body')
        if action['path'] == plan['setup']:
            raise ValueError('One atomic chain per plan: setup cannot split the actions')
    predicate = plan.get('predicate')
    if predicate == 'business_rule_must_hold':
        if plan.get('claim_type') != 'amount_bound':
            raise ValueError('Only amount_bound claims can use the numeric sum_lte check')
        invariant = plan.get('invariant', {})
        if not isinstance(invariant, dict) or invariant.get('operator') != 'sum_lte':
            raise ValueError('A supported executable invariant is required')
        paths = invariant.get('terms')
        if not isinstance(paths, list) or not paths:
            raise ValueError('Invariant terms are required')
        for path in [*paths, invariant.get('limit')]:
            if not isinstance(path, list) or not path or not all(isinstance(k, str) and k for k in path):
                raise ValueError('Invariant paths must start at the response root')
    elif predicate != 'unsupported_business_rule' or not str(plan.get('unsupported_reason', '')).strip():
        raise ValueError('Unsupported claims require unsupported_business_rule and a concrete reason')
    if predicate == 'business_rule_must_hold' or plan.get('inferred_invariant') is not None:
        from backend.runtime.inferred_invariants import validate_inference_shape
        inferred, _ = validate_inference_shape(plan)
        cursor = 0
        required = inferred['required_actions']
        for action in actions:
            if cursor < len(required) and all(action[k] == required[cursor][k] for k in ('method', 'path')):
                cursor += 1
        if cursor != len(required):
            raise ValueError('Plan omits a required action from its inferred invariant')


def binding_contract(plan):
    validate_plan(plan)
    return {key: plan.get(key) for key in
            ('origin', 'setup', 'state_read', 'actions', 'predicate', 'invariant', 'unsupported_reason', 'claim_type')}


def render_plan(plan):
    validate_plan(plan)
    return 'PROBE_PLAN = ' + repr(plan) + '\n' + '''
import json, math, httpx
def numeric(body, path):
    for key in path:
        body = body[key]
    if type(body) not in (int, float) or not math.isfinite(body):
        raise ValueError('Non-finite/non-numeric AFTER operand: ' + repr(path))
    return body
with httpx.Client(timeout=30, follow_redirects=False) as client:
    def capture(method, path, body=None):
        response = client.request(method, PROBE_PLAN['origin'] + path,
                                  **({'json': body} if body is not None else {}))
        try:
            value = response.json()
        except ValueError:
            value = response.text
        return {'sequence': response.extensions['flowbusters_sequence'],
                'status_code': response.status_code, 'complete': True,
                'request': {'method': method, 'url': str(response.request.url), 'body': body},
                'response': value}
    setup = []
    if PROBE_PLAN.get('setup'):
        setup = [capture('POST', PROBE_PLAN['setup'], {})]
        if setup[0]['status_code'] != 200:
            raise ValueError('Authorized setup failed')
    before = capture('GET', PROBE_PLAN['state_read'])
    actions = [capture(a['method'], a['path'], a['body']) for a in PROBE_PLAN['actions']]
    after = capture('GET', PROBE_PLAN['state_read'])
    verification = {'predicate': PROBE_PLAN['predicate'], 'setup': setup,
        'before': before, 'actions': actions, 'after': after,
        'rule': {'source': 'agent_inference', 'reference': 'AI-proposed test condition; business meaning requires user review'}}
    verification['rule_origin'] = 'AI_INFERRED'
    verification['inferred_invariant'] = PROBE_PLAN.get('inferred_invariant')
    verification['supporting_observations'] = PROBE_PLAN.get('supporting_observations', [])
    if PROBE_PLAN.get('missing_evidence'):
        verification['missing_evidence'] = PROBE_PLAN['missing_evidence']
    if PROBE_PLAN['predicate'] == 'business_rule_must_hold':
        invariant = PROBE_PLAN['invariant']
        total = sum(numeric(after['response'], p) for p in invariant['terms'])
        maximum = numeric(after['response'], invariant['limit'])
        verification.update(invariant=invariant, violation={'observed': total > maximum,
            'description': f'Final GET: {total} > {maximum} = {total > maximum}'})
    else:
        verification['unsupported_reason'] = PROBE_PLAN['unsupported_reason']
    print(json.dumps({'title': PROBE_PLAN['title'], 'mutation_type':
        'FINANCIAL_BOUND' if PROBE_PLAN['claim_type'] == 'amount_bound' else 'UNSUPPORTED_CLAIM',
        'verification': verification}))
'''


def compile_plans(run, flow, *, skip_invalid=False):
    run = Path(run)
    if list((run / 'reports').glob('*/executions/*.json')):
        raise ValueError('Probe compilation refused after execution')
    for script in sorted((run / 'mutations' / flow).glob('*.py')):
        try:
            plan = read_plan(script)
        except (ValueError, SyntaxError, TypeError, KeyError):
            if skip_invalid:
                continue  # Executor records the per-probe preflight rejection.
            raise
        if plan is None:
            continue  # Legacy programs retain the strict existing execution gate.
        rendered = render_plan(plan)
        if script.read_text(encoding='utf-8') == rendered:
            continue
        tree = ast.parse(script.read_text(encoding='utf-8'))
        statements = list(tree.body)
        if (statements and isinstance(statements[0], ast.Expr)
                and isinstance(statements[0].value, ast.Constant)
                and isinstance(statements[0].value.value, str)):
            statements.pop(0)  # A descriptive module docstring is not executable logic.
        if len(statements) != 1 or not isinstance(statements[0], ast.Assign):
            raise ValueError(f'{script.name}: write only PROBE_PLAN; custom code cannot be silently discarded')
        archive = run / 'generated_probe_drafts'; archive.mkdir(exist_ok=True)
        saved = archive / script.name
        if not saved.exists():
            saved.write_bytes(script.read_bytes())
        script.write_text(rendered, encoding='utf-8')


def validate_plan_set(scripts, catalog, *, run=None, root=None):
    """Validate the whole queue, including isolation, before any request is sent."""
    from backend.runtime.endpoint_catalog import preflight_endpoints
    errors = []
    for script in scripts:
        try:
            plan = read_plan(script)
            error = preflight_endpoints(script, catalog)
            if not error and plan is not None and not plan.get('setup'):
                error = ('Missing independent starting state: every mutation plan requires an explicitly '
                         'configured setup endpoint; removing setup would share mutated state.')
            if not error and plan is not None and plan.get('inferred_invariant') is not None and run is not None:
                from backend.runtime.inferred_invariants import validate_support
                validate_support(plan, run, root)
                validate_recorded_operand_paths(plan, run)
        except (ValueError, SyntaxError, TypeError, KeyError) as exc:
            error = str(exc)
        if error:
            errors.append({'script': script.name, 'error': error})
    return errors


def validate_recorded_operand_paths(plan, run):
    """Reject invented response-root numeric paths, never guess a replacement.

    Called only after source authentication and observation-pointer validation.
    This checks recorded schema, not whether the future attack will violate it.
    Runtime still validates and recomputes against the actual AFTER response.
    """
    if plan.get('predicate') != 'business_rule_must_hold':
        return
    run = Path(run)
    cross = (run / 'cross_flow_inputs.json').exists()
    bodies = []
    for source in {o['source_run'] for o in plan['supporting_observations']}:
        folder = run / 'cross_flow_sources' / source if cross else run / 'flows' / run.name
        har = json.loads((folder / 'recording.har').read_text(encoding='utf-8'))
        for entry in har['log']['entries']:
            content = entry['response'].get('content') or {}
            raw = content.get('text', '')
            try:
                if content.get('encoding') == 'base64':
                    raw = base64.b64decode(raw, validate=True).decode('utf-8')
                bodies.append(json.loads(raw))
            except (ValueError, TypeError, UnicodeError):
                continue
    invariant = plan['invariant']
    for path in [*invariant['terms'], invariant['limit']]:
        found = False
        for body in bodies:
            value = body
            for key in path:
                if not isinstance(value, dict) or key not in value:
                    value = None
                    break
                value = value[key]
            if type(value) in (int, float) and math.isfinite(value):
                found = True
                break
        if not found:
            raise ValueError('Invariant operand path has no numeric evidence in the selected HAR: '
                             + '.'.join(path) + '. Use exact response-root paths; do not guess nesting.')
