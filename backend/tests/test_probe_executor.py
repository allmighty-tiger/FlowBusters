import asyncio
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest

from backend.runtime.probe_executor import (
    execute, execute_run, load_receipts, normalize_response,
    preflight_script_contract, reconcile, resolve_numeric_path, trace_error,
    validate_probe_output,
)
from backend.runtime.verification import load_report, normalize_report


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'runs' / 'sample'
        self.reports = self.run / 'reports' / 'sample'
        self.reports.mkdir(parents=True)
        self.state = {'originalAmount': 100, 'priceAdjustment': 0, 'completedRefund': 0, 'totalReturned': 0}
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps(state).encode())
            def do_POST(self):
                if self.path.endswith('/demo/reset'):
                    state.update(priceAdjustment=0, completedRefund=0, totalReturned=0)
                if self.path.endswith('price-adjustment'): state['priceAdjustment'] = 30
                if self.path.endswith('refund/complete'): state['completedRefund'] = 100
                state['totalReturned'] = state['priceAdjustment'] + state['completedRefund']
                self.do_GET()
            def do_DELETE(self):
                state['parts'] = [part for part in state['parts'] if part['id'] != 1]
                self.do_GET()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f'http://127.0.0.1:{server.server_port}'
        (self.run / 'scope.json').write_text(json.dumps({'allowed_domains': [self.url], 'allowed_paths_prefix': ['/api/'], 'block_production': True}))

    def script(self, complete=True, client='urllib'):
        script = self.run / 'probe.py'
        code = '''import urllib.request, json, sys
url = URL
counter = 0
def call(method, path):
    global counter
    counter += 1
    with urllib.request.urlopen(urllib.request.Request(url + path, method=method)) as response:
        value = json.loads(response.read())
        status = response.status
    return {'sequence': counter, 'status_code': status, 'complete': True,
            'request': {'method': method, 'url': url + path, 'body': None}, 'response': value}
before = call('GET', '/api/order')
actions = [call('POST', '/api/order/price-adjustment'), call('POST', '/api/order/refund/request')]
COMPLETE
after = call('GET', '/api/order')
v = {'predicate': 'business_rule_must_hold', 'rule': {'source': 'specification', 'reference': 'Reimbursement must not exceed original payment'},
     'before': before, 'actions': actions, 'after': after,
     'invariant': {'operator': 'sum_lte', 'terms': [['priceAdjustment'], ['completedRefund']], 'limit': ['originalAmount']},
     'violation': {'observed': after['response']['totalReturned'] > 100, 'description': 'Captured reimbursement exceeds original payment'}}
print('diagnostic output')
print('stderr preserved', file=sys.stderr)
print(json.dumps({'mutation_type': 'STATE_INTERLEAVING', 'verification': v, 'status_code': 200, 'url': url + '/api/order'}))
'''.replace('URL', repr(self.url)).replace('COMPLETE', "actions.append(call('POST', '/api/order/refund/complete'))" if complete else '')
        if client != 'urllib':
            start = code.index('    with urllib.request.urlopen')
            end = code.index("    return {'sequence'", start)
            if client == 'httpx_async':
                replacement = "    import asyncio, httpx\n    async def fetch():\n        async with httpx.AsyncClient() as client:\n            return await client.request(method, url + path)\n    response = asyncio.run(fetch())\n"
            else:
                replacement = f'    import {client}\n    response = {client}.request(method, url + path)\n'
            code = code[:start] + replacement + '    value = response.json()\n    status = response.status_code\n' + code[end:]
        script.write_text(code, encoding='utf-8')
        return script

    def run_script(self, complete=True, **kwargs):
        script = self.script(complete)
        original = script.read_bytes()
        receipt = asyncio.run(execute(script, self.reports, root=self.root, cwd=self.run, **kwargs))
        self.assertEqual(script.read_bytes(), original)
        return receipt

    def sequenced_httpx_script(self, *, multiple_resets=False, predicate='business_rule_must_hold',
                               include_invariant=True):
        script = self.run / 'sequenced.py'
        predicate_fields = "'predicate': " + repr(predicate)
        if predicate == 'unsupported_business_rule':
            predicate_fields += ", 'unsupported_reason': 'The current verifier cannot express a status-transition exclusivity rule'"
        invariant = (", 'invariant': {'operator': 'sum_lte', 'terms': [['priceAdjustment'], ['completedRefund']], 'limit': ['originalAmount']}"
                     if include_invariant else '')
        supplementary = ""
        if multiple_resets:
            supplementary = """
        second_reset = await call(client, 'POST', '/api/demo/reset')
        second_before = await call(client, 'GET', '/api/order')
        second_action = await call(client, 'POST', '/api/order/price-adjustment')
        second_after = await call(client, 'GET', '/api/order')
        setup.append(second_reset)
        supplementary_scenarios = {'second': {'before': second_before, 'actions': [second_action], 'after': second_after}}
"""
        code = f'''import asyncio, json, httpx
url = {self.url!r}
async def call(client, method, path):
    response = await client.request(method, url + path)
    return {{'sequence': response.extensions['flowbusters_sequence'],
            'status_code': response.status_code, 'complete': True,
            'request': {{'method': method, 'url': url + path, 'body': None}},
            'response': response.json()}}
async def probe():
    async with httpx.AsyncClient() as client:
        setup = [await call(client, 'POST', '/api/demo/reset')]
        before = await call(client, 'GET', '/api/order')
        action = await call(client, 'POST', '/api/order/price-adjustment')
        after = await call(client, 'GET', '/api/order')
        supplementary_scenarios = {{}}
{supplementary}
        verification = {{{predicate_fields}, 'rule': {{'source': 'specification', 'reference': 'Returned value must not exceed payment'}},
            'setup': setup, 'before': before, 'actions': [action], 'after': after{invariant},
            'violation': {{'observed': False, 'description': 'Returned value stayed within the payment'}} ,
            'supplementary_scenarios': supplementary_scenarios}}
        print(json.dumps({{'title': 'Sequenced probe', 'mutation_type': 'STATE_INTERLEAVING',
                          'url': url + '/api/order', 'verification': verification}}))
asyncio.run(probe())
'''
        script.write_text(code, encoding='utf-8')
        return script

    def finding(self, receipt):
        return {'id': 'F-1', 'title': 'Reimbursement overlap', 'source': receipt['source'], 'script': receipt['script'],
                'triggered_by': receipt.get('triggered_by'),
                'execution_id': receipt['execution_id'], 'verification': deepcopy(receipt['parsed_result']['verification'])}

    def planned_receipts(self, total, executed, *, complete=True):
        template = self.script(complete).read_text(encoding='utf-8')
        directory = self.run / 'mutations' / 'sample'
        directory.mkdir(parents=True, exist_ok=True)
        receipts = []
        for index in range(total):
            script = directory / f'{index + 1:02d}_planned.py'
            script.write_text(template, encoding='utf-8')
            if index < executed:
                receipts.append(asyncio.run(execute(
                    script, self.reports, root=self.root, cwd=self.run)))
        return receipts

    def test_actual_complete_chain_confirmed_and_raw_preserved(self):
        receipt = self.run_script()
        self.assertEqual(receipt['exit_code'], 0)
        self.assertIn('diagnostic output', receipt['stdout'])
        self.assertIn('stderr preserved', receipt['stderr'])
        self.assertEqual(json.loads(receipt['stdout'].splitlines()[-1]), receipt['parsed_result'])
        self.assertEqual(len(receipt['trace']), 5)
        self.assertEqual(receipt['trace'][-1]['response']['totalReturned'], 130)
        report = reconcile({'findings': [self.finding(receipt)], 'results': [{'status_code': 999}]}, self.reports, self.root)
        result = normalize_report(report)
        self.assertEqual(result['findings'][0]['verification_status'], 'NEEDS_REVIEW')
        self.assertIn('not backend-authenticated rule authority',
                      result['findings'][0]['verification_reason'])
        self.assertEqual(result['results'][0]['status_code'], 200)
        self.assertEqual(len(result['findings'][0]['evidence']['requests']), 5)

    def test_harness_sequence_accounts_for_reset_before_state_read(self):
        script = self.sequenced_httpx_script()
        receipt = asyncio.run(execute(script, self.reports, root=self.root, cwd=self.run))
        verification = receipt['parsed_result']['verification']
        self.assertEqual(verification['setup'][0]['sequence'], 1)
        self.assertEqual(verification['before']['sequence'], 2)
        self.assertEqual(receipt['contract_validation']['status'], 'valid')
        self.assertIsNone(trace_error(self.finding(receipt), receipt))

    def test_harness_sequence_accounts_for_multiple_resets(self):
        script = self.sequenced_httpx_script(multiple_resets=True)
        receipt = asyncio.run(execute(script, self.reports, root=self.root, cwd=self.run))
        verification = receipt['parsed_result']['verification']
        self.assertEqual([item['sequence'] for item in verification['setup']], [1, 5])
        self.assertEqual([event['sequence'] for event in receipt['trace']], list(range(1, 9)))
        self.assertEqual(receipt['contract_validation']['status'], 'valid')

    def test_each_scenario_invariant_uses_its_own_after_state(self):
        script = self.sequenced_httpx_script(multiple_resets=True)
        code = script.read_text(encoding='utf-8').replace(
            "second_action = await call(client, 'POST', '/api/order/price-adjustment')",
            "second_action = await call(client, 'POST', '/api/order/price-adjustment')\n        extra = await call(client, 'POST', '/api/order/refund/complete')").replace(
                "'actions': [second_action]", "'actions': [second_action, extra]")
        script.write_text(code, encoding='utf-8')
        receipt = asyncio.run(execute(script, self.reports, root=self.root, cwd=self.run))
        verification = receipt['parsed_result']['verification']
        second = verification['supplementary_scenarios']['second']
        second['invariant'] = deepcopy(verification['invariant'])
        second['violation'] = {'observed': True, 'description': 'Second scenario only'}
        self.assertEqual(verification['after']['response']['totalReturned'], 30)
        self.assertEqual(second['after']['response']['totalReturned'], 130)
        self.assertEqual(validate_probe_output(receipt, enforce_contract=True)['status'], 'valid')
        second['violation']['observed'] = False
        invalid = validate_probe_output(receipt, enforce_contract=True)
        self.assertEqual(invalid['category'], 'evidence_contract_error')
        self.assertIn('scenario=second', invalid['error'])
        self.assertIn('AFTER sequence=9', invalid['error'])
        self.assertIn('values=[30, 100]', invalid['error'])
        self.assertIn('value=100', invalid['error'])
        self.assertIn('recomputed=True', invalid['error'])
        second.pop('invariant'); second.pop('violation')
        verification['violation']['observed'] = True
        invalid = validate_probe_output(receipt, enforce_contract=True)
        self.assertIn('scenario=primary', invalid['error'])
        self.assertIn('AFTER sequence=4', invalid['error'])
        self.assertIn('recomputed=False', invalid['error'])

    def test_multi_scenario_repeated_requests_account_for_every_sequence(self):
        script = self.sequenced_httpx_script(multiple_resets=True)
        receipt = asyncio.run(execute(script, self.reports, root=self.root, cwd=self.run))
        verification = receipt['parsed_result']['verification']
        primary = [
            *verification['setup'], verification['before'],
            *verification['actions'], verification['after'],
        ]
        second = verification['supplementary_scenarios']['second']
        supplementary = [second['before'], *second['actions'], second['after']]
        self.assertEqual(
            sorted(capture['sequence'] for capture in [*primary, *supplementary]),
            [event['sequence'] for event in receipt['trace']],
        )
        self.assertEqual(
            validate_probe_output(receipt, enforce_contract=True)['status'], 'valid')

        omitted = deepcopy(receipt)
        omitted['parsed_result']['verification']['supplementary_scenarios']['second'][
            'actions'] = []
        validation = validate_probe_output(omitted, enforce_contract=True)
        self.assertEqual(validation['category'], 'evidence_contract_error')
        self.assertIn('signed transport sequences [7] are not represented', validation['error'])

        misplaced = deepcopy(receipt)
        moved = misplaced['parsed_result']['verification'].pop('supplementary_scenarios')
        misplaced['parsed_result']['supplementary_scenarios'] = list(moved.values())
        validation = validate_probe_output(misplaced, enforce_contract=True)
        self.assertEqual(validation['category'], 'evidence_contract_error')
        self.assertIn(
            'inside verification, not a top-level result field',
            validation['error'],
        )

    def test_top_level_supplementary_scenarios_fail_preflight(self):
        script = self.run / 'top_level_supplementary.py'
        script.write_text(
            "result = {'verification': {}, 'supplementary_scenarios': []}\n",
            encoding='utf-8')
        self.assertIn(
            'inside verification, not a top-level result field',
            preflight_script_contract(script),
        )

    def test_complete_supported_invariant_passes_output_gate(self):
        receipt = asyncio.run(execute(self.sequenced_httpx_script(), self.reports,
                                      root=self.root, cwd=self.run))
        self.assertEqual(validate_probe_output(receipt, enforce_contract=True)['status'], 'valid')

    def test_missing_invariant_is_explicit_contract_error(self):
        script = self.sequenced_httpx_script(include_invariant=False)
        self.assertIn('requires a supported executable invariant', preflight_script_contract(script))
        receipt = asyncio.run(execute(script, self.reports, root=self.root, cwd=self.run))
        validation = receipt['contract_validation']
        self.assertEqual(validation['category'], 'evidence_contract_error')
        self.assertIn('requires a supported executable invariant', validation['error'])
        report = normalize_report(reconcile(
            {'findings': [self.finding(receipt)]}, self.reports, self.root))
        self.assertEqual(report['summary']['process_errors'], 0)
        self.assertEqual(report['summary']['evidence_contract_errors'], 1)
        self.assertEqual(report['summary']['trace_mismatches'], 0)
        self.assertEqual(report['results'][0]['outcome'], 'NEEDS_REVIEW')

    def test_scenario_helper_unpack_has_statically_visible_invariant(self):
        script = self.run / 'helper.py'
        code = '''
async def scenario():
    return {'invariant': {'operator': 'sum_lte', 'terms': [['order', 'totalReturned']],
                          'limit': ['order', 'originalAmount']}, 'violation': {'observed': False}}
async def probe():
    primary = await scenario()
    verification = {'predicate': 'business_rule_must_hold', **primary}
'''
        script.write_text(code, encoding='utf-8')
        self.assertIsNone(preflight_script_contract(script))
        script.write_text(code.replace("'invariant':", "'not_an_invariant':"), encoding='utf-8')
        self.assertIn('requires a supported executable invariant', preflight_script_contract(script))

    def test_literal_null_violation_is_rejected_before_execution(self):
        script = self.sequenced_httpx_script()
        code = script.read_text(encoding='utf-8').replace(
            "'observed': False", "'observed': None")
        script.write_text(code, encoding='utf-8')
        error = preflight_script_contract(script)
        self.assertIn('literal violation.observed must be boolean, not NoneType', error)
        self.assertIn('captured AFTER response', error)

    def test_unsupported_rule_stays_reviewable_without_invented_invariant(self):
        script = self.sequenced_httpx_script(predicate='unsupported_business_rule',
                                             include_invariant=False)
        receipt = asyncio.run(execute(script, self.reports, root=self.root, cwd=self.run))
        self.assertEqual(receipt['contract_validation']['status'], 'reviewable')
        normalized = normalize_report(reconcile(
            {'findings': [self.finding(receipt)]}, self.reports, self.root))
        self.assertEqual(normalized['findings'][0]['verification_status'], 'NEEDS_REVIEW')
        self.assertIn('current verifier cannot express',
                      normalized['findings'][0]['verification_reason'])

    def test_new_contract_keeps_strict_signed_sequence_matching(self):
        receipt = asyncio.run(execute(self.sequenced_httpx_script(), self.reports,
                                      root=self.root, cwd=self.run))
        receipt['parsed_result']['verification']['before']['request']['url'] += '?fabricated=1'
        validation = validate_probe_output(receipt, enforce_contract=True)
        self.assertEqual(validation['category'], 'evidence_contract_error')
        self.assertIn('does not match its signed transport event', validation['error'])

    def test_new_contract_rejects_out_of_order_capture_sequences(self):
        receipt = self.run_script()
        receipt['parsed_result']['verification']['actions'].reverse()
        validation = validate_probe_output(receipt, enforce_contract=True)
        self.assertEqual(validation['category'], 'evidence_contract_error')
        self.assertIn('unique and increasing', validation['error'])

    def test_new_contract_requires_response_root_invariant_paths(self):
        receipt = asyncio.run(execute(self.sequenced_httpx_script(), self.reports,
                                      root=self.root, cwd=self.run))
        verification = receipt['parsed_result']['verification']
        verification['after']['response'] = {'order': verification['after']['response']}
        receipt['trace'][-1]['response'] = deepcopy(verification['after']['response'])
        validation = validate_probe_output(receipt, enforce_contract=True)
        self.assertEqual(validation['category'], 'evidence_contract_error')
        self.assertIn('full AFTER response root', validation['error'])

    def test_execution_summary_ignores_stale_agent_counts(self):
        self.planned_receipts(7, 7)
        report_path = self.reports / 'findings.json'
        report_path.write_text(json.dumps({
            'findings': [],
            'summary': {'pending_execution': 7, 'completed_executions': 0,
                        'confirmed': 999, 'controls_held': 999},
        }), encoding='utf-8')
        summary = load_report(report_path)['summary']
        self.assertEqual(summary['planned_executions'], 7)
        self.assertEqual(summary['execution_attempts'], 7)
        self.assertEqual(summary['completed_executions'], 7)
        self.assertEqual(summary['pending_execution'], 0)
        self.assertEqual(summary['missing_receipts'], 0)
        self.assertEqual(summary['confirmed'], 0)

    def test_partial_and_untrusted_receipts_remain_pending(self):
        receipts = self.planned_receipts(7, 3)
        report_path = self.reports / 'findings.json'
        report_path.write_text(json.dumps({'findings': [], 'summary': {
            'pending_execution': 0}}), encoding='utf-8')
        summary = load_report(report_path)['summary']
        self.assertEqual((summary['completed_executions'], summary['pending_execution']), (3, 4))

        # Add a fourth signed receipt, then invalidate its immutable contents.
        fourth_script = self.run / 'mutations' / 'sample' / '04_planned.py'
        fourth = asyncio.run(execute(fourth_script, self.reports, root=self.root, cwd=self.run))
        receipt_path = self.reports / 'executions' / f"{fourth['execution_id']}.json"
        receipt_path.write_text(receipt_path.read_text(encoding='utf-8').replace(
            'diagnostic output', 'tampered output'), encoding='utf-8')
        summary = load_report(report_path)['summary']
        self.assertEqual(summary['completed_executions'], 3)
        self.assertEqual(summary['pending_execution'], 4)
        self.assertEqual(summary['untrusted_receipts'], 1)

    def test_deduplication_preserves_execution_attempt_counts(self):
        receipts = self.planned_receipts(2, 2, complete=False)
        findings = []
        for index, receipt in enumerate(receipts, 1):
            finding = self.finding(receipt)
            finding.update({'id': f'F-{index:03d}', 'cwe': ['CWE-841'],
                            'url_tested': self.url + '/api/order'})
            findings.append(finding)
        report_path = self.reports / 'findings.json'
        report_path.write_text(json.dumps({'findings': findings}), encoding='utf-8')
        report = load_report(report_path)
        self.assertEqual(len(report['findings']), 1)
        self.assertEqual(report['summary']['finding_count'], 1)
        self.assertEqual(report['summary']['execution_attempts'], 2)
        self.assertEqual(report['summary']['completed_executions'], 2)
        self.assertEqual(report['summary']['controls_held'], 2)
        self.assertEqual(report['summary']['deduplicated_execution_results'], 1)

    def test_missing_complete_cannot_be_invented_by_report(self):
        receipt = self.run_script(False)
        finding = self.finding(receipt)
        action = deepcopy(finding['verification']['actions'][-1])
        action['request']['url'] = self.url + '/api/order/refund/complete'
        finding['verification']['actions'].append(action)
        finding['verification']['after']['response']['totalReturned'] = 130
        finding['verification']['violation']['observed'] = True
        result = normalize_report(reconcile({'findings': [finding]}, self.reports, self.root))
        self.assertEqual(result['findings'][0]['verification_status'], 'NEEDS_REVIEW')
        self.assertNotIn('/api/order/refund/complete', json.dumps(result['findings'][0]['execution_trace']))

    def test_report_cannot_spoof_observed_ui_provenance(self):
        script = self.script()
        code = script.read_text(encoding='utf-8').replace(
            "{'source': 'specification', 'reference': 'Reimbursement must not exceed original payment'}",
            "{'source': 'observed_ui', 'reference': 'legacy prose', 'validated': True}")
        script.write_text(code, encoding='utf-8')
        receipt = asyncio.run(execute(script, self.reports, root=self.root, cwd=self.run))
        finding = self.finding(receipt)
        finding['validated'] = True
        finding['_rule_provenance_valid'] = True
        result = normalize_report(reconcile({'findings': [finding]}, self.reports, self.root))
        self.assertIsNone(result['findings'][0]['_provenance_error'])
        self.assertEqual(result['findings'][0]['verification_status'], 'NEEDS_REVIEW')
        self.assertFalse(result['findings'][0]['_rule_provenance_valid'])

    def test_negative_signed_execution_ignores_missing_rule_provenance(self):
        script = self.script(False)
        code = script.read_text(encoding='utf-8').replace(
            "{'source': 'specification', 'reference': 'Reimbursement must not exceed original payment'}",
            "{'source': 'observed_ui', 'reference': 'legacy prose'}")
        script.write_text(code, encoding='utf-8')
        receipt = asyncio.run(execute(script, self.reports, root=self.root, cwd=self.run))
        finding = self.finding(receipt)
        result = normalize_report(reconcile({'findings': [finding]}, self.reports, self.root))
        self.assertEqual(result['findings'][0]['verification_status'], 'NOT_REPRODUCED',
                         result['findings'][0].get('verification_reason'))

    def test_authenticated_receipt_replaces_stale_not_executed_draft(self):
        receipt = self.run_script()
        finding = self.finding(receipt)
        draft_verification = {
            'predicate': 'business_rule_must_hold',
            'rule': {'source': 'agent_inference', 'reference': 'draft only'},
            'invariant': deepcopy(receipt['parsed_result']['verification']['invariant']),
            'execution': {'status': 'NOT_EXECUTED', 'missing_precondition': 'backend pending'},
        }
        finding['verification'] = deepcopy(draft_verification)
        finding['execution'] = deepcopy(draft_verification['execution'])
        result = normalize_report(reconcile({'findings': [finding]}, self.reports, self.root))
        normalized = result['findings'][0]
        self.assertEqual(normalized['verification_status'], 'NEEDS_REVIEW')
        self.assertEqual(normalized['agent_draft_verification'], draft_verification)
        self.assertEqual(normalized['agent_draft_execution']['status'], 'NOT_EXECUTED')
        self.assertIn('before', normalized['verification'])
        self.assertNotIn('execution', normalized)

    def test_post_execution_contradiction_is_not_replaced_by_receipt(self):
        receipt = self.run_script()
        finding = self.finding(receipt)
        finding['verification']['actions'][0]['request']['body'] = {'invented': True}
        result = normalize_report(reconcile({'findings': [finding]}, self.reports, self.root))
        normalized = result['findings'][0]
        self.assertEqual(normalized['verification_status'], 'NEEDS_REVIEW')
        self.assertEqual(normalized['_provenance_error'],
                         'Finding verification differs from raw script output')

    def test_declared_supplement_without_invariant_is_partial_coverage(self):
        script = self.script(False)
        source = script.read_text(encoding='utf-8')
        source = source.replace(
            "print('diagnostic output')",
            "v['supplementary_scenarios'] = {'cancel_after_price_adjustment': "
            "{'before': before, 'actions': actions, 'after': after}}\nprint('diagnostic output')")
        source = source.replace(
            "{'source': 'specification', 'reference': 'Reimbursement must not exceed original payment'}",
            "{'source': 'agent_inference', 'reference': 'cross_flow_candidates.json XF-002'}")
        script.write_text(source, encoding='utf-8')
        receipt = asyncio.run(execute(script, self.reports, root=self.root, cwd=self.run))
        finding = self.finding(receipt)
        finding['cwe'] = ['CWE-841']
        report = reconcile({
            'findings': [finding],
            '_cross_flow_candidates': [{
                'id': 'XF-002',
                'before_action_after': {'actions': [
                    'Sub-scenario A: refund then cancel',
                    'Sub-scenario B (separate reset): adjustment then cancel',
                ]},
            }],
        }, self.reports, self.root)
        normalized = normalize_report(report)
        self.assertEqual(len(normalized['findings']), 1)
        self.assertEqual(normalized['summary']['partial_coverage'], 1)
        self.assertEqual(normalized['partial_coverage'][0]['status'], 'PARTIAL_COVERAGE')
        self.assertIn('independent invariant', normalized['partial_coverage'][0]['reason'])

    def test_setup_path_probe_is_visible_but_not_a_security_finding(self):
        receipt = self.run_script(False)
        finding = self.finding(receipt)
        finding['title'] = 'Replay through demo-reset fixture'
        report = {'findings': [finding], '_setup_paths': ['/api/demo/reset']}
        normalized = normalize_report(reconcile(report, self.reports, self.root))
        self.assertEqual(normalized['findings'], [])
        self.assertEqual(len(normalized['excluded_setup_executions']), 1)
        self.assertEqual(len(normalized['results']), 1)
        self.assertEqual(normalized['results'][0]['presentation_status'],
                         'EXCLUDED_SETUP_PATH')

    def test_fabricated_stdout_does_not_override_transport(self):
        receipt = self.run_script(False)
        record = self.finding(receipt)
        record['verification']['actions'][-1]['request']['url'] = self.url + '/api/order/refund/complete'
        receipt['parsed_result']['verification'] = deepcopy(record['verification'])
        self.assertIn('absent', trace_error(record, receipt))

    def test_actual_capture_wrapper_matches_direct_trace_body(self):
        receipt = self.run_script()
        finding = self.finding(receipt)
        finding['verification']['before']['response'] = {
            'actual': finding['verification']['before']['response']}
        receipt['parsed_result']['verification'] = deepcopy(finding['verification'])
        self.assertIsNone(trace_error(finding, receipt))

    def test_api_actual_field_with_siblings_is_not_unwrapped(self):
        payload = {'actual': {'value': 1}, 'expected': {'value': 2}}
        self.assertEqual(normalize_response(payload, 200), (payload, 200))

    def test_status_body_capture_wrapper_matches(self):
        receipt = self.run_script()
        finding = self.finding(receipt)
        before = finding['verification']['before']
        before['response'] = {'status_code': before['status_code'],
                              'body': json.dumps(before['response'])}
        before.pop('status_code')
        receipt['parsed_result']['verification'] = deepcopy(finding['verification'])
        self.assertIsNone(trace_error(finding, receipt))

    def test_actual_capture_wrapper_with_different_body_fails(self):
        receipt = self.run_script()
        finding = self.finding(receipt)
        finding['verification']['before']['response'] = {'actual': {'different': True}}
        receipt['parsed_result']['verification'] = deepcopy(finding['verification'])
        self.assertIn('absent', trace_error(finding, receipt))

    def test_invariant_exact_root_path(self):
        value, path = resolve_numeric_path({'order': {'totalReturned': 130}},
                                           ['order', 'totalReturned'])
        self.assertEqual((value, path), (130, ['order', 'totalReturned']))

    def test_invariant_single_envelope_compatibility_path(self):
        value, path = resolve_numeric_path({'order': {'totalReturned': 130}},
                                           ['totalReturned'])
        self.assertEqual((value, path), (130, ['order', 'totalReturned']))

    def test_invariant_multiple_envelopes_rejected(self):
        with self.assertRaises(KeyError):
            resolve_numeric_path({'order': {'totalReturned': 130},
                                  'data': {'totalReturned': 130}}, ['totalReturned'])

    def test_invariant_recursive_guessing_rejected(self):
        with self.assertRaises(KeyError):
            resolve_numeric_path({'payload': {'order': {'totalReturned': 130}}},
                                 ['totalReturned'])

    def test_invariant_exact_and_fallback_conflict_rejected(self):
        response = {'order': {'totalReturned': 130,
                              'order': {'totalReturned': 30}}}
        with self.assertRaises(ValueError):
            resolve_numeric_path(response, ['order', 'totalReturned'])

    def test_northstar_envelope_invariant_and_resolved_metadata(self):
        receipt = self.run_script()
        finding = self.finding(receipt)
        verification = finding['verification']
        after = verification['after']
        after['response'] = {'order': {'originalAmount': 100,
                                       'priceAdjustment': 30,
                                       'completedRefund': 100,
                                       'totalReturned': 130}}
        verification['invariant'] = {'operator': 'sum_lte',
            'terms': [['priceAdjustment'], ['completedRefund']],
            'limit': ['originalAmount']}
        verification['violation']['observed'] = True
        receipt['parsed_result']['verification'] = deepcopy(verification)
        receipt['trace'][-1]['response'] = deepcopy(after['response'])
        self.assertIsNone(trace_error(finding, receipt))
        self.assertEqual(verification['resolved_invariant'], {
            'operator': 'sum_lte',
            'terms': [['order', 'priceAdjustment'], ['order', 'completedRefund']],
            'limit': ['order', 'originalAmount']})

    def test_followup_is_separately_attributed(self):
        receipt = self.run_script(source='VERIFICATION_PROBE', triggered_by='original.py')
        finding = self.finding(receipt)
        self.assertIsNone(trace_error(finding, receipt))
        finding['source'] = 'MUTATION_SCRIPT'
        self.assertIn('source', trace_error(finding, receipt))
        self.assertEqual(receipt['triggered_by'], 'original.py')

    def test_cross_flow_origin_binds_to_mutation_receipt(self):
        script = self.script()
        mutations = self.run / 'mutations' / 'sample'
        mutations.mkdir(parents=True)
        script.replace(mutations / script.name)
        draft = {'findings': [{'id': 'XF-1', 'title': 'Cross-flow reimbursement overlap',
                               'source': 'CROSS_FLOW', 'source_runs': ['one', 'two'],
                               'script': script.name}]}
        (self.reports / 'findings.json').write_text(json.dumps(draft), encoding='utf-8')
        asyncio.run(execute_run(self.run, 'sample', self.root, lambda message: None))
        saved = json.loads((self.reports / 'findings.json').read_text(encoding='utf-8'))
        finding = saved['findings'][0]
        self.assertEqual(finding['source'], 'MUTATION_SCRIPT')
        self.assertEqual(finding['analysis_source'], 'CROSS_FLOW')
        self.assertTrue(finding['execution_id'])
        report = normalize_report(reconcile(saved, self.reports, self.root))
        self.assertEqual(report['findings'][0]['verification_status'], 'NEEDS_REVIEW')

    def test_legacy_cross_flow_report_links_only_unique_signed_receipt(self):
        receipt = self.run_script()
        legacy = {'id': 'XF-1', 'title': 'Legacy cross-flow finding', 'source': 'CROSS_FLOW',
                  'script': receipt['script'],
                  'verification': deepcopy(receipt['parsed_result']['verification'])}
        report = normalize_report(reconcile({'findings': [legacy]}, self.reports, self.root))
        self.assertEqual(report['findings'][0]['execution_id'], receipt['execution_id'])
        self.assertEqual(report['findings'][0]['analysis_source'], 'CROSS_FLOW')
        self.assertEqual(report['findings'][0]['verification_status'], 'NEEDS_REVIEW')

    def test_tampered_receipt_and_legacy_report_cannot_confirm(self):
        receipt = self.run_script()
        path = self.reports / 'executions' / (receipt['execution_id'] + '.json')
        original = path.read_bytes()
        path.write_text(original.decode().replace('stderr preserved', 'stderr fabricated'))
        self.assertEqual(load_receipts(self.reports, self.root), {})
        report_path = self.reports / 'findings.json'
        report_path.write_text(json.dumps({'findings': [self.finding(receipt)], 'results': []}))
        self.assertEqual(load_report(report_path)['findings'][0]['verification_status'], 'NEEDS_REVIEW')

    def test_omitted_action_and_false_invariant_rejected(self):
        receipt = self.run_script()
        finding = self.finding(receipt)
        finding['verification']['actions'].pop(1)
        receipt['parsed_result']['verification'] = deepcopy(finding['verification'])
        self.assertIn('omitted', trace_error(finding, receipt))
        self.state.update(priceAdjustment=0, completedRefund=0, totalReturned=0)
        receipt = self.run_script(False)
        finding = self.finding(receipt)
        finding['verification']['violation']['observed'] = True
        receipt['parsed_result']['verification'] = deepcopy(finding['verification'])
        self.assertIn('contradicts', trace_error(finding, receipt))

    def test_timeout_retains_partial_output_and_rerun_preserves_receipts(self):
        script = self.run / 'slow.py'
        script.write_text("import time, sys\nprint('before timeout', flush=True)\nprint('error detail', file=sys.stderr, flush=True)\ntime.sleep(10)\n")
        first = asyncio.run(execute(script, self.reports, root=self.root, cwd=self.run, timeout=.5))
        self.assertEqual(first['stdout'].splitlines(), ['before timeout'])
        self.assertEqual(first['stderr'].splitlines(), ['error detail'])
        self.assertIsNotNone(first['error'])
        path = self.reports / 'executions' / (first['execution_id'] + '.json')
        original = path.read_bytes()
        second = asyncio.run(execute(script, self.reports, root=self.root, cwd=self.run, timeout=.5))
        self.assertNotEqual(first['execution_id'], second['execution_id'])
        self.assertEqual(path.read_bytes(), original)

    def test_nonzero_exit_preserved(self):
        script = self.run / 'failure.py'
        script.write_text("import sys\nprint('{\"outcome\": \"CONFIRMED\"}')\nprint('failure', file=sys.stderr)\nsys.exit(7)\n")
        receipt = asyncio.run(execute(script, self.reports, root=self.root, cwd=self.run))
        self.assertEqual(receipt['exit_code'], 7)
        result = normalize_report(reconcile({'findings': []}, self.reports, self.root))
        self.assertEqual(result['results'][0]['outcome'], 'CHECK_ERROR')

    def test_endpoint_attribution_cannot_name_unexecuted_request(self):
        receipt = self.run_script(False)
        finding = self.finding(receipt)
        finding['url_tested'] = self.url + '/api/order/refund/complete'
        self.assertIn('endpoint', trace_error(finding, receipt))

    def test_optional_backend_probe_has_its_own_signed_trace(self):
        from unittest.mock import patch
        from backend.runtime.probe_executor import execute_state_lock
        self.state.update(status='APPROVED', parts=[{'id': 1}, {'id': 2}])
        target = {'base': self.url, 'parent_path': '/api/order', 'children_key': 'parts',
                  'child_delete_paths': ['/api/parts'], 'lifecycle_field': 'status',
                  'lifecycle_posts': [], 'locked_states': ['APPROVED'], 'cookie': 'test=1', 'login': None}
        with patch('backend.runtime.state_lock_probe.find_child_mutation_target', return_value=target):
            fid = asyncio.run(execute_state_lock(self.run, 'sample', self.root, self.url))
        self.assertIsNotNone(fid)
        report = load_report(self.reports / 'findings.json')
        self.assertEqual(report['summary']['confirmed'], 1)
        self.assertEqual(report['results'][0]['source'], 'STATE_LOCK_PROBE')
        self.assertEqual(len(report['findings'][0]['execution_trace']), 5)

    def test_installed_http_clients_capture_complete_chain(self):
        import importlib.util
        installed = [name for name in ('httpx', 'requests') if importlib.util.find_spec(name)]
        if not installed:
            self.skipTest('Optional HTTPX/requests client adapters require the project runtime dependencies')
        if 'httpx' in installed:
            installed.append('httpx_async')
        for client in installed:
            with self.subTest(client=client):
                self.state.update(priceAdjustment=0, completedRefund=0, totalReturned=0)
                script = self.script(client=client)
                receipt = asyncio.run(execute(script, self.reports, root=self.root, cwd=self.run))
                self.assertEqual(receipt['exit_code'], 0, receipt['stderr'])
                self.assertIsNone(trace_error(self.finding(receipt), receipt))

    def test_child_process_calls_fail_closed(self):
        script = self.run / 'child.py'
        script.write_text("import subprocess, sys\nsubprocess.run([sys.executable, '-c', 'print(123)'])\n")
        receipt = asyncio.run(execute(script, self.reports, root=self.root, cwd=self.run))
        self.assertNotEqual(receipt['exit_code'], 0)
        self.assertIn('Probe subprocesses are unsupported', receipt['stderr'])


if __name__ == '__main__': unittest.main()
