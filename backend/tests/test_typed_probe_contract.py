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
    regeneration_feedback, validate_combination, write_admitted_plan, inspect_observations)
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

    async def test_unique_exact_pointer_repair_preserves_numeric_plan_and_draft(self):
        plan = deepcopy(self.plan)
        plan['supporting_observations'][0]['json_pointer'] = '/wrong'
        draft = self.folder / '01.plan.json'; draft.write_text(json.dumps(plan))
        original = draft.read_bytes()
        with patch('backend.runtime.endpoint_catalog.validate_recorded_input'), \
             patch('backend.runtime.endpoint_catalog.load_endpoint_catalog', return_value=self.catalog), \
             patch('backend.runtime.probe_plan_correction.propose_setups', AsyncMock()) as model:
            await admit_json_plans(self.config, self.run, 'flow', {}, lambda _: None)
        model.assert_not_awaited()
        self.assertEqual(read_plan(self.folder / '01.py'), self.plan)
        self.assertEqual(draft.read_bytes(), original)
        audit = json.loads((self.run / 'plan_admission/decisions.json').read_text())
        self.assertEqual(audit['decisions'][0]['pointer_repairs'][0]['repaired_pointer'], '/label')

    def test_ambiguous_matches_and_type_mismatch_are_not_repaired(self):
        (self.raw / 'demo.json').write_text(json.dumps({'a': 'same', 'b': 'same', 'c': 1}))
        plan = deepcopy(self.plan)
        for value, count in [('same', 2), (True, 0)]:
            plan['supporting_observations'][0].update(json_pointer='/wrong', value=value)
            with patch('backend.runtime.endpoint_catalog.validate_recorded_input'):
                corrected, repairs, errors = inspect_observations(plan, self.run, self.root)
            self.assertEqual(corrected, plan)
            self.assertEqual(repairs, [])
            self.assertEqual(len(errors[0]['exact_match_pointers']), count)

    def test_exact_pointer_repair_escapes_tokens(self):
        (self.raw / 'demo.json').write_text(json.dumps({'a/b~c': 'Measured amount and limit'}))
        with patch('backend.runtime.endpoint_catalog.validate_recorded_input'):
            corrected, repairs, errors = inspect_observations(self.plan, self.run, self.root)
        self.assertEqual(errors, [])
        self.assertEqual(corrected['supporting_observations'][0]['json_pointer'], '/a~1b~0c')
        self.assertEqual(len(repairs), 1)

    async def test_refund_demo_shaped_subset_and_state_map_references_regenerate(self):
        # Mirrors preserved refund-demo: a shortened content.text plus an agent
        # state-map reference. No fixture depends on ignored run files.
        plan = deepcopy(self.plan)
        raw = json.loads((self.raw / 'recording.har').read_text())['log']['entries'][0]['response']['content']['text']
        plan['supporting_observations'] = [
            {'source_run': 'flow', 'artifact': 'recording.har',
             'json_pointer': '/log/entries/0/response/content/text', 'value': '{"record":{"measured":0}}'},
            {'source_run': 'flow', 'artifact': 'flows/flow/state_map.json',
             'json_pointer': '/critical_endpoints/1', 'value': 'Single-use endpoint'}]
        draft = self.folder / '01.plan.json'; draft.write_text(json.dumps(plan))
        original = draft.read_bytes()
        corrected = deepcopy(plan)
        corrected['supporting_observations'] = [dict(plan['supporting_observations'][0], value=raw)]
        async def proposal(config, feedback, env):
            self.assertFalse(list(self.folder.glob('*.py')))
            issues = feedback['rejected_plans']['01.plan.json']['observation_errors']
            self.assertEqual(len(issues), 2)
            self.assertEqual(issues[0]['actual_value'], raw)
            self.assertEqual(issues[0]['exact_match_pointers'], [])
            self.assertIn('state_map.json', issues[1]['error'])
            return {'plans': {'01.plan.json': corrected}}
        with patch('backend.runtime.endpoint_catalog.validate_recorded_input'), \
             patch('backend.runtime.endpoint_catalog.load_endpoint_catalog', return_value=self.catalog), \
             patch('backend.runtime.probe_plan_correction.propose_setups', AsyncMock(side_effect=proposal)):
            await admit_json_plans(self.config, self.run, 'flow', {}, lambda _: None)
            await execute_run(self.run, 'flow', self.root, lambda _: None)
            report = load_report(self.run / 'reports/flow/findings.json')
        self.assertEqual(draft.read_bytes(), original)
        self.assertEqual(read_plan(self.folder / '01.py')['predicate'], 'business_rule_must_hold')
        self.assertEqual(report['results'][0]['outcome'], 'CONFIRMED')

    async def test_failed_numeric_correction_rejects_without_http_and_continues_valid_probe(self):
        invalid = deepcopy(self.plan)
        invalid['supporting_observations'][0]['value'] = 'invented'
        (self.folder / '01.plan.json').write_text(json.dumps(invalid))
        (self.folder / '02.plan.json').write_text(json.dumps(self.plan))
        with patch('backend.runtime.endpoint_catalog.validate_recorded_input'), \
             patch('backend.runtime.endpoint_catalog.load_endpoint_catalog', return_value=self.catalog), \
             patch('backend.runtime.probe_plan_correction.propose_setups', AsyncMock(return_value={'plans': {}})):
            await admit_json_plans(self.config, self.run, 'flow', {}, lambda _: None)
            self.assertFalse((self.folder / '01.py').exists())
            self.assertTrue((self.folder / '02.py').exists())
            await execute_run(self.run, 'flow', self.root, lambda _: None)
            report = load_report(self.run / 'reports/flow/findings.json')
        receipts = load_receipts(self.run / 'reports/flow', self.root)
        self.assertEqual([r['script'] for r in receipts.values()], ['02.py'])
        self.assertEqual(report['summary']['planned_executions'], 2)
        self.assertEqual(report['summary']['preflight_rejected'], 1)
        self.assertEqual(report['summary']['pending_execution'], 0)
        self.assertEqual(report['summary']['process_errors'], 0)
        self.assertEqual(report['assessment_status']['status'], 'COMPLETED_WITH_ERRORS')
        self.assertEqual(report['results'][0]['outcome'], 'CONFIRMED')

    async def test_model_cannot_downgrade_numeric_plan_to_hide_bad_support(self):
        from backend.runtime.probe_contract import review_only
        invalid = deepcopy(self.plan)
        invalid['supporting_observations'][0]['value'] = 'invented'
        (self.folder / '01.plan.json').write_text(json.dumps(invalid))
        proposal = {'plans': {'01.plan.json': review_only(self.plan, 'bad references')}}
        with patch('backend.runtime.endpoint_catalog.validate_recorded_input'), \
             patch('backend.runtime.endpoint_catalog.load_endpoint_catalog', return_value=self.catalog), \
             patch('backend.runtime.probe_plan_correction.propose_setups', AsyncMock(return_value=proposal)):
            await admit_json_plans(self.config, self.run, 'flow', {}, lambda _: None)
        self.assertFalse((self.folder / '01.py').exists())
        audit = json.loads((self.run / 'plan_admission/decisions.json').read_text())
        self.assertEqual(audit['decisions'][0]['status'], 'REJECTED')
        self.assertIn('cannot be downgraded', audit['decisions'][0]['rejection_error'])

    async def test_refund_demo_chains_respect_declared_pending_terminal_state(self):
        # Controlled local fixture, not the live application or a saved assessment.
        order = {}
        def reset():
            order.clear()
            order.update(originalAmount=100, totalReturned=0,
                         refund={'status': 'none', 'requestedAmount': 0, 'completedAmount': 0})
        reset()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def reply(self, status=200):
                body = json.dumps({'order': order}).encode()
                self.send_response(status); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_GET(self): self.reply()
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                status = 200
                if self.path == '/reset': reset()
                elif self.path == '/refund/request':
                    if order['refund']['status'] != 'none': status = 409
                    else: order['refund'].update(status='pending', requestedAmount=100); status = 201
                elif self.path == '/refund/complete':
                    if order['refund']['status'] == 'none': status = 409
                    else:
                        order['refund']['status'] = 'completed'
                        order['refund']['completedAmount'] += 100
                        order['totalReturned'] += 100
                else: status = 404
                self.reply(status)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        origin = f'http://127.0.0.1:{server.server_port}'
        rows = [('POST', '/reset'), ('GET', '/state'), ('POST', '/refund/request'), ('POST', '/refund/complete')]
        catalog = {'endpoints': [{'method': m, 'origin': origin, 'path': p,
                    'authority': 'configured_setup' if p == '/reset' else 'validated_recording'} for m, p in rows]}
        (self.run / 'scope.json').write_text(json.dumps({'allowed_domains': [origin], 'allowed_paths_prefix': ['*']}))
        raw = json.dumps({'order': order})
        (self.raw / 'recording.har').write_text(json.dumps({'log': {'entries': [
            {'request': {'method': m, 'url': origin + p}, 'response': {'status': 200, 'content': {'text': raw}}}
            for m, p in rows[1:]]}}))
        cases = [(['request', 'complete', 'complete'], {}, 'totalReturned', 'CONFIRMED'),
                 (['request', 'complete', 'request', 'complete'], {}, 'totalReturned', 'CONFIRMED'),
                 (['request', 'complete'], {'amount': 9999, 'completedAmount': 9999}, 'totalReturned', 'NOT_REPRODUCED'),
                 (['request'], {'amount': 9999, 'requestedAmount': 9999}, 'requestedAmount', 'NOT_REPRODUCED'),
                 (['request', 'request'], {}, 'requestedAmount', 'NOT_REPRODUCED')]
        for index, (chain, injected, field, expected) in enumerate(cases, 1):
            plan = deepcopy(self.plan)
            term = ['order', field] if field == 'totalReturned' else ['order', 'refund', field]
            inv = {'operator': 'sum_lte', 'terms': [term], 'limit': ['order', 'originalAmount']}
            actions = [{'method': 'POST', 'path': '/refund/' + action,
                        'body': injected if i == len(chain) - 1 else {}} for i, action in enumerate(chain)]
            plan.update(origin=origin, invariant=inv, actions=actions,
                title=('Total returned exceeds original payment' if field == 'totalReturned'
                       else 'Requested amount exceeds original payment'),
                supporting_observations=[{'source_run': 'flow', 'artifact': 'recording.har',
                    'json_pointer': '/log/entries/0/response/content/text', 'value': raw}],
                inferred_invariant={'statement': ('Total returned should not exceed the original payment'
                    if field == 'totalReturned' else 'Requested amount should not exceed the original payment'),
                    'predicate': inv, 'required_actions': [{'method': a['method'], 'path': a['path']} for a in actions],
                    'terminal_state': [{'path': ['order', 'refund', 'status'],
                                        'equals': 'completed' if 'complete' in chain else 'pending'}]})
            (self.folder / f'{index:02}.plan.json').write_text(json.dumps(plan))
        with patch('backend.runtime.endpoint_catalog.validate_recorded_input'), \
             patch('backend.runtime.endpoint_catalog.load_endpoint_catalog', return_value=catalog):
            await admit_json_plans(self.config, self.run, 'flow', {}, lambda _: None)
            await execute_run(self.run, 'flow', self.root, lambda _: None)
            report = load_report(self.run / 'reports/flow/findings.json')
        results = sorted(report['results'], key=lambda row: row['script'])
        self.assertEqual([r['outcome'] for r in results], [case[3] for case in cases],
                         [r.get('verification_reason') for r in results])
        # The admitted claim is the requested-amount bound, not arbitrary input
        # trust or uniqueness. Pending is its explicitly declared terminal state.
        for result in results[-2:]:
            self.assertEqual(result['verification']['after']['response']['order']['refund']['requestedAmount'], 100)
            self.assertEqual(result['verification']['after']['response']['order']['refund']['status'], 'pending')
            self.assertIs(result['verification']['violation']['observed'], False)
        self.assertEqual(results[-1]['verification']['actions'][-1]['status_code'], 409)
        receipts = load_receipts(self.run / 'reports/flow', self.root)
        self.assertEqual(len(receipts), 5)
        for receipt in receipts.values():
            self.assertEqual(validate_probe_output(receipt, enforce_contract=True)['status'], 'valid')
            self.assertEqual(receipt['parsed_result']['verification']['after']['request']['method'], 'GET')
        self.assertEqual(report['summary']['observed_security_issues'], 1)
        self.assertEqual(report['assessment_status']['status'], 'COMPLETED')

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
