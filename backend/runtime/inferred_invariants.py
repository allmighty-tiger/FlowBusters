"""Evidence-backed AI hypotheses, not owner-approved product requirements."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from backend.runtime.ui_provenance import resolve_json_pointer, _verify_cross_flow_snapshot


def validate_inference_shape(verification):
    if verification.get('rule_origin') != 'AI_INFERRED':
        raise ValueError('The inferred rule must explicitly declare rule_origin=AI_INFERRED')
    inferred = verification.get('inferred_invariant')
    if not isinstance(inferred, dict) or not str(inferred.get('statement', '')).strip():
        raise ValueError('An explicit inferred invariant statement is required')
    if inferred.get('predicate') != verification.get('invariant'):
        raise ValueError('The inferred invariant differs from the executed predicate')
    for name in ('required_actions', 'terminal_state'):
        if not isinstance(inferred.get(name), list) or not inferred[name]:
            raise ValueError(f'The inferred invariant needs explicit {name}')
    for action in inferred['required_actions']:
        if (not isinstance(action, dict) or action.get('method') not in ('POST', 'PUT', 'PATCH', 'DELETE')
                or not isinstance(action.get('path'), str) or not action['path'].startswith('/')
                or action['path'].startswith('//')):
            raise ValueError('Required actions need exact HTTP methods and root paths')
    for condition in inferred['terminal_state']:
        if (not isinstance(condition, dict) or 'equals' not in condition
                or not isinstance(condition.get('path'), list) or not condition['path']
                or not all(isinstance(key, str) and key for key in condition['path'])):
            raise ValueError('Terminal state needs exact response-root paths and expected values')
    observations = verification.get('supporting_observations')
    if not isinstance(observations, list) or not observations:
        raise ValueError('The inferred invariant needs raw supporting observations')
    return inferred, observations


def validate_support(verification, run, root):
    """Dereference only the selected run's authenticated recording snapshots."""
    inferred, observations = validate_inference_shape(verification)
    run = Path(run).resolve()
    from backend.runtime.endpoint_catalog import validate_recorded_input
    cross = (run / 'cross_flow_inputs.json').exists()
    if not cross:
        validate_recorded_input(run, run.name, root)
    endpoints = set()
    for observation in observations:
        if not isinstance(observation, dict):
            raise ValueError('Supporting observation must be structured')
        source = observation.get('source_run')
        artifact = observation.get('artifact')
        if not isinstance(source, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', source):
            raise ValueError('Invalid observation source_run')
        if artifact not in ('demo.json', 'recording.har'):
            raise ValueError('Supporting observations must reference demo.json or recording.har')
        folder = run / 'cross_flow_sources' / source if cross else run / 'flows' / run.name
        if not cross and source != run.name:
            raise ValueError('Observation references a recording not selected for this run')
        path = folder / artifact
        if path.is_symlink() or not path.resolve().is_relative_to(run):
            raise ValueError('Observation escapes the run')
        if cross:
            _verify_cross_flow_snapshot(run, source, folder, root=root)
        har = json.loads((folder / 'recording.har').read_text(encoding='utf-8'))
        for entry in har['log']['entries']:
            if entry['response'].get('status') not in (404, 405):
                endpoints.add((entry['request']['method'], urlsplit(entry['request']['url']).path))
        actual = resolve_json_pointer(json.loads(path.read_text(encoding='utf-8')), observation.get('json_pointer'))
        if 'value' not in observation or type(actual) is not type(observation['value']) or actual != observation['value']:
            raise ValueError('Supporting observation contradicts the raw recording')
    for action in inferred['required_actions']:
        if (action['method'], action['path']) not in endpoints:
            raise ValueError('Required terminal/workflow action was not captured in selected recordings: '
                             + action['method'] + ' ' + action['path'])
    return inferred


def terminal_error(verification):
    inferred, _ = validate_inference_shape(verification)
    actions = verification.get('actions') or []
    actual = [(a.get('request', {}).get('method'), urlsplit(a.get('request', {}).get('url', '')).path)
              for a in actions]
    required = [(a['method'], a['path']) for a in inferred['required_actions']]
    cursor = 0
    for action in actual:
        if cursor < len(required) and action == required[cursor]:
            cursor += 1
    if cursor != len(required):
        return 'Required workflow action was not executed: ' + ' '.join(required[cursor])
    from backend.runtime.probe_executor import normalize_response
    body, _ = normalize_response(verification.get('after', {}).get('response'), 200)
    for condition in inferred['terminal_state']:
        value = body
        for key in condition['path']:
            if not isinstance(value, dict) or key not in value:
                return 'Final state does not contain terminal field: ' + '.'.join(condition['path'])
            value = value[key]
        if type(value) is not type(condition['equals']) or value != condition['equals']:
            return ('Required terminal state was not reached: ' + '.'.join(condition['path'])
                    + f" is {value!r}; expected {condition['equals']!r}")
    return None
