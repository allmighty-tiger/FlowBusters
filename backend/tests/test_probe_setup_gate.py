import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from backend.runtime.endpoint_catalog import preflight_endpoints
from backend.runtime.generated_probes import render_plan, validate_plan_set
from backend.runtime.probe_plan_correction import correct_probe_setups


class SetupGateTests(unittest.IsolatedAsyncioTestCase):
    def test_setup_authorization_is_run_scoped_and_tamper_evident(self):
        from backend.runtime.endpoint_catalog import issue_setup_authorization, load_setup_authorization
        from backend.runtime.probe_executor import key_for
        key_for(self.run, create=True)
        issue_setup_authorization(self.run, self.run, 'http://example.test', ['/reset'])
        self.assertEqual(load_setup_authorization(self.run, self.run)['setup_paths'], ['/reset'])
        path = self.run / 'setup_authorization.json'
        data = json.loads(path.read_text())
        data['setup_paths'] = ['/different']
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'authentication failed'):
            load_setup_authorization(self.run, self.run)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)
        self.folder = self.run / 'mutations' / 'flow'
        self.folder.mkdir(parents=True)
        self.catalog = {'endpoints': [
            {'method': 'GET', 'origin': 'http://example.test', 'path': '/state', 'authority': 'validated_recording'},
            {'method': 'POST', 'origin': 'http://example.test', 'path': '/action', 'authority': 'validated_recording'},
            {'method': 'POST', 'origin': 'http://example.test', 'path': '/reset', 'authority': 'configured_setup'}]}
        self.plan = {'schema_version': 1, 'title': 'Test condition', 'origin': 'http://example.test',
                     'setup': '/state', 'state_read': '/state', 'claim_type': 'workflow',
                     'actions': [{'method': 'POST', 'path': '/action', 'body': {}}],
                     'predicate': 'unsupported_business_rule', 'unsupported_reason': 'No supported predicate'}
        for name in ('01.py', '02.py'):
            (self.folder / name).write_text(render_plan(self.plan))
        (self.run / 'endpoint_catalog.json').write_text(json.dumps(self.catalog))
        flow = self.run / 'flows' / 'flow'
        flow.mkdir(parents=True)
        for name in ('demo.json', 'recording.har'):
            (flow / name).write_text('{}')
        self.config = SimpleNamespace(run_dir=str(self.run))

    def test_compiled_plan_invalid_setup_caught_before_transport(self):
        self.assertIn('Unauthorized setup', preflight_endpoints(self.folder / '01.py', self.catalog))

    def test_missing_endpoint_and_method_rejected(self):
        plan = dict(self.plan, setup='/reset', actions=[{'method': 'POST', 'path': '/state', 'body': {}}])
        (self.folder / '01.py').write_text(render_plan(plan))
        self.assertIn('Endpoint selection error', preflight_endpoints(self.folder / '01.py', self.catalog))

    def test_removing_setup_does_not_establish_isolation(self):
        for p in self.folder.glob('*.py'):
            p.write_text(render_plan(dict(self.plan, setup=None)))
        self.assertEqual(len(validate_plan_set(list(self.folder.glob('*.py')), self.catalog)), 2)

    async def test_bounded_correction_preserves_actions_and_evidence(self):
        from backend.runtime.generated_probes import read_plan
        proposal = AsyncMock(return_value={'setups': {'01.py': '/reset', '02.py': '/reset'}})
        with patch('backend.runtime.endpoint_catalog.load_endpoint_catalog', return_value=self.catalog), \
             patch('backend.runtime.probe_plan_correction.propose_setups', proposal):
            await correct_probe_setups(self.config, self.run, 'flow', {}, lambda _: None)
        self.assertEqual(proposal.await_count, 1)
        for p in self.folder.glob('*.py'):
            self.assertEqual(read_plan(p), dict(self.plan, setup='/reset'))
        self.assertEqual((self.run / 'flows/flow/demo.json').read_text(), '{}')
        self.assertTrue((self.run / 'probe_plan_corrections/01.py').exists())

    async def test_failed_correction_keeps_scripts_and_creates_no_receipts(self):
        before = (self.folder / '01.py').read_bytes()
        proposal = AsyncMock(return_value={'setups': {'01.py': '/invented', '02.py': '/invented'}})
        with patch('backend.runtime.endpoint_catalog.load_endpoint_catalog', return_value=self.catalog), \
             patch('backend.runtime.probe_plan_correction.propose_setups', proposal):
            errors = await correct_probe_setups(self.config, self.run, 'flow', {}, lambda _: None)
        self.assertEqual(len(errors), 2)
        self.assertEqual(proposal.await_count, 1)
        self.assertEqual((self.folder / '01.py').read_bytes(), before)
        self.assertFalse((self.run / 'reports').exists())

    async def test_duplicate_prevention_cannot_borrow_amount_predicate(self):
        from backend.runtime.generated_probes import read_plan
        invalid = dict(self.plan, claim_type='duplicate_refund_prevention',
                       predicate='business_rule_must_hold',
                       invariant={'operator': 'sum_lte', 'terms': [['requested']], 'limit': ['original']})
        for script in self.folder.glob('*.py'):
            script.write_text('PROBE_PLAN = ' + repr(invalid))
        reason = 'An amount upper bound cannot establish request uniqueness or idempotency.'
        proposal = AsyncMock(return_value={'setups': {'01.py': '/reset', '02.py': '/reset'},
                                          'unsupported_reasons': {'01.py': reason, '02.py': reason}})
        with patch('backend.runtime.endpoint_catalog.load_endpoint_catalog', return_value=self.catalog), \
             patch('backend.runtime.probe_plan_correction.propose_setups', proposal):
            await correct_probe_setups(self.config, self.run, 'flow', {}, lambda _: None)
        result = read_plan(self.folder / '01.py')
        self.assertEqual(result['predicate'], 'unsupported_business_rule')
        self.assertEqual(result['claim_type'], invalid['claim_type'])
        self.assertEqual(result['actions'], invalid['actions'])

    async def test_no_authorized_reset_stops_without_agent_or_probe(self):
        self.catalog['endpoints'].pop()
        with patch('backend.runtime.endpoint_catalog.load_endpoint_catalog', return_value=self.catalog), \
             patch('backend.runtime.probe_plan_correction.propose_setups', AsyncMock()) as proposal:
            self.assertEqual(len(await correct_probe_setups(self.config, self.run, 'flow', {}, lambda _: None)), 2)
            proposal.assert_not_awaited()

    async def test_explicit_unsupported_downgrade_can_include_complete_plan_without_changing_claim(self):
        from backend.runtime.generated_probes import read_plan
        invalid = dict(self.plan, setup='/reset', predicate='business_rule_must_hold',
                       claim_type='input_trust', invariant={'operator': 'sum_lte',
                       'terms': [['value']], 'limit': ['limit']})
        reason = 'A numeric bound cannot establish which submitted fields were accepted.'
        for script in self.folder.glob('*.py'):
            script.write_text('PROBE_PLAN = ' + repr(invalid), encoding='utf-8')
        candidate = dict(invalid, predicate='unsupported_business_rule', invariant=None, unsupported_reason=reason)
        proposal = AsyncMock(return_value={'plans': {n: candidate for n in ('01.py', '02.py')},
                         'unsupported_reasons': {n: reason for n in ('01.py', '02.py')}})
        with patch('backend.runtime.endpoint_catalog.load_endpoint_catalog', return_value=self.catalog), \
             patch('backend.runtime.probe_plan_correction.propose_setups', proposal):
            errors = await correct_probe_setups(self.config, self.run, 'flow', {}, lambda _: None)
        self.assertEqual(errors, [])
        self.assertEqual(proposal.await_count, 1)
        self.assertEqual(read_plan(self.folder / '01.py'), candidate)
        self.assertEqual(read_plan(self.folder / '01.py')['actions'], invalid['actions'])

    async def test_invalid_queue_never_calls_executor(self):
        from backend.runtime.probe_executor import execute_run
        executor = AsyncMock()
        with patch('backend.runtime.endpoint_catalog.load_endpoint_catalog', return_value=self.catalog), \
             patch('backend.runtime.probe_executor.execute', executor):
            await execute_run(self.run, 'flow', self.run, lambda _: None)
        from backend.runtime.queue_status import load_rejections
        self.assertEqual(len(load_rejections(self.run, self.run)), 2)
        executor.assert_not_awaited()
        self.assertFalse(list((self.run / 'reports').glob('*/executions/*.json')))

    async def test_invalid_claim_type_does_not_escape_endpoint_preflight_or_block_next_plan(self):
        from backend.runtime.probe_executor import execute_run
        from backend.runtime.queue_status import load_rejections
        invalid = dict(self.plan, setup='/reset', predicate='business_rule_must_hold',
                       claim_type='duplicate_effect', invariant={'operator': 'sum_lte',
                       'terms': [['value']], 'limit': ['limit']})
        (self.folder / '01.py').write_text('PROBE_PLAN = ' + repr(invalid), encoding='utf-8')
        (self.folder / '02.py').write_text(render_plan(dict(self.plan, setup='/reset')), encoding='utf-8')
        executor = AsyncMock(return_value={'execution_id': 'next-probe', 'exit_code': 0,
                                          'parsed_result': {'verification': {'predicate': 'unsupported_business_rule'}}})
        with patch('backend.runtime.endpoint_catalog.load_endpoint_catalog', return_value=self.catalog), \
             patch('backend.runtime.probe_executor.execute', executor), \
             patch('backend.runtime.probe_executor.validate_probe_output', return_value={'status': 'reviewable'}):
            await execute_run(self.run, 'flow', self.run, lambda _: None)
        self.assertEqual(executor.await_count, 1)
        self.assertEqual(executor.call_args.args[0].name, '02.py')
        rejected = load_rejections(self.run, self.run)
        self.assertEqual([r['script'] for r in rejected], ['01.py'])
        self.assertIn('Only amount_bound', rejected[0]['error'])

    async def test_two_isolated_plans_get_valid_terminal_receipts(self):
        import hashlib
        import hmac
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        from backend.runtime.probe_executor import canonical, key_for, execute_run, load_receipts, validate_probe_output
        state = {'value': 0}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self):
                data = json.dumps({'value': state['value'], 'limit': 1}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.reply()

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                state['value'] = 0 if self.path == '/reset' else state['value'] + 1
                self.reply()

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            origin = f'http://127.0.0.1:{server.server_port}'
            catalog = {'schema_version': 1, 'flow_name': self.run.name, 'endpoints': [
                dict(row, origin=origin) for row in self.catalog['endpoints']]}
            catalog['signature'] = hmac.new(key_for(self.run, create=True), canonical(catalog), hashlib.sha256).hexdigest()
            (self.run / 'endpoint_catalog.json').write_text(json.dumps(catalog))
            (self.run / 'scope.json').write_text(json.dumps({'allowed_domains': [origin],
                'allowed_paths_prefix': ['*'], 'setup_paths': ['/reset']}))
            plan = dict(self.plan, origin=origin, setup='/reset', claim_type='amount_bound',
                        predicate='business_rule_must_hold',
                        invariant={'operator': 'sum_lte', 'terms': [['value']], 'limit': ['limit']})
            plan.update(rule_origin='AI_INFERRED', inferred_invariant={
                'statement': 'Value stays within its limit', 'predicate': plan['invariant'],
                'required_actions': [{'method': 'POST', 'path': '/action'}],
                'terminal_state': [{'path': ['value'], 'equals': 1}]},
                supporting_observations=[{'source_run': self.run.name, 'artifact': 'demo.json',
                                         'json_pointer': '/label', 'value': 'Limit'}])
            recording = self.run / 'flows' / self.run.name
            recording.mkdir(parents=True)
            (recording / 'demo.json').write_text(json.dumps({'label': 'Limit'}))
            (recording / 'recording.har').write_text(json.dumps({'log': {'entries': [
                {'request': {'method': 'POST', 'url': origin + '/action'},
                 'response': {'status': 200, 'content': {
                     'text': json.dumps({'value': 0, 'limit': 1})}}}]}}))
            from copy import deepcopy
            for index, script in enumerate(sorted(self.folder.glob('*.py')), 1):
                atomic = deepcopy(plan)
                atomic['actions'] *= index
                atomic['inferred_invariant']['required_actions'] *= index
                atomic['inferred_invariant']['terminal_state'][0]['equals'] = index
                script.write_text(render_plan(atomic))
            with patch('backend.runtime.endpoint_catalog.validate_recorded_input'):
                await execute_run(self.run, 'flow', self.run, lambda _: None)
                from backend.runtime.verification import normalize_report
                from backend.runtime.probe_executor import reconcile
                report = normalize_report(reconcile(
                    json.loads((self.run / 'reports/flow/findings.json').read_text()),
                    self.run / 'reports/flow', self.run))
            self.assertEqual(sorted(r['outcome'] for r in report['results']),
                             ['CONFIRMED', 'NOT_REPRODUCED'])
            receipts = load_receipts(self.run / 'reports/flow', self.run)
            self.assertEqual(len(receipts), 2)
            for receipt in receipts.values():
                self.assertEqual(receipt['exit_code'], 0)
                self.assertNotEqual(validate_probe_output(receipt, enforce_contract=True)['status'], 'invalid')
                verification = receipt['parsed_result']['verification']
                self.assertEqual(verification['before']['response']['value'], 0)
                final = verification['after']['response']['value']
                self.assertIn(final, (1, 2))
                self.assertIs(verification['violation']['observed'], final > 1)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
