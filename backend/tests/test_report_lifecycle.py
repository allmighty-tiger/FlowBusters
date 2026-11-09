import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.runtime.report_presentation import _draft_matches, _repeat_evidence, present_report


class ReportLifecycleTests(unittest.TestCase):
    def fixture(self, extra_rejected=False):
        inv = {'operator': 'sum_lte', 'terms': [['order', 'totalReturned']], 'limit': ['order', 'originalAmount']}
        def cap(n, amount, status='completed', endpoint='complete', http=200):
            return {'sequence': n, 'status_code': http, 'complete': True,
                    'request': {'method': 'POST', 'url': 'http://example.test/api/order/refund/' + endpoint, 'body': None},
                    'response': {'order': {'id': 'A', 'totalReturned': amount, 'originalAmount': 100,
                                            'refund': {'status': status}}}}
        actions = [cap(3, 0, 'pending', 'request', 201), cap(4, 100)]
        if extra_rejected:
            actions.append(cap(5, 100, endpoint='request', http=409))
        actions.append(cap(len(actions) + 3, 200))
        verification = {'before': cap(2, 0, 'none'), 'after': cap(8, 200), 'actions': actions,
                        'invariant': inv, 'resolved_invariant': inv,
                        'inferred_invariant': {'terminal_state': [{'path': ['order', 'refund', 'status'], 'equals': 'completed'}]}}
        return {'verification': verification}, {'parsed_result': {'verification': verification}}

    def test_repeat_grouping_requires_exact_evaluated_evidence_not_chain_identity(self):
        a, ar = self.fixture()
        b, br = self.fixture(True)
        first = _repeat_evidence(a, ar)
        second = _repeat_evidence(b, br)
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[1]['overpayment'], 100)
        self.assertEqual(first[1]['title'], 'Repeated refund completion causes a double payout')

    def test_distinct_predicate_terminal_field_or_successful_action_does_not_group(self):
        a, receipt = self.fixture()
        canonical = _repeat_evidence(a, receipt)[0]
        for kind in ('action', 'field', 'invariant'):
            b, br = deepcopy(a), deepcopy(receipt)
            v = br['parsed_result']['verification']
            if kind == 'action':
                for action in v['actions'][1:]:
                    action['request']['url'] = 'http://example.test/api/settle'
            elif kind == 'field':
                v['inferred_invariant']['terminal_state'] = [{'path': ['order', 'id'], 'equals': 'A'}]
            else:
                b['verification']['resolved_invariant']['terms'] = [['different']]
            result = _repeat_evidence(b, br)
            self.assertTrue(result is None or result[0] != canonical)

    def test_rejected_repeat_and_no_measured_increment_cannot_support_group(self):
        a, r = self.fixture()
        r['parsed_result']['verification']['actions'][-1]['status_code'] = 409
        self.assertIsNone(_repeat_evidence(a, r))
        a, r = self.fixture()
        r['parsed_result']['verification']['actions'][-1]['response']['order']['totalReturned'] = 100
        self.assertIsNone(_repeat_evidence(a, r))

    def test_generation_link_needs_exact_embedded_plan_and_never_hides_executed_claim(self):
        with TemporaryDirectory() as tmp:
            run = Path(tmp) / 'flow'; directory = run / 'mutations/flow'; directory.mkdir(parents=True)
            plan = {'predicate': 'unsupported_business_rule', 'invariant': None, 'title': 'Hypothesis'}
            artifact = directory / '01.plan.json'; artifact.write_text(json.dumps(plan))
            raw = ('PROBE_PLAN = ' + repr(plan)).encode()
            receipt = {'script': '01.py', 'script_source_base64': base64.b64encode(raw).decode(),
                       'script_sha256': hashlib.sha256(raw).hexdigest()}
            draft = {'script': '01.plan.json', 'verification': {'predicate': 'unsupported_business_rule'}}
            self.assertTrue(_draft_matches(draft, receipt, run))
            for field in ('before', 'actions', 'after'):
                changed = deepcopy(draft); changed['verification'][field] = {}
                self.assertFalse(_draft_matches(changed, receipt, run))
            artifact.write_text(json.dumps(dict(plan, title='Different')))
            self.assertFalse(_draft_matches(draft, receipt, run))
            self.assertFalse(_draft_matches(draft, dict(receipt, script_sha256='bad'), run))

    def test_five_outcomes_keep_verdicts_and_two_reproductions_one_impact(self):
        findings, receipts, results = [], {}, []
        statuses = ['CONFIRMED', 'NEEDS_REVIEW', 'CONFIRMED', 'NEEDS_REVIEW', 'NOT_REPRODUCED']
        for i, status in enumerate(statuses, 1):
            finding, receipt = self.fixture(i == 3)
            finding.update(id=str(i), execution_id=str(i), script=f'{i:02}.py', verification_status=status)
            receipt.update(script=f'{i:02}.py', source='MUTATION_SCRIPT', execution_id=str(i))
            receipts[str(i)] = receipt
            findings.append(finding)
            results.append({'execution_id': str(i), 'outcome': status, 'script': f'{i:02}.py'})
        findings[0]['associated_agent_drafts'] = [{'id': 'existing-draft'}]
        original = {'findings': findings, 'results': results, 'summary': {'unique_vulnerabilities': 2}}
        baseline = deepcopy(original)
        with patch('backend.runtime.report_presentation.load_receipts', return_value=receipts), \
             patch('backend.runtime.report_presentation.receipt_evidence_error', return_value=None):
            report = present_report(original, Path('/tmp/runs/flow/reports/flow/findings.json'), Path('/tmp'))
        self.assertEqual(original, baseline)
        view = report['report_presentation']
        self.assertEqual(len(view['probes']), 5)
        self.assertEqual([p['verdict'] for p in view['probes']], statuses)
        self.assertEqual(len(view['issues']), 1)
        self.assertEqual(view['issues'][0]['probes'], ['Probe 01', 'Probe 03'])
        self.assertEqual(report['findings'][0]['associated_agent_drafts'], [{'id': 'existing-draft'}])
        self.assertEqual(report['summary']['observed_security_issues'], 1)

    def test_untrusted_receipt_cannot_create_observed_repeat_group(self):
        finding, receipt = self.fixture()
        finding.update(id='A', execution_id='a', verification_status='CONFIRMED')
        receipt.update(script='a.py', source='MUTATION_SCRIPT')
        with patch('backend.runtime.report_presentation.load_receipts', return_value={'a': receipt}), \
             patch('backend.runtime.report_presentation.receipt_evidence_error', return_value='Invalid trace'):
            report = present_report({'findings': [finding], 'results': [{'execution_id': 'a', 'outcome': 'CONFIRMED'}], 'summary': {}},
                                    Path('/tmp/runs/flow/reports/flow/findings.json'), Path('/tmp'))
        self.assertNotIn('overpayment', report['report_presentation']['issues'][0])


class LifecycleIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from backend.tests.test_typed_probe_contract import TypedProbeContractTests
        TypedProbeContractTests.setUp(self)

    async def test_actual_receipt_absorbs_exact_plan_draft_without_rewriting_evidence(self):
        from backend.runtime.probe_contract import write_admitted_plan
        from backend.runtime.probe_executor import execute_run
        from backend.runtime.verification import load_report
        with patch('backend.runtime.endpoint_catalog.validate_recorded_input'), \
             patch('backend.runtime.endpoint_catalog.load_endpoint_catalog', return_value=self.catalog):
            (self.folder / '01.plan.json').write_text(json.dumps(self.plan))
            write_admitted_plan(self.folder / '01.py', self.plan, self.run, self.root, self.catalog)
            await execute_run(self.run, 'flow', self.root, lambda _: None)
            path = self.run / 'reports/flow/findings.json'
            data = json.loads(path.read_text())
            data['findings'].append({'id': 'DRAFT', 'source': 'MUTATION_SCRIPT', 'script': '01.plan.json',
                                    'title': self.plan['title'], 'verification': {
                                        'predicate': self.plan['predicate'], 'invariant': self.plan['invariant'],
                                        'execution': {'status': 'NOT_EXECUTED'}}})
            path.write_text(json.dumps(data))
            before = {p: p.read_bytes() for p in self.run.rglob('*') if p.is_file()}
            report = load_report(path)
            self.assertEqual(len(report['findings']), 1)
            self.assertEqual(report['unexecuted_hypotheses'], [])
            probe = report['report_presentation']['probes'][0]
            self.assertEqual(probe['script'], '01.py')
            self.assertEqual(probe['verdict'], 'CONFIRMED')
            self.assertEqual(probe['associated_agent_drafts'][0]['id'], 'DRAFT')
            self.assertEqual(before, {p: p.read_bytes() for p in self.run.rglob('*') if p.is_file()})
