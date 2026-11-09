"""Complete, isolated recorder evidence for source-selection/report tests."""
import json
from pathlib import Path


def write_recording(root, name):
    run = Path(root) / 'runs' / name
    artifacts = run / 'flows' / name
    artifacts.mkdir(parents=True)
    endpoints = (['/api/order/refund/request', '/api/order/refund/complete']
                 if name == 'refund' else ['/api/order/cancel'])
    requests = [{'method': 'GET', 'url': 'http://localhost:3000/api/order'}] + [
        {'method': 'POST', 'url': 'http://localhost:3000' + endpoint} for endpoint in endpoints]
    demo = {
        'schema_version': 2, 'source': 'backend-mcp-recorder', 'flow_name': name,
        'target_url': 'http://localhost:3000/',
        'timestamp_start': '2026-09-17T04:01:16Z', 'timestamp_end': '2026-09-17T04:01:45Z',
        'initial_snapshot': 'initial', 'final_snapshot': 'final', 'warnings': [],
        'request_count': len(requests),
        'workflow_timeline': {'network_sequence': requests, 'ui_states': [
            {'step': 1, 'elements': ['paragraph : Ready'],
             'changes_from_previous': {'appeared': [], 'disappeared': []}},
            {'step': 2, 'elements': ['paragraph : Done'],
             'changes_from_previous': {'appeared': [], 'disappeared': []}},
        ]},
    }
    state = {
        'schema_version': 2, 'observed_ui_rules_schema_version': 1,
        'flow_name': name, 'target_url': demo['target_url'],
        'transitions': [{'name': name, **requests[-1], 'response_status': 200, 'depends_on': [],
                         'ui_context': {'before_step': 1, 'after_step': 2,
                                        'visible_constraints': [], 'observed_changes': []}}],
        'roles': [], 'critical_endpoints': [requests[-1]],
        'semantic_ui_capture': {'status': 'succeeded', 'artifact': 'demo.json',
                                'ui_state_count': 2, 'no_relevant_rules_reason': 'No relevant UI rule in fixture'},
        'observed_ui_rules': [],
    }
    files = {
        artifacts / 'demo.json': demo,
        artifacts / 'recording.har': {'log': {'version': '1.2', 'creator': {'name': 'fixture'},
            'entries': [{'request': request, 'response': {'status': 200, 'content': {'text': '{}'}}}
                        for request in requests]}},
        artifacts / 'state_map.json': state,
        artifacts / 'har_data' / 'capture_manifest.json': {
            'request_ids': list(range(1, len(requests) + 1)), 'missing': [], 'unrecoverable_response_bodies': []},
        run / 'scope.json': {'allowed_domains': ['http://localhost:3000'],
                            'allowed_paths_prefix': ['*'], 'block_production': False},
        run / 'reports' / name / 'findings.json': {'findings': []},
    }
    for path, value in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')
    (run / 'recording_validated.marker').write_text(str(len(requests)), encoding='utf-8')
    return run, artifacts
