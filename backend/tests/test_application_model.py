import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from backend.tests.recording_fixture import write_recording
from backend.runtime.ui_provenance import normalize_observed_ui_rules
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
            write_recording(self.root, name)

    def test_catalog(self):
        apps = catalog(self.root)
        self.assertEqual(len(apps), 1)
        self.assertEqual(len(apps[0]['runs']), 2)
        self.assertTrue(apps[0]['runs'][0]['eligible'])
        self.assertEqual(apps[0]['runs'][0]['observed_ui_provenance'], 'verified')

    def test_zero_script_refund_is_eligible_without_report_target_and_sources_are_unchanged(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        inputs = collect(self.root, ['refund', 'cancel'], 'http://localhost:3000')
        self.assertEqual([s['run_id'] for s in inputs['sources']], ['refund', 'cancel'])
        self.assertEqual([e['request']['url'] for e in inputs['sources'][0]['har']['log']['entries']][1:], [
            'http://localhost:3000/api/order/refund/request', 'http://localhost:3000/api/order/refund/complete'])
        self.assertFalse(list(self.root.rglob('*.py')))
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        _, artifact = source(self.root, 'refund')
        artifact('reports', 'findings.json').write_text('{unfinished draft', encoding='utf-8')
        self.assertEqual(len(collect(self.root, ['refund', 'cancel'], 'http://localhost:3000')['sources']), 2)
        artifact('reports', 'findings.json').unlink()  # Optional report, temp fixture only.
        self.assertEqual(len(collect(self.root, ['refund', 'cancel'], 'http://localhost:3000')['sources']), 2)

    def test_origin_disagreement_in_state_report_or_har_is_rejected(self):
        _, artifact = source(self.root, 'refund')
        for folder, filename in [('flows', 'state_map.json'), ('reports', 'findings.json'), ('flows', 'recording.har')]:
            path = artifact(folder, filename)
            before = path.read_bytes()
            data = json.loads(before)
            if filename == 'recording.har':
                data['log']['entries'][0]['request']['url'] = 'http://localhost:3001/api/order'
            else:
                data['target_url'] = 'http://localhost:3001'
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.subTest(artifact=filename), self.assertRaisesRegex(ValueError, 'origin'):
                collect(self.root, ['refund', 'cancel'], 'http://localhost:3000')
            self.assertEqual([r['run_id'] for r in catalog(self.root)[0]['runs']], ['cancel'])
            path.write_bytes(before)

    def test_marker_and_current_state_map_are_required_for_new_selection(self):
        run, artifact = source(self.root, 'refund')
        marker = run / 'recording_validated.marker'
        marker.write_text('999', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'count disagree'):
            collect(self.root, ['refund', 'cancel'], 'http://localhost:3000')
        marker.write_text('3', encoding='utf-8')
        artifact('flows', 'state_map.json').write_text('{"transitions": []}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'schema_version must be 2'):
            collect(self.root, ['refund', 'cancel'], 'http://localhost:3000')

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
        self.assertEqual([e['_source_run'] for e in har['log']['entries']], ['refund'] * 3 + ['cancel'] * 2)
        self.assertEqual(len(catalog(self.root)[0]['runs']), 2)
        with self.assertRaises(ValueError):
            collect(self.root, ['cross-test', 'refund'], 'http://localhost:3000')

    def test_schema_native_signed_rules_validate_without_false_snapshot_mismatch(self):
        inputs = collect(self.root, ['refund', 'cancel'], 'http://localhost:3000')
        self.assertNotIn('provenance_status', inputs['sources'][0]['observed_ui_rules'][0]
                         if inputs['sources'][0]['observed_ui_rules'] else {})
        run = self.root / 'runs' / 'cross-native-rules'
        prepare_inputs(run, 'cross-native-rules', inputs, root=self.root)
        candidate = {'source_runs': ['refund', 'cancel'], 'rule': {
            'source': 'agent_inference', 'provenance': {
                'schema_version': 1, 'source_run': 'refund',
                'artifact': 'state_map.json', 'rule_id': 'missing', 'fact_ids': [],
            }}}
        # Snapshot authentication must pass first. The expected failure is the
        # deliberately missing rule, not a false digest mismatch.
        with self.assertRaisesRegex(ValueError, 'rule id is missing or ambiguous'):
            validate_cross_flow_candidates(
                {'schema_version': 1, 'candidates': [candidate]}, run, root=self.root)

    def test_signed_rule_metadata_cannot_diverge_from_validated_source(self):
        inputs = collect(self.root, ['refund', 'cancel'], 'http://localhost:3000')
        # Give the fixture one real rule so signed metadata tampering is visible.
        _, artifact = source(self.root, 'refund')
        state = json.loads(artifact('flows', 'state_map.json').read_text(encoding='utf-8'))
        state['observed_ui_rules'] = [{
            'schema_version': 1, 'id': 'UIR-001', 'statement': 'Ready text is visible',
            'facts': [{'id': 'UIR-001-F1', 'type': 'explicit_ui_text',
                       'provenance_type': 'explicit_visible_ui_text', 'source_run': 'refund',
                       'artifact': 'demo.json', 'step': 1, 'text': 'paragraph : Ready',
                       'json_pointer': '/workflow_timeline/ui_states/0/elements/0'}],
            'inference': {'provenance_type': 'agent_inference', 'text': 'Ready state exists',
                          'derived_from': ['UIR-001-F1']},
        }]
        artifact('flows', 'state_map.json').write_text(json.dumps(state), encoding='utf-8')
        inputs = collect(self.root, ['refund', 'cancel'], 'http://localhost:3000')
        run = self.root / 'runs' / 'cross-rule-tamper'
        prepare_inputs(run, 'cross-rule-tamper', inputs, root=self.root)
        manifest_path = run / 'cross_flow_inputs.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['sources'][0]['observed_ui_rules'][0]['statement'] = 'tampered but re-signed'
        from backend.runtime.application_model import _canonical
        from backend.runtime.probe_executor import key_for
        import hmac
        unsigned = {key: value for key, value in manifest.items() if key != 'signature'}
        manifest['signature'] = hmac.new(key_for(self.root), _canonical(unsigned), hashlib.sha256).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        candidate = {'source_runs': ['refund', 'cancel'], 'rule': {
            'source': 'observed_ui', 'provenance': {'schema_version': 1,
                'source_run': 'refund', 'artifact': 'state_map.json',
                'rule_id': 'UIR-001', 'fact_ids': ['UIR-001-F1']}}}
        with self.assertRaisesRegex(ValueError, 'manifest rule metadata differs'):
            validate_cross_flow_candidates(
                {'schema_version': 1, 'candidates': [candidate]}, run, root=self.root)

    def test_corrupt_source_not_catalogued(self):
        _, artifact = source(self.root, 'refund')
        artifact('flows', 'state_map.json').write_text('broken')
        self.assertEqual(len(catalog(self.root)[0]['runs']), 1)

    def test_catalog_explicitly_loads_verified_structured_rules(self):
        _, artifact = source(self.root, 'refund')
        demo = json.loads(artifact('flows', 'demo.json').read_text(encoding='utf-8'))
        demo['workflow_timeline']['ui_states'] = [
            {'step': 1,
             'elements': ['paragraph : Refunds cannot exceed the original payment'],
             'changes_from_previous': {'appeared': [], 'disappeared': []}},
            {'step': 2,
             'elements': ['paragraph : Refunds cannot exceed the original payment'],
             'changes_from_previous': {'appeared': [], 'disappeared': []}},
        ]
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
        inputs = collect(self.root, ['refund', 'cancel'], 'http://localhost:3000')
        _, artifact = source(self.root, 'refund')
        state = json.loads(artifact('flows', 'state_map.json').read_text(encoding='utf-8'))
        state.pop('schema_version')
        state['observed_ui_rules'] = [{'statement': 'Refund should stay within the payment'}]
        artifact('flows', 'state_map.json').write_text(json.dumps(state), encoding='utf-8')
        # Simulate a historical snapshot; new selections must reject legacy
        # maps, while existing copied legacy rules remain readable/unverified.
        with self.assertRaisesRegex(ValueError, 'schema_version must be 2'):
            collect(self.root, ['refund', 'cancel'], 'http://localhost:3000')
        item = inputs['sources'][0]
        item['state_map'] = state
        item['observed_ui_rules'] = normalize_observed_ui_rules(state, 'refund', artifact('flows', 'state_map.json').parent)
        item['observed_ui_provenance'] = 'legacy_unverified'
        item.pop('sha256')
        item['sha256'] = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
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
