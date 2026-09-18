"""Versioned backend test inputs, not preset results or invented provenance.

Only authenticated application identity plus validated recorded operations can
select a plan. Agent-generated probes are retained and use the same executor.
"""
import json
import ast
import base64
from pathlib import Path
from urllib.parse import urlsplit

from backend.runtime.application_identity import validate_identity
from backend.runtime.business_requirements import load_business_requirements


PLAN_PATH = Path(__file__).with_name('coverage_plans.json')


def receipt_probe_origin(receipt):
    """Use authenticated receipt metadata; legacy names alone prove nothing."""
    if receipt.get('source') in ('BACKEND_COVERAGE', 'VERIFICATION_PROBE'):
        return receipt['source']
    if receipt.get('source') != 'MUTATION_SCRIPT':
        return 'UNKNOWN'
    try:
        code = base64.b64decode(receipt['script_source_base64'], validate=True).decode('utf-8').replace('\r\n', '\n')
        first = ast.parse(code).body[0]
        config = ast.literal_eval(first.value) if isinstance(first, ast.Assign) else None
        profiles = json.loads(PLAN_PATH.read_text(encoding='utf-8'))['profiles']
        matches_profile = isinstance(config, dict) and any(
            config.get('setup') == profile['setup'] and config.get('state_read') == profile['state_read']
            and config.get('actions') == scenario['actions']
            and config.get('name') == scenario['id'].replace('_', ' ')
            and receipt.get('script') == '00_coverage_' + scenario['id'] + '.py'
            and any(rule['id'] == profile['requirement_id'] and rule['predicate'] == config.get('invariant')
                    for rule in load_business_requirements())
            for profile in profiles for scenario in profile['scenarios'])
        if matches_profile and code == render_probe(**config):
            # Old receipts did not record generation authority. Identify the
            # exact saved template, without pretending that proves authorship.
            return 'LEGACY_COVERAGE_TEMPLATE'
    except (KeyError, ValueError, TypeError, SyntaxError, UnicodeError):
        pass
    return 'AI_PROBE'


def render_probe(origin, setup, state_read, actions, invariant, name):
    """One reset, one atomic scenario, own final GET; no expected verdict."""
    config = dict(origin=origin, setup=setup, state_read=state_read,
                  actions=actions, invariant=invariant, name=name)
    return 'CONFIG = ' + repr(config) + '\n' + '''
import json
import math
import httpx

def numeric(value, path):
    for key in path:
        value = value[key]
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Non-numeric invariant operand: ' + repr(path))
    return value

with httpx.Client(timeout=15, follow_redirects=False) as client:
    def capture(method, path, body=None):
        response = client.request(method, CONFIG['origin'] + path,
                                  **({'json': body} if body is not None else {}))
        return {'sequence': response.extensions['flowbusters_sequence'],
                'complete': True, 'status_code': response.status_code,
                'request': {'method': method, 'url': str(response.request.url), 'body': body},
                'response': response.json()}
    setup = capture('POST', CONFIG['setup'], {})
    if setup['status_code'] != 200:
        raise ValueError('Authorized fixture reset failed')
    before = capture('GET', CONFIG['state_read'])
    actions = [capture('POST', action['endpoint'], action['body']) for action in CONFIG['actions']]
    after = capture('GET', CONFIG['state_read'])
    invariant = CONFIG['invariant']
    total = sum(numeric(after['response'], path) for path in invariant['terms'])
    limit = numeric(after['response'], invariant['limit'])
    verification = {'predicate': 'business_rule_must_hold',
        'rule': {'source': 'agent_inference', 'reference': 'Resolve independent backend requirement on report load'},
        'setup': [setup], 'before': before, 'actions': actions, 'after': after,
        'invariant': invariant,
        'violation': {'observed': total > limit, 'description': f'Final state: {total} > {limit} = {total > limit}'}}
    print(json.dumps({'title': CONFIG['name'], 'mutation_type': 'FINANCIAL_BOUND',
                      'verification': verification}))
'''


def prepare_coverage_probes(run_dir, flow, root):
    """Called only after the state-map gate, before script counts/execution."""
    run_dir = Path(run_dir)
    identity = validate_identity(run_dir, root)
    if not identity:
        return []
    profiles = json.loads(PLAN_PATH.read_text(encoding='utf-8'))['profiles']
    profile = next((item for item in profiles if all(item[key] == identity[key]
        for key in ('application_id', 'identity_version', 'requirements_version'))), None)
    if profile is None:
        return []
    requirements = load_business_requirements()
    requirement = next((item for item in requirements if item['id'] == profile['requirement_id']
        and item['application_id'] == identity['application_id']
        and identity['identity_version'] in item['identity_versions']
        and item['requirements_version'] == identity['requirements_version']), None)
    if requirement is None:
        raise ValueError('Coverage plan has no applicable independent product requirement')
    if (run_dir / 'cross_flow_inputs.json').exists():
        from backend.runtime.ui_provenance import validate_cross_flow_manifest, _verify_cross_flow_snapshot
        manifest = validate_cross_flow_manifest(run_dir, root=root)
        selected_identity = {key: identity[key] for key in
                             ('application_id', 'identity_version', 'requirements_version', 'product')}
        for item in manifest['sources']:
            if item.get('application_identity') != selected_identity:
                raise ValueError('Coverage source identity differs from cross-flow identity')
            _verify_cross_flow_snapshot(run_dir, item['run_id'], run_dir / item['path'], root=root)
        recordings = [(item['run_id'], json.loads((run_dir / item['path'] / 'recording.har')
                       .read_text(encoding='utf-8'))) for item in manifest['sources']]
        mode = 'cross_flow'
    else:
        from backend.runtime.endpoint_catalog import validate_recorded_input
        validate_recorded_input(run_dir, flow, root)
        recordings = [(flow, json.loads((run_dir / 'flows' / flow / 'recording.har')
                      .read_text(encoding='utf-8')))]
        mode = 'recorded'
    observed = {}
    for source, har in recordings:
        paths = [urlsplit(entry['request']['url']).path for entry in har['log']['entries']
                 if entry['request']['method'] == 'POST' and 200 <= entry['response']['status'] < 300]
        for workflow, chain in profile['workflows'].items():
            if any(paths[index:index + len(chain)] == chain for index in range(len(paths))):
                observed.setdefault(workflow, set()).add(source)
    created = []
    for scenario in profile['scenarios']:
        if scenario['mode'] != mode or not all(key in observed for key in scenario['requires']):
            continue
        if mode == 'cross_flow' and len(set.union(*(observed[key] for key in scenario['requires']))) < 2:
            continue
        path = run_dir / 'backend_coverage' / flow / ('00_coverage_' + scenario['id'] + '.py')
        code = render_probe(identity['target_origin'], profile['setup'], profile['state_read'],
                            scenario['actions'], requirement['predicate'], scenario['id'].replace('_', ' '))
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_text(encoding='utf-8') != code:
                raise ValueError('Refusing to overwrite existing coverage probe: ' + path.name)
        else:
            with path.open('x', encoding='utf-8') as stream:
                stream.write(code)
        created.append(path)
    return created
