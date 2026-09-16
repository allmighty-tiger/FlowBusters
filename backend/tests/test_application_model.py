import json
import tempfile
import unittest
from pathlib import Path
from backend.runtime.application_model import (
    catalog, collect, origin, prepare_inputs, source,
    validate_cross_flow_candidates,
)


class ApplicationModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ['refund', 'cancel']:
            for folder, filename, data in [
                ('reports', 'findings.json', {'target_url': 'http://localhost:3000'}),
                ('flows', 'state_map.json', {'transitions': [{'name': name}]}),
                ('flows', 'recording.har', {'log': {'entries': [{'request': {'method': 'POST'}}]}}),
                ('flows', 'demo.json', {'workflow_timeline': []}),
            ]:
                path = self.root / 'runs' / name / folder / name / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data), encoding='utf-8')

    def test_catalog(self):
        apps = catalog(self.root)
        self.assertEqual(len(apps), 1)
        self.assertEqual(len(apps[0]['runs']), 2)
        self.assertTrue(apps[0]['runs'][0]['eligible'])
        self.assertEqual(apps[0]['runs'][0]['observed_ui_provenance'], 'legacy_unverified')

    def test_origin(self):
        self.assertEqual(origin('https://EXAMPLE.com:443/path'), 'https://example.com')
        self.assertEqual(origin('http://[::1]:3000'), 'http://[::1]:3000')
        with self.assertRaises(ValueError):
            origin('http://user:password@example.com')

    def test_reject_bad_selection(self):
        for names in [['refund'], ['refund', 'refund'], ['../refund', 'cancel']]:
            with self.assertRaises(ValueError):
                collect(self.root, names, 'http://localhost:3000')
        with self.assertRaises(ValueError):
            collect(self.root, ['refund', 'cancel'], 'http://localhost:3001')

    def test_snapshot_provenance_and_source_preservation(self):
        inputs = collect(self.root, ['refund', 'cancel'], 'http://localhost:3000')
        dest = self.root / 'runs' / 'cross-test'
        prepare_inputs(dest, 'cross-test', inputs)
        manifest = json.loads((dest / 'cross_flow_inputs.json').read_text())
        self.assertEqual(len(manifest['sources']), 2)
        self.assertEqual(len(manifest['sources'][0]['sha256']), 64)
        self.assertEqual(len(manifest['signature']), 64)
        validate_cross_flow_candidates({'schema_version': 1, 'candidates': []},
                                       dest, root=self.root)
        har = json.loads((dest / 'flows/cross-test/recording.har').read_text())
        self.assertEqual([e['_source_run'] for e in har['log']['entries']], ['refund', 'cancel'])
        self.assertEqual(len(catalog(self.root)[0]['runs']), 2)
        with self.assertRaises(ValueError):
            collect(self.root, ['cross-test', 'refund'], 'http://localhost:3000')

    def test_corrupt_source_not_catalogued(self):
        _, artifact = source(self.root, 'refund')
        artifact('flows', 'state_map.json').write_text('broken')
        self.assertEqual(len(catalog(self.root)[0]['runs']), 1)

    def test_catalog_explicitly_loads_verified_structured_rules(self):
        _, artifact = source(self.root, 'refund')
        demo = {'workflow_timeline': {'ui_states': [
            {'step': 1,
             'elements': ['paragraph : Refunds cannot exceed the original payment'],
             'changes_from_previous': {'appeared': [], 'disappeared': []}},
            {'step': 2,
             'elements': ['paragraph : Refunds cannot exceed the original payment'],
             'changes_from_previous': {'appeared': [], 'disappeared': []}},
        ]}}
        artifact('flows', 'demo.json').write_text(json.dumps(demo), encoding='utf-8')
        state = {
            'schema_version': 2, 'observed_ui_rules_schema_version': 1,
            'target_url': 'http://localhost:3000', 'flow_name': 'refund',
            'transitions': [{'name': 'refund', 'method': 'POST', 'url': '/refund',
                             'response_status': 200, 'depends_on': [],
                             'ui_context': {'before_step': 1, 'after_step': 2,
                                            'visible_constraints': [], 'observed_changes': []}}],
            'roles': [], 'critical_endpoints': [{'method': 'POST', 'url': '/refund'}],
            'semantic_ui_capture': {'status': 'succeeded', 'artifact': 'demo.json',
                                    'ui_state_count': 2},
            'observed_ui_rules': [{
                'schema_version': 1, 'id': 'UIR-001',
                'statement': 'Refunds cannot exceed the original payment',
                'facts': [{'id': 'UIR-001-F1', 'type': 'explicit_ui_text',
                           'provenance_type': 'explicit_visible_ui_text',
                           'source_run': 'refund', 'artifact': 'demo.json', 'step': 1,
                           'text': 'paragraph : Refunds cannot exceed the original payment',
                           'json_pointer': '/workflow_timeline/ui_states/0/elements/0'}],
                'inference': {'provenance_type': 'agent_inference',
                              'text': 'The server should enforce the visible refund limit',
                              'derived_from': ['UIR-001-F1']},
            }],
        }
        artifact('flows', 'state_map.json').write_text(json.dumps(state), encoding='utf-8')
        run = next(item for item in catalog(self.root)[0]['runs'] if item['run_id'] == 'refund')
        self.assertEqual(run['observed_ui_provenance'], 'verified')
        self.assertEqual(run['observed_ui_rules'][0]['id'], 'UIR-001')
        self.assertEqual(run['observed_ui_rules'][0]['facts'][0]['source_run'], 'refund')

    def test_cross_flow_candidate_requires_structured_rule(self):
        run = self.root / 'runs' / 'cross-test'
        run.mkdir(parents=True)
        prepare_inputs(run, 'cross-test', {
            'target_url': 'http://localhost:3000', 'sources': [],
        }, root=self.root)
        with self.assertRaisesRegex(ValueError, 'structured rule'):
            validate_cross_flow_candidates({
                'schema_version': 1,
                'candidates': [{'source_runs': ['refund', 'cancel'],
                                'rule': 'refund/state_map.json observed_ui_rules[0]'}],
            }, run, root=self.root)
        with self.assertRaisesRegex(ValueError, 'supporting provenance'):
            validate_cross_flow_candidates({
                'schema_version': 1,
                'candidates': [{'source_runs': ['refund', 'cancel'],
                                'rule': {'source': 'api_state', 'reference': 'free form'}}],
            }, run, root=self.root)

    def test_cross_flow_manifest_tampering_is_rejected(self):
        inputs = collect(self.root, ['refund', 'cancel'], 'http://localhost:3000')
        run = self.root / 'runs' / 'cross-test'
        prepare_inputs(run, 'cross-test', inputs, root=self.root)
        path = run / 'cross_flow_inputs.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        manifest['sources'][0]['path'] = 'agent-rewritten-source'
        path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'signature is invalid'):
            validate_cross_flow_candidates({'schema_version': 1, 'candidates': []},
                                           run, root=self.root)

    def test_exact_legacy_rule_reference_remains_an_unverified_hypothesis(self):
        _, artifact = source(self.root, 'refund')
        state = json.loads(artifact('flows', 'state_map.json').read_text(encoding='utf-8'))
        state['observed_ui_rules'] = [{'statement': 'Refund should stay within the payment'}]
        artifact('flows', 'state_map.json').write_text(json.dumps(state), encoding='utf-8')
        inputs = collect(self.root, ['refund', 'cancel'], 'http://localhost:3000')
        run = self.root / 'runs' / 'cross-test'
        prepare_inputs(run, 'cross-test', inputs, root=self.root)
        candidate = {
            'source_runs': ['refund', 'cancel'],
            'rule': {'source': 'agent_inference', 'provenance': {
                'schema_version': 1,
                'source_run': 'refund',
                'artifact': 'state_map.json',
                'rule_id': 'LEGACY-UIR-001',
                'fact_ids': [],
            }},
        }
        validate_cross_flow_candidates(
            {'schema_version': 1, 'candidates': [candidate]}, run, root=self.root)

        invalid = json.loads(json.dumps(candidate))
        invalid['rule']['provenance']['fact_ids'] = ['invented-fact']
        with self.assertRaisesRegex(ValueError, 'empty fact_ids'):
            validate_cross_flow_candidates(
                {'schema_version': 1, 'candidates': [invalid]}, run, root=self.root)

        invalid = json.loads(json.dumps(candidate))
        invalid['rule']['provenance']['rule_id'] = 'LEGACY-UIR-999'
        with self.assertRaisesRegex(ValueError, 'missing or ambiguous'):
            validate_cross_flow_candidates(
                {'schema_version': 1, 'candidates': [invalid]}, run, root=self.root)

        masquerading = json.loads(json.dumps(candidate))
        masquerading['rule']['source'] = 'observed_ui'
        masquerading['rule']['provenance']['fact_ids'] = ['LEGACY-UIR-001-F1']
        with self.assertRaisesRegex(ValueError, 'schema_version must be 2'):
            validate_cross_flow_candidates(
                {'schema_version': 1, 'candidates': [masquerading]}, run, root=self.root)


if __name__ == '__main__':
    unittest.main()
