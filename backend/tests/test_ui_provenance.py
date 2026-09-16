import hashlib
import hmac
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from backend.runtime.crew_runner import ArtifactWatcher
from backend.runtime.ui_provenance import (
    UIProvenanceError,
    normalize_observed_ui_rules,
    resolve_json_pointer,
    validate_rule_reference,
    validate_state_map,
)


CANCEL = 'button "Cancel order" [cursor=pointer]'


class UIProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.artifacts = self.root / 'flows' / 'price-adjustment'
        self.artifacts.mkdir(parents=True)
        self.demo = {'workflow_timeline': {'ui_states': [
            {'step': 2, 'elements': [CANCEL],
             'changes_from_previous': {'appeared': [], 'disappeared': []}},
            {'step': 4, 'elements': ['button "Request refund" [cursor=pointer]'],
             'changes_from_previous': {'appeared': [], 'disappeared': [CANCEL]}},
        ]}}
        bodies = [
            {'order': {'availableActions': {'cancelOrder': True}}},
            {'message': 'Price adjustment issued.',
             'order': {'availableActions': {'cancelOrder': False}}},
        ]
        self.har = {'log': {'entries': [
            {'response': {'content': {'text': json.dumps(body)}}} for body in bodies
        ]}}
        self.rule = {
            'schema_version': 1,
            'id': 'UIR-001',
            'statement': 'Cancellation is unavailable after price adjustment',
            'facts': [
                {'id': 'UIR-001-F1', 'type': 'ui_element_transition',
                 'provenance_type': 'observed_ui_affordance',
                 'source_run': 'price-adjustment', 'artifact': 'demo.json',
                 'before_step': 2, 'after_step': 4,
                 'element': {'role': 'button', 'name': 'Cancel order'},
                 'transition': 'disappeared',
                 'json_pointers': {
                     'before': '/workflow_timeline/ui_states/0/elements/0',
                     'after': '/workflow_timeline/ui_states/1/changes_from_previous/disappeared/0'}},
                {'id': 'UIR-001-F2', 'type': 'api_field_transition',
                 'provenance_type': 'api_state_fact',
                 'source_run': 'price-adjustment', 'artifact': 'recording.har',
                 'field_path': ['order', 'availableActions', 'cancelOrder'],
                 'before': True, 'after': False, 'entry_indexes': [0, 1],
                 'json_pointers': {
                     'before': '/log/entries/0/response/content/text',
                     'after': '/log/entries/1/response/content/text'}},
            ],
            'inference': {'provenance_type': 'agent_inference',
                          'text': 'The workflow intends cancellation to be unavailable after adjustment',
                          'derived_from': ['UIR-001-F1', 'UIR-001-F2']},
        }
        self.state_map = {
            'schema_version': 2,
            'observed_ui_rules_schema_version': 1,
            'target_url': 'http://localhost:3000',
            'flow_name': 'price-adjustment',
            'transitions': [{
                'name': 'request_price_adjustment', 'method': 'POST',
                'url': 'http://localhost:3000/api/order/price-adjustment',
                'response_status': 200, 'depends_on': [],
                'ui_context': {'before_step': 2, 'after_step': 4,
                               'visible_constraints': [],
                               'observed_changes': ['Cancel order disappeared']},
            }],
            'roles': [],
            'critical_endpoints': [{'method': 'POST', 'url': '/api/order/price-adjustment'}],
            'semantic_ui_capture': {'status': 'succeeded', 'artifact': 'demo.json', 'ui_state_count': 2},
            'observed_ui_rules': [self.rule],
        }
        self._write_artifacts()

    def tearDown(self):
        self.temp.cleanup()

    def _write_artifacts(self):
        (self.artifacts / 'demo.json').write_text(json.dumps(self.demo), encoding='utf-8')
        (self.artifacts / 'recording.har').write_text(json.dumps(self.har), encoding='utf-8')
        (self.artifacts / 'state_map.json').write_text(json.dumps(self.state_map), encoding='utf-8')

    def _write_cross_flow_manifest(self, run, source_runs):
        key = b'test-only-signing-key'
        key_path = self.root / 'execution_keys' / 'receipt.key'
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(key)
        manifest = {
            'schema_version': 1,
            'target_url': 'http://localhost:3000',
            'sources': [{'run_id': source_run} for source_run in source_runs],
        }
        signature = hmac.new(
            key,
            json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode(),
            hashlib.sha256,
        ).hexdigest()
        (run / 'cross_flow_inputs.json').write_text(
            json.dumps({**manifest, 'signature': signature}), encoding='utf-8')

    def test_real_cancel_order_disappearance_resolves_to_demo_states(self):
        rules = validate_state_map(self.state_map, self.artifacts, 'price-adjustment')
        self.assertEqual(rules[0]['facts'][0]['element'], {'role': 'button', 'name': 'Cancel order'})

    def test_api_capability_transition_is_api_fact(self):
        rules = validate_state_map(self.state_map, self.artifacts, 'price-adjustment')
        fact = rules[0]['facts'][1]
        self.assertEqual(fact['provenance_type'], 'api_state_fact')
        self.assertEqual((fact['before'], fact['after']), (True, False))

    def test_fact_artifact_allowlist_and_source_run_traversal(self):
        wrong_artifact = deepcopy(self.state_map)
        wrong_artifact['observed_ui_rules'][0]['facts'][0]['artifact'] = 'state_map.json'
        with self.assertRaisesRegex(UIProvenanceError, 'must reference demo.json'):
            validate_state_map(wrong_artifact, self.artifacts, 'price-adjustment')
        reference = {'source': 'observed_ui', 'provenance': {
            'schema_version': 1, 'source_run': '../price-adjustment',
            'artifact': 'state_map.json', 'rule_id': 'UIR-001',
            'fact_ids': ['UIR-001-F1']}}
        with self.assertRaisesRegex(UIProvenanceError, 'source_run is invalid'):
            validate_rule_reference(reference, self.root)

    def test_cross_flow_cannot_fall_back_to_agent_authored_flow_artifacts(self):
        (self.root / 'cross_flow_inputs.json').write_text(
            json.dumps({'schema_version': 1, 'sources': []}), encoding='utf-8')
        reference = {'source': 'observed_ui', 'provenance': {
            'schema_version': 1, 'source_run': 'price-adjustment',
            'artifact': 'state_map.json', 'rule_id': 'UIR-001',
            'fact_ids': ['UIR-001-F1']}}
        with self.assertRaisesRegex(UIProvenanceError, 'artifacts are unavailable'):
            validate_rule_reference(reference, self.root)

    def test_server_rule_is_only_agent_inference(self):
        rule = validate_state_map(self.state_map, self.artifacts, 'price-adjustment')[0]
        self.assertEqual(rule['inference']['provenance_type'], 'agent_inference')
        self.assertNotIn('agent_inference', {fact['provenance_type'] for fact in rule['facts']})

    def test_duplicate_and_cross_namespaced_fact_ids_are_rejected(self):
        duplicate = deepcopy(self.state_map)
        duplicate['observed_ui_rules'][0]['facts'].append(
            deepcopy(duplicate['observed_ui_rules'][0]['facts'][0]))
        with self.assertRaisesRegex(UIProvenanceError, 'unique'):
            validate_state_map(duplicate, self.artifacts, 'price-adjustment')
        wrong_namespace = deepcopy(self.state_map)
        wrong_namespace['observed_ui_rules'][0]['facts'][0]['id'] = 'UIR-002-F1'
        wrong_namespace['observed_ui_rules'][0]['inference']['derived_from'][0] = 'UIR-002-F1'
        with self.assertRaisesRegex(UIProvenanceError, 'namespaced'):
            validate_state_map(wrong_namespace, self.artifacts, 'price-adjustment')

    def test_broken_and_ambiguous_ui_pointers_are_rejected(self):
        broken = deepcopy(self.state_map)
        broken['observed_ui_rules'][0]['facts'][0]['json_pointers']['before'] += '0'
        with self.assertRaises(UIProvenanceError):
            validate_state_map(broken, self.artifacts, 'price-adjustment')
        ambiguous = deepcopy(self.demo)
        ambiguous['workflow_timeline']['ui_states'][0]['elements'].append(CANCEL)
        (self.artifacts / 'demo.json').write_text(json.dumps(ambiguous), encoding='utf-8')
        with self.assertRaisesRegex(UIProvenanceError, 'ambiguous'):
            validate_state_map(self.state_map, self.artifacts, 'price-adjustment')

    def test_json_pointer_escaping_is_exact_and_invalid_escapes_fail(self):
        document = {'a/b': {'x~y': 7}}
        self.assertEqual(resolve_json_pointer(document, '/a~1b/x~0y'), 7)
        for pointer in ('/a~2b', '/a~0b~2'):
            with self.assertRaisesRegex(UIProvenanceError, 'Invalid JSON pointer escape'):
                resolve_json_pointer(document, pointer)

    def test_missing_steps_and_invented_elements_are_rejected(self):
        for field, value in [('before_step', 3), ('after_step', 5)]:
            missing = deepcopy(self.state_map)
            missing['observed_ui_rules'][0]['facts'][0][field] = value
            with self.assertRaisesRegex(UIProvenanceError, 'declared UI step'):
                validate_state_map(missing, self.artifacts, 'price-adjustment')
        invented = deepcopy(self.state_map)
        invented['observed_ui_rules'][0]['facts'][0]['element']['name'] = 'Issue credit'
        with self.assertRaisesRegex(UIProvenanceError, 'role/name'):
            validate_state_map(invented, self.artifacts, 'price-adjustment')

    def test_rule_reference_requires_raw_ui_fact(self):
        reference = {'source': 'observed_ui', 'provenance': {
            'schema_version': 1, 'source_run': 'price-adjustment',
            'artifact': 'state_map.json', 'rule_id': 'UIR-001',
            'fact_ids': ['UIR-001-F1', 'UIR-001-F2']}}
        resolved = validate_rule_reference(reference, self.root)
        self.assertEqual(resolved['rule_id'], 'UIR-001')
        api_only = deepcopy(reference)
        api_only['provenance']['fact_ids'] = ['UIR-001-F2']
        with self.assertRaisesRegex(UIProvenanceError, 'raw UI fact'):
            validate_rule_reference(api_only, self.root)

    def test_schema_incompatible_new_state_map_fails_artifact_gate(self):
        accepted = ArtifactWatcher(self.root, 'price-adjustment').check()
        self.assertIn('state_map.json', {name for name, _ in accepted})
        invalid = deepcopy(self.state_map)
        del invalid['semantic_ui_capture']
        (self.artifacts / 'state_map.json').write_text(json.dumps(invalid), encoding='utf-8')
        watcher = ArtifactWatcher(self.root, 'price-adjustment')
        with self.assertRaisesRegex(UIProvenanceError, 'semantic UI capture'):
            watcher.check()

    def test_cross_flow_aggregate_may_have_no_new_rules(self):
        run = self.root / 'runs' / 'cross-test'
        artifacts = run / 'flows' / 'cross-test'
        artifacts.mkdir(parents=True)
        aggregate = deepcopy(self.state_map)
        aggregate.update({
            'mode': 'cross_flow',
            'flow_name': 'cross-test',
            'source_runs': ['price-adjustment', 'refund'],
            'observed_ui_rules': [],
        })
        aggregate['transitions'][0]['ui_context']['source_run'] = 'price-adjustment'
        aggregate['semantic_ui_capture']['no_relevant_rules_reason'] = None
        for state in self.demo['workflow_timeline']['ui_states']:
            state['_source_run'] = 'price-adjustment'
        self.demo.update({
            'mode': 'cross_flow',
            'sources': [
                {'run_id': 'price-adjustment'},
                {'run_id': 'refund'},
            ],
        })
        (artifacts / 'demo.json').write_text(json.dumps(self.demo), encoding='utf-8')
        (artifacts / 'recording.har').write_text(json.dumps(self.har), encoding='utf-8')
        self._write_cross_flow_manifest(run, aggregate['source_runs'])
        self.assertEqual(
            validate_state_map(aggregate, artifacts, 'cross-test'), [])

    def test_cross_flow_aggregate_sources_must_match_backend_manifest(self):
        run = self.root / 'runs' / 'cross-test'
        artifacts = run / 'flows' / 'cross-test'
        artifacts.mkdir(parents=True)
        aggregate = deepcopy(self.state_map)
        aggregate.update({
            'mode': 'cross_flow',
            'flow_name': 'cross-test',
            'source_runs': ['price-adjustment', 'invented'],
            'observed_ui_rules': [],
        })
        aggregate['transitions'][0]['ui_context']['source_run'] = 'price-adjustment'
        for state in self.demo['workflow_timeline']['ui_states']:
            state['_source_run'] = 'price-adjustment'
        self.demo.update({
            'mode': 'cross_flow',
            'sources': [
                {'run_id': 'price-adjustment'},
                {'run_id': 'refund'},
            ],
        })
        (artifacts / 'demo.json').write_text(json.dumps(self.demo), encoding='utf-8')
        (artifacts / 'recording.har').write_text(json.dumps(self.har), encoding='utf-8')
        self._write_cross_flow_manifest(run, ['price-adjustment', 'refund'])
        with self.assertRaisesRegex(UIProvenanceError, 'source_runs must match'):
            validate_state_map(aggregate, artifacts, 'cross-test')

    def test_ordinary_empty_rules_still_need_an_explicit_reason(self):
        ordinary = deepcopy(self.state_map)
        ordinary['observed_ui_rules'] = []
        ordinary['semantic_ui_capture']['no_relevant_rules_reason'] = None
        with self.assertRaisesRegex(UIProvenanceError, 'no-relevant-rules reason'):
            validate_state_map(ordinary, self.artifacts, 'price-adjustment')

    def test_cross_flow_steps_are_resolved_within_the_named_source(self):
        run = self.root / 'runs' / 'cross-test'
        artifacts = run / 'flows' / 'cross-test'
        artifacts.mkdir(parents=True)
        aggregate = deepcopy(self.state_map)
        aggregate.update({
            'mode': 'cross_flow', 'flow_name': 'cross-test',
            'source_runs': ['price-adjustment', 'refund'],
            'observed_ui_rules': [],
        })
        aggregate['transitions'][0]['ui_context']['source_run'] = 'price-adjustment'
        states = self.demo['workflow_timeline']['ui_states']
        for state in states:
            state['_source_run'] = 'price-adjustment'
        self.demo.update({'mode': 'cross_flow', 'sources': [
            {'run_id': 'price-adjustment'}, {'run_id': 'refund'}]})
        (artifacts / 'demo.json').write_text(json.dumps(self.demo), encoding='utf-8')
        (artifacts / 'recording.har').write_text(json.dumps(self.har), encoding='utf-8')
        self._write_cross_flow_manifest(run, aggregate['source_runs'])
        validate_state_map(aggregate, artifacts, 'cross-test')

        missing_source = deepcopy(aggregate)
        del missing_source['transitions'][0]['ui_context']['source_run']
        with self.assertRaisesRegex(UIProvenanceError, 'exact source_run'):
            validate_state_map(missing_source, artifacts, 'cross-test')

        wrong_local_step = deepcopy(aggregate)
        wrong_local_step['transitions'][0]['ui_context']['after_step'] = 3
        with self.assertRaisesRegex(UIProvenanceError, 'source-local'):
            validate_state_map(wrong_local_step, artifacts, 'cross-test')

    def test_inferred_transition_has_no_invented_http_result(self):
        inferred = deepcopy(self.state_map)
        inferred['transitions'].append({
            'name': 'cancel_order',
            'method': 'POST',
            'url': 'http://localhost:3000/api/order/cancel',
            'response_status': None,
            'inferred': True,
            'depends_on': [],
            'ui_context': {
                'before_step': 2,
                'after_step': 2,
                'visible_constraints': ['Cancel order is visible'],
                'observed_changes': ['Not executed; no state transition observed'],
            },
        })
        self.assertEqual(len(validate_state_map(
            inferred, self.artifacts, 'price-adjustment')), 1)

        fabricated = deepcopy(inferred)
        fabricated['transitions'][-1]['response_status'] = 200
        with self.assertRaisesRegex(UIProvenanceError, 'must not claim'):
            validate_state_map(fabricated, self.artifacts, 'price-adjustment')

        observed_without_status = deepcopy(self.state_map)
        observed_without_status['transitions'][0]['response_status'] = None
        with self.assertRaisesRegex(UIProvenanceError, 'observed transition needs'):
            validate_state_map(observed_without_status, self.artifacts, 'price-adjustment')

    def test_legacy_state_map_remains_readable_but_unverified(self):
        legacy = {'observed_ui_rules': [{'text': 'Cancel order disappears'}]}
        normalized = normalize_observed_ui_rules(legacy, 'price-adjustment', self.artifacts)
        self.assertEqual(normalized[0]['provenance_status'], 'legacy_unverified')
        self.assertIn('#/observed_ui_rules/0', normalized[0]['legacy_reference'])


if __name__ == '__main__':
    unittest.main()
