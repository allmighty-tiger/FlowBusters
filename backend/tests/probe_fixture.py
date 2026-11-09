"""Explicit test plans only: never loaded by production probe generation."""
import json
from copy import deepcopy


def evidence_plan(run, origin, actions, invariant, title='Amount bound'):
    cross = (run / 'cross_flow_inputs.json').exists()
    folders = sorted((run / 'cross_flow_sources').iterdir()) if cross else [run / 'flows' / run.name]
    observations = []
    for folder in folders:
        entries = json.loads((folder / 'recording.har').read_text())['log']['entries']
        observations.append({'source_run': folder.name, 'artifact': 'recording.har',
            'json_pointer': '/log/entries/0/response/content/text', 'value': entries[0]['response']['content']['text']})
    chain = [{'method': 'POST', 'path': a['endpoint'], 'body': a['body']} for a in actions]
    return {'schema_version': 1, 'title': title, 'origin': origin, 'setup': '/api/demo/reset',
        'state_read': '/api/order', 'claim_type': 'amount_bound', 'actions': chain,
        'predicate': 'business_rule_must_hold', 'invariant': deepcopy(invariant),
        'rule_origin': 'AI_INFERRED', 'inferred_invariant': {'statement': 'Compensation stays within payment',
            'predicate': deepcopy(invariant), 'required_actions': [{'method': a['method'], 'path': a['path']} for a in chain],
            'terminal_state': [{'path': ['order', 'id'], 'equals': '1042'}]},
        'supporting_observations': observations}
