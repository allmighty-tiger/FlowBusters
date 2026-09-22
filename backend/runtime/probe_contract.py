"""Typed AI plan admission. No HTTP execution and no product-specific authority."""
from copy import deepcopy
from enum import Enum
import json
import math
from pathlib import Path


class ClaimType(str, Enum):
    AMOUNT_BOUND = 'amount_bound'
    UNSUPPORTED = 'unsupported'


class Predicate(str, Enum):
    BUSINESS_RULE = 'business_rule_must_hold'
    UNSUPPORTED = 'unsupported_business_rule'


ALLOWED_COMBINATIONS = [
    {'claim_type': ClaimType.AMOUNT_BOUND.value, 'predicate': Predicate.BUSINESS_RULE.value,
     'operator': 'sum_lte'},
    {'claim_type': ClaimType.UNSUPPORTED.value, 'predicate': Predicate.UNSUPPORTED.value,
     'operator': None},
]
PLAN_SCHEMA = {
    'type': 'object', 'required': ['schema_version', 'title', 'claim_type', 'predicate',
                                 'origin', 'setup', 'state_read', 'actions'],
    'properties': {'schema_version': {'const': 2},
                   'claim_type': {'enum': [v.value for v in ClaimType]},
                   'predicate': {'enum': [v.value for v in Predicate]}},
    'oneOf': [
        {'properties': {'claim_type': {'const': 'amount_bound'},
                        'predicate': {'const': 'business_rule_must_hold'},
                        'invariant': {'type': 'object', 'properties': {'operator': {'const': 'sum_lte'}}}},
         'required': ['invariant', 'rule_origin', 'inferred_invariant', 'supporting_observations']},
        {'properties': {'claim_type': {'const': 'unsupported'},
                        'predicate': {'const': 'unsupported_business_rule'}, 'invariant': {'type': 'null'}},
         'required': ['unsupported_reason']},
    ],
}


def validate_combination(plan):
    try:
        pair = (ClaimType(plan.get('claim_type')), Predicate(plan.get('predicate')))
    except (ValueError, TypeError) as exc:
        raise ValueError('Unknown typed claim/predicate; allowed combinations: ' + json.dumps(ALLOWED_COMBINATIONS)) from exc
    if pair not in ((ClaimType.AMOUNT_BOUND, Predicate.BUSINESS_RULE),
                    (ClaimType.UNSUPPORTED, Predicate.UNSUPPORTED)):
        raise ValueError('Incompatible claim/predicate; allowed combinations: ' + json.dumps(ALLOWED_COMBINATIONS))
    if pair[0] == ClaimType.UNSUPPORTED and plan.get('invariant') is not None:
        raise ValueError('Unsupported hypotheses must not claim an evaluated invariant')


def validate_admission(plan, run, root, catalog):
    from backend.runtime.generated_probes import validate_plan, validate_recorded_operand_paths
    from backend.runtime.inferred_invariants import validate_support
    if plan.get('schema_version') != 2:
        raise ValueError('New JSON plans require schema_version=2')
    validate_combination(plan)
    validate_plan(plan)
    if not plan.get('setup'):
        raise ValueError('Independent configured setup is required')
    rows = catalog['endpoints']
    if not any(r['method'] == 'POST' and r['path'] == plan['setup'] and r['origin'] == plan['origin']
               and r['authority'] == 'configured_setup' for r in rows):
        raise ValueError('Setup is not authorized by the endpoint catalog')
    for method, path in [('GET', plan['state_read']), *[(a['method'], a['path']) for a in plan['actions']]]:
        if not any((r['method'], r['origin'], r['path']) == (method, plan['origin'], path) for r in rows):
            raise ValueError(f'Unobserved method/path: {method} {path}')
    if plan['predicate'] == Predicate.BUSINESS_RULE.value:
        validate_support(plan, run, root)
        validate_recorded_operand_paths(plan, run)


def write_admitted_plan(path, plan, run, root, catalog):
    """Nothing is written until the entire plan, including its pair, validates."""
    from backend.runtime.generated_probes import render_plan
    validate_admission(plan, run, root, catalog)
    path = Path(path)
    if list((Path(run) / 'reports').glob('*/executions/*.json')):
        raise ValueError('Admission refused after execution')
    with path.open('x', encoding='utf-8') as stream:
        stream.write(render_plan(plan))


def observed_context(run, root):
    """Enumerate explicit paths for feedback; never substitute or guess a path."""
    from backend.runtime.endpoint_catalog import validate_recorded_input
    from backend.runtime.ui_provenance import _verify_cross_flow_snapshot
    run = Path(run)
    cross = (run / 'cross_flow_inputs.json').exists()
    folders = sorted((run / 'cross_flow_sources').iterdir()) if cross else [run / 'flows' / run.name]
    fields, observations = [], []
    for folder in folders:
        if cross:
            _verify_cross_flow_snapshot(run, folder.name, folder, root=root)
        else:
            validate_recorded_input(run, run.name, root)
        har = json.loads((folder / 'recording.har').read_text(encoding='utf-8'))
        for index, entry in enumerate(har['log']['entries']):
            content = entry['response'].get('content') or {}
            raw = content.get('text', '')
            pointer = f'/log/entries/{index}/response/content/text'
            observations.append({'source_run': folder.name, 'artifact': 'recording.har',
                                 'json_pointer': pointer, 'value': raw})
            try:
                decoded = raw
                if content.get('encoding') == 'base64':
                    import base64
                    decoded = base64.b64decode(raw, validate=True).decode('utf-8')
                body = json.loads(decoded)
            except (ValueError, TypeError, UnicodeError):
                continue
            def walk(value, path):
                if isinstance(value, dict):
                    for key, child in value.items():
                        walk(child, [*path, key])
                elif type(value) in (int, float, str, bool) and (type(value) not in (int, float) or math.isfinite(value)):
                    fields.append({'source_run': folder.name, 'entry_index': index,
                                   'response_path': path, 'value': value,
                                   'numeric': type(value) in (int, float), 'raw_pointer': pointer})
            walk(body, [])
    return {'observed_fields': fields, 'supporting_observation_examples': observations}


def regeneration_feedback(plan, error, context):
    return {'original_security_hypothesis': plan.get('title'),
            'original_inferred_invariant': plan.get('inferred_invariant'),
            'rejected_combination': {k: plan.get(k) for k in ('claim_type', 'predicate')},
            'validation_error': str(error), 'allowed_combinations': ALLOWED_COMBINATIONS,
            'schema': PLAN_SCHEMA, 'original_plan': plan, **context}


def review_only(plan, error):
    """Preserve the hypothesis, never reinterpret an invented predicate as supported."""
    result = deepcopy(plan)
    result.update(schema_version=2, claim_type=ClaimType.UNSUPPORTED.value,
                  predicate=Predicate.UNSUPPORTED.value, invariant=None,
                  unsupported_reason='The generated hypothesis has no admitted executable predicate: ' + str(error))
    result.pop('inferred_invariant', None)
    return result
