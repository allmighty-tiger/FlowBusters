"""New-run contracts: real pointers, terminal actions and honest confirmation."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import AsyncMock
from types import SimpleNamespace
import asyncio

from backend.runtime.generated_probes import validate_plan_set
from backend.runtime.inferred_invariants import validate_support, terminal_error
from backend.runtime.ui_provenance import relocate_ui_pointers, _validate_ui_text, UIProvenanceError
from backend.tests.test_generated_probes import plan


class InferenceGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name) / 'flow'; self.run.mkdir()
        self.artifacts = self.run / 'flows/flow'; self.artifacts.mkdir(parents=True)
        self.plan = plan()
        (self.artifacts / 'demo.json').write_text(json.dumps({'label': 'Original payment'}))
        entries = [{'request': {'method': a['method'], 'url': self.plan['origin'] + a['path']},
                    'response': {'status': 200, 'content': {'text': json.dumps({
                        'order': {'totalReturned': 0, 'originalAmount': 100,
                                  'refund': {'requestedAmount': 100}}})}}}
                   for a in self.plan['actions']]
        (self.artifacts / 'recording.har').write_text(json.dumps({'log': {'entries': entries}}))

    def test_raw_support_resolves_and_fabricated_value_or_pointer_fails(self):
        with patch('backend.runtime.endpoint_catalog.validate_recorded_input'):
            validate_support(self.plan, self.run, self.run.parent)
            for field, value in [('value', 'Invented text'), ('json_pointer', '/absent')]:
                invalid = deepcopy(self.plan); invalid['supporting_observations'][0][field] = value
                with self.subTest(field=field), self.assertRaises(ValueError):
                    validate_support(invalid, self.run, self.run.parent)

    def test_gate_checks_raw_support_before_execution(self):
        script = self.run / 'probe.py'
        invalid = deepcopy(self.plan); invalid['supporting_observations'][0]['value'] = 'fabricated'
        script.write_text('PROBE_PLAN = ' + repr(invalid))
        with patch('backend.runtime.endpoint_catalog.validate_recorded_input'), \
             patch('backend.runtime.endpoint_catalog.preflight_endpoints', return_value=None):
            errors = validate_plan_set([script], {}, run=self.run, root=self.run.parent)
        self.assertIn('contradicts', errors[0]['error'])

    def test_terminal_pending_is_not_completed(self):
        verification = deepcopy(self.plan)
        verification['actions'] = [{'request': {'method': a['method'], 'url': self.plan['origin'] + a['path']}}
                                   for a in self.plan['actions']]
        verification['after'] = {'response': {'order': {'refund': {'status': 'pending'}}}}
        self.assertIn('not reached', terminal_error(verification))
        verification['after']['response']['order']['refund']['status'] = 'completed'
        self.assertIsNone(terminal_error(verification))

    def test_invented_operand_nesting_rejected_before_first_probe(self):
        from backend.runtime.generated_probes import validate_recorded_operand_paths
        invalid = deepcopy(self.plan)
        invalid['invariant']['limit'] = ['order', 'requestedAmount']
        with self.assertRaisesRegex(ValueError, 'order.requestedAmount'):
            validate_recorded_operand_paths(invalid, self.run)
        invalid['invariant']['limit'] = ['order', 'refund', 'requestedAmount']
        validate_recorded_operand_paths(invalid, self.run)

    def test_numeric_path_regenerated_once_without_changing_actions_or_operand(self):
        from backend.runtime.probe_plan_correction import correct_probe_setups
        from backend.runtime.generated_probes import read_plan
        folder = self.run / 'mutations/flow'; folder.mkdir(parents=True)
        script = folder / 'probe.py'
        invalid = deepcopy(self.plan)
        invalid['invariant']['limit'] = ['order', 'requestedAmount']
        invalid['inferred_invariant']['predicate'] = deepcopy(invalid['invariant'])
        corrected = deepcopy(invalid)
        corrected['invariant']['limit'] = ['order', 'refund', 'requestedAmount']
        corrected['inferred_invariant']['predicate'] = deepcopy(corrected['invariant'])
        catalog = {'endpoints': [{'method': 'POST', 'path': self.plan['setup'],
                                 'origin': self.plan['origin'], 'authority': 'configured_setup'}]}
        (self.run / 'endpoint_catalog.json').write_text(json.dumps(catalog))
        for valid in (False, True):
            script.write_text('PROBE_PLAN = ' + repr(invalid), encoding='utf-8')
            proposal = AsyncMock(return_value={'plans': {'probe.py': corrected if valid else invalid}})
            with patch('backend.runtime.endpoint_catalog.load_endpoint_catalog', return_value=catalog), \
                 patch('backend.runtime.endpoint_catalog.validate_recorded_input'), \
                 patch('backend.runtime.endpoint_catalog.preflight_endpoints', return_value=None), \
                 patch('backend.runtime.probe_plan_correction.propose_setups', proposal):
                errors = asyncio.run(correct_probe_setups(SimpleNamespace(run_dir=str(self.run.parent)),
                    self.run, 'flow', {}, lambda _: None))
            self.assertEqual(proposal.await_count, 1)
            self.assertIn('order.requestedAmount', proposal.call_args.args[1]['errors'][0]['error'])
            self.assertEqual(bool(errors), not valid)
            self.assertEqual(read_plan(script)['actions'], invalid['actions'])
            self.assertEqual(read_plan(script)['invariant'], (corrected if valid else invalid)['invariant'])
        self.assertFalse(list(self.run.glob('reports/*/executions/*.json')))

    def test_missing_metadata_is_regenerated_and_verified_before_publication(self):
        from backend.runtime.probe_plan_correction import correct_probe_setups
        from backend.runtime.generated_probes import read_plan
        folder = self.run / 'mutations/flow'; folder.mkdir(parents=True)
        script = folder / 'probe.py'
        invalid = deepcopy(self.plan)
        metadata = {k: invalid.pop(k) for k in ('rule_origin', 'inferred_invariant', 'supporting_observations')}
        script.write_text('PROBE_PLAN = ' + repr(invalid))
        original = script.read_bytes()
        catalog = {'endpoints': [{'method': 'POST', 'path': self.plan['setup'],
                                 'origin': self.plan['origin'], 'authority': 'configured_setup'}]}
        (self.run / 'endpoint_catalog.json').write_text(json.dumps(catalog))
        for broken in (True, False):
            proposed = deepcopy(metadata)
            if broken:
                proposed['supporting_observations'][0]['value'] = 'fabricated'
            proposal = AsyncMock(return_value={'setups': {'probe.py': self.plan['setup']},
                                               'inference_metadata': {'probe.py': proposed}})
            with patch('backend.runtime.endpoint_catalog.load_endpoint_catalog', return_value=catalog), \
                 patch('backend.runtime.endpoint_catalog.preflight_endpoints', return_value=None), \
                 patch('backend.runtime.endpoint_catalog.validate_recorded_input'), \
                 patch('backend.runtime.probe_plan_correction.propose_setups', proposal):
                task = correct_probe_setups(SimpleNamespace(run_dir=self.run.parent),
                                            self.run, 'flow', {}, lambda _: None)
                if broken:
                    self.assertEqual(len(asyncio.run(task)), 1)
                    self.assertEqual(proposal.await_count, 1)
                    self.assertEqual(script.read_bytes(), original)
                else:
                    asyncio.run(task)
                    self.assertEqual(proposal.await_count, 1)
                    self.assertEqual(read_plan(script), self.plan)
        self.assertFalse((self.run / 'reports').exists())

    def test_both_saved_recording_pointer_errors_relocate_only_unique_exact_text(self):
        paragraph = 'paragraph : Price protection is not available while another order request is active.'
        elements = [['generic : Eligible', 'generic : "?"'],
                    [paragraph, 'heading "Price protection" [level=2]']]
        demo = {'workflow_timeline': {'ui_states': [
            {'step': 1, 'elements': elements[0]}, {'step': 4, 'elements': elements[1]}]}}
        (self.artifacts / 'demo.json').write_text(json.dumps(demo))
        facts = [{'id': f'UIR-001-F{i+1}', 'type': 'explicit_ui_text',
                  'provenance_type': 'explicit_visible_ui_text', 'source_run': 'flow',
                  'artifact': 'demo.json', 'step': step, 'text': text,
                  'json_pointer': f'/workflow_timeline/ui_states/{i}/elements/1'}
                 for i, (step, text) in enumerate([(1, 'generic : Eligible'), (4, paragraph)])]
        state = {'observed_ui_rules': [{'id': 'UIR-001', 'facts': facts}]}
        for fact in facts:
            with self.assertRaises(UIProvenanceError):
                _validate_ui_text(fact, 'flow', self.artifacts)
        corrected, changes = relocate_ui_pointers(state, self.artifacts)
        self.assertEqual(len(changes), 2)
        for fact in corrected['observed_ui_rules'][0]['facts']:
            _validate_ui_text(fact, 'flow', self.artifacts)
        self.assertTrue(all(f['json_pointer'].endswith('/1') for f in facts))
        demo['workflow_timeline']['ui_states'][0]['elements'].append(' generic : Eligible ')
        (self.artifacts / 'demo.json').write_text(json.dumps(demo))
        ambiguous, changes = relocate_ui_pointers(state, self.artifacts)
        self.assertEqual(len(changes), 1)
        with self.assertRaises(UIProvenanceError):
            _validate_ui_text(ambiguous['observed_ui_rules'][0]['facts'][0], 'flow', self.artifacts)
