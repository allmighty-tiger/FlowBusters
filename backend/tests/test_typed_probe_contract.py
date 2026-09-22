import asyncio
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from backend.runtime.probe_contract import (ALLOWED_COMBINATIONS, observed_context,
    regeneration_feedback, validate_combination, write_admitted_plan)
from backend.runtime.generated_probes import read_plan
from backend.runtime.probe_plan_correction import admit_json_plans
from backend.runtime.probe_executor import execute_run, load_receipts, validate_probe_output
from backend.runtime.verification import load_report


class TypedProbeContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.run = self.root / 'runs/flow'
        self.folder = self.run / 'mutations/flow'; self.folder.mkdir(parents=True)
        self.raw = self.run / 'flows/flow'; self.raw.mkdir(parents=True)
        self.state = {'measured': 0, 'limit': 1}
        state = self.state
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def reply(self):
                body = json.dumps({'record': state}).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_GET(self): self.reply()
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                state['measured'] = 0 if self.path == '/reset' else state['measured'] + 2
                self.reply()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        origin = f'http://127.0.0.1:{server.server_port}'
        self.catalog = {'endpoints': [{'method': method, 'origin': origin, 'path': path,
            'authority': 'configured_setup' if path == '/reset' else 'validated_recording'}
            for method, path in [('POST', '/reset'), ('GET', '/state'), ('POST', '/settle')]]}
        (self.run / 'endpoint_catalog.json').write_text(json.dumps(self.catalog))
        (self.run / 'scope.json').write_text(json.dumps({'allowed_domains': [origin], 'allowed_paths_prefix': ['*']}))
        (self.raw / 'demo.json').write_text(json.dumps({'label': 'Measured amount and limit'}))
        (self.raw / 'recording.har').write_text(json.dumps({'log': {'entries': [{'request': {
            'method': 'POST', 'url': origin + '/settle'}, 'response': {'status': 200,
            'content': {'text': json.dumps({'record': self.state})}}}]}}))
        invariant = {'operator': 'sum_lte', 'terms': [['record', 'measured']], 'limit': ['record', 'limit']}
        self.plan = {'schema_version': 2, 'title': 'Measured amount exceeds its inferred limit',
            'origin': origin, 'setup': '/reset', 'state_read': '/state', 'claim_type': 'amount_bound',
            'predicate': 'business_rule_must_hold', 'invariant': invariant,
            'actions': [{'method': 'POST', 'path': '/settle', 'body': {}}], 'rule_origin': 'AI_INFERRED',
            'inferred_invariant': {'statement': 'Measured amount should not exceed the recorded limit',
                'predicate': deepcopy(invariant), 'required_actions': [{'method': 'POST', 'path': '/settle'}],
                'terminal_state': [{'path': ['record', 'limit'], 'equals': 1}]},
            'supporting_observations': [{'source_run': 'flow', 'artifact': 'demo.json',
                'json_pointer': '/label', 'value': 'Measured amount and limit'}]}
        self.config = SimpleNamespace(run_dir=str(self.root))

    def test_exact_enums_and_combinations(self):
        for pair in ALLOWED_COMBINATIONS:
            validate_combination(dict(pair, invariant=self.plan['invariant'] if pair['operator'] else None))
        for claim, predicate in [('amount_bound', 'unsupported_business_rule'),
                                  ('unsupported', 'business_rule_must_hold'), ('made_up', 'made_up')]:
            with self.assertRaises(ValueError): validate_combination({'claim_type': claim, 'predicate': predicate})

    def test_incompatible_pair_rejected_before_python_file_exists(self):
        path = self.folder / 'invalid.py'
        plan = dict(self.plan, claim_type='workflow')
        with self.assertRaisesRegex(ValueError, 'Unknown typed'):
            write_admitted_plan(path, plan, self.run, self.root, self.catalog)
        self.assertFalse(path.exists())
        self.assertEqual(list(self.folder.glob('*.py')), [])

    def test_regeneration_feedback_has_pairs_original_claim_and_exact_numeric_paths(self):
        with patch('backend.runtime.endpoint_catalog.validate_recorded_input'):
            context = observed_context(self.run, self.root)
        feedback = regeneration_feedback(self.plan, 'bad pair', context)
        self.assertEqual(feedback['allowed_combinations'], ALLOWED_COMBINATIONS)
        self.assertEqual(feedback['original_security_hypothesis'], self.plan['title'])
        self.assertEqual(feedback['rejected_combination']['predicate'], 'business_rule_must_hold')
        self.assertEqual({tuple(f['response_path']) for f in context['observed_fields']},
                         {('record', 'measured'), ('record', 'limit')})

    async def test_one_correction_before_creation_preserves_hypothesis(self):
        invalid = dict(self.plan, claim_type='invented_amount_claim')
        draft = self.folder / '01.plan.json'; draft.write_text(json.dumps(invalid))
        raw = draft.read_bytes()
        async def proposal(config, feedback, env):
            self.assertFalse(list(self.folder.glob('*.py')))
            self.assertEqual(feedback['rejected_plans']['01.plan.json']['allowed_combinations'], ALLOWED_COMBINATIONS)
            return {'plans': {'01.plan.json': self.plan}}
        mock = AsyncMock(side_effect=proposal)
        with patch('backend.runtime.endpoint_catalog.validate_recorded_input'), \
             patch('backend.runtime.endpoint_catalog.load_endpoint_catalog', return_value=self.catalog), \
             patch('backend.runtime.probe_plan_correction.propose_setups', mock):
            await admit_json_plans(self.config, self.run, 'flow', {}, lambda _: None)
        self.assertEqual(mock.await_count, 1)
        self.assertEqual(read_plan(self.folder / '01.py'), self.plan)
        self.assertEqual(draft.read_bytes(), raw)

    async def test_generic_numeric_violation_confirms_from_real_receipt(self):
        with patch('backend.runtime.endpoint_catalog.validate_recorded_input'), \
             patch('backend.runtime.endpoint_catalog.load_endpoint_catalog', return_value=self.catalog):
            write_admitted_plan(self.folder / '01.py', self.plan, self.run, self.root, self.catalog)
            await execute_run(self.run, 'flow', self.root, lambda _: None)
            report = load_report(self.run / 'reports/flow/findings.json')
        receipts = load_receipts(self.run / 'reports/flow', self.root)
        self.assertEqual(len(receipts), 1)
        receipt = next(iter(receipts.values()))
        self.assertEqual(validate_probe_output(receipt, enforce_contract=True)['status'], 'valid')
        self.assertEqual(receipt['parsed_result']['verification']['after']['response']['record']['measured'], 2)
        self.assertIs(receipt['parsed_result']['verification']['violation']['observed'], True)
        self.assertEqual(report['findings'][0]['verification_status'], 'CONFIRMED')

    async def test_invented_predicate_cannot_create_check_error_or_numeric_confirmation(self):
        plan = dict(self.plan, title='Request uniqueness', claim_type='uniqueness', predicate='unique_request')
        (self.folder / '01.plan.json').write_text(json.dumps(plan))
        proposal = AsyncMock(return_value={'plans': {}})
        with patch('backend.runtime.endpoint_catalog.validate_recorded_input'), \
             patch('backend.runtime.endpoint_catalog.load_endpoint_catalog', return_value=self.catalog), \
             patch('backend.runtime.probe_plan_correction.propose_setups', proposal):
            await admit_json_plans(self.config, self.run, 'flow', {}, lambda _: None)
            await execute_run(self.run, 'flow', self.root, lambda _: None)
            report = load_report(self.run / 'reports/flow/findings.json')
        admitted = read_plan(self.folder / '01.py')
        self.assertEqual(admitted['claim_type'], 'unsupported')
        self.assertEqual(admitted['predicate'], 'unsupported_business_rule')
        self.assertEqual(admitted['title'], 'Request uniqueness')
        self.assertIsNone(admitted['invariant'])
        self.assertEqual(report['findings'][0]['verification_status'], 'NEEDS_REVIEW')
        self.assertEqual(report['results'][0]['outcome'], 'NEEDS_REVIEW')
        self.assertEqual(report['summary']['errors'], 0)
        self.assertEqual(proposal.await_count, 1)
