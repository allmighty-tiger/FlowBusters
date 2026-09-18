import base64
from copy import deepcopy
import unittest

from backend.runtime.business_requirements import apply_backend_requirements
from backend.runtime.coverage_plans import receipt_probe_origin, render_probe
from backend.runtime.verification import normalize_report, _is_auth_finding
from backend.tests.test_application_identity_smoke import record


class ReportIssueGroupTests(unittest.TestCase):
    def test_pending_payout_is_review_in_findings_and_results(self):
        f = self.finding('pending', 30)
        f['verification']['violation']['observed'] = False
        f['verification']['after']['response']['actual']['order']['refund'] = {'status': 'pending'}
        r = self.normalize([f])
        self.assertEqual(r['findings'][0]['verification_status'], 'NEEDS_REVIEW')
        self.assertEqual(r['results'][0]['outcome'], 'NEEDS_REVIEW')
        self.assertEqual(r['summary']['controls_held'], 0)
        self.assertEqual(r['summary']['coverage_gaps'], 1)
        self.assertIn('completion action', r['findings'][0]['verification_reason'])

    def test_amount_tamper_negative_bound_does_not_test_input_trust(self):
        f = self.finding('amount', 0)
        f['mutation_type'] = 'PRICING_TAMPER'
        f['verification']['violation']['observed'] = False
        f['verification']['actions'][0]['request']['body'] = {'requestedAmount': 999999}
        r = self.normalize([f])
        self.assertEqual(r['findings'][0]['verification_status'], 'NEEDS_REVIEW')
        self.assertEqual(r['results'][0]['outcome'], 'NEEDS_REVIEW')
        self.assertEqual(r['summary']['controls_held'], 0)
        self.assertIn('request-to-expected-state', r['findings'][0]['verification_reason'])

    def test_backend_source_does_not_mean_broken_access_control(self):
        self.assertFalse(_is_auth_finding({'source': 'BACKEND_COVERAGE'}))
        self.assertTrue(_is_auth_finding({'mutation_type': 'BAC_CHECK'}))
    def finding(self, identifier, amount=130, path='/api/order/price-adjustment'):
        f = record(amount, True)
        f.update(id=identifier, execution_id='exec-' + identifier, script=identifier + '.py', source='MUTATION_SCRIPT')
        f['verification']['before']['response']['actual']['order'].update(id='1042', totalReturned=0)
        f['verification']['actions'][0]['request'] = {'method': 'POST', 'url': 'http://localhost:3000' + path,
                                                      'body': {'adjustmentAmount': amount}}
        return f

    def normalize(self, findings):
        report = {'findings': findings, 'results': [dict(deepcopy(f), finding_id=f['id']) for f in findings]}
        apply_backend_requirements(report, trusted_run_id='arbitrary-flow', trusted_identity={
            'application_id': 'northstar-market', 'identity_version': '2', 'requirements_version': '2026-09-17'})
        return normalize_report(report)

    def test_payload_amount_variants_are_one_issue_two_findings_two_results(self):
        a, b = self.finding('A', 130), self.finding('B', 999999)
        b['verification']['actions'][0]['request']['body']['currentPrice'] = -999999
        r = self.normalize([a, b])
        self.assertEqual(r['summary']['confirmed'], 2)
        self.assertEqual(r['summary']['unique_vulnerabilities'], 1)
        self.assertEqual(r['summary']['deduplicated_primary_chains'], 0)
        self.assertEqual(r['summary']['execution_result_counts']['confirmed'], 2)
        self.assertEqual(r['vulnerabilities'][0]['execution_ids'], ['exec-A', 'exec-B'])
        self.assertEqual(len(r['findings']), 2)

    def test_distinct_endpoint_chains_or_invariants_do_not_group(self):
        a, b = self.finding('A'), self.finding('B', path='/api/order/cancel')
        self.assertEqual(self.normalize([a, b])['summary']['unique_vulnerabilities'], 2)
        b = self.finding('B')
        b['verification']['invariant']['terms'] = [['order', 'otherTotal']]
        r = self.normalize([a, b])
        self.assertEqual(r['summary']['confirmed'], 1)
        self.assertEqual(r['summary']['needs_review'], 1)

    def test_404_or_405_is_incomplete_coverage_not_a_held_control(self):
        for status in (404, 405):
            f = self.finding('missing', 100, '/api/order/refund')
            f['verification']['violation']['observed'] = False
            f['verification']['actions'][0]['status_code'] = status
            r = self.normalize([f])
            self.assertEqual(r['summary']['needs_review'], 1)
            self.assertEqual(r['summary']['controls_held'], 0)
            self.assertEqual(r['summary']['coverage_gaps'], 1)
            self.assertEqual(r['results'][0]['outcome'], 'NEEDS_REVIEW')
            self.assertIn(str(status), r['findings'][0]['verification_reason'])
            self.assertNotIn('remediation', r['findings'][0])

    def test_409_remains_a_negative_predicate_result(self):
        f = self.finding('rejected', 100)
        f['verification']['violation']['observed'] = False
        f['verification']['actions'][0]['status_code'] = 409
        r = self.normalize([f])
        self.assertEqual(r['summary']['controls_held'], 1)
        self.assertEqual(r['summary']['coverage_gaps'], 0)

    def test_origin_cannot_be_claimed_by_filename_or_result_flag(self):
        r = {'script': '00_coverage_price_amount_bound.py', 'source': 'MUTATION_SCRIPT',
             'parsed_result': {'probe_origin': 'BACKEND_COVERAGE'},
             'script_source_base64': base64.b64encode(b'print("fake")').decode()}
        self.assertEqual(receipt_probe_origin(r), 'AI_PROBE')

    def test_legacy_template_match_retains_honest_unknown_authorship(self):
        f = self.finding('legacy')
        code = render_probe('http://localhost:3000', '/api/demo/reset', '/api/order',
                            [{'endpoint': '/api/order/price-adjustment', 'body': {'adjustmentAmount': 130}}],
                            f['verification']['invariant'], 'price amount bound')
        r = {'source': 'MUTATION_SCRIPT', 'script': '00_coverage_price_amount_bound.py',
             'script_source_base64': base64.b64encode(code.replace('\n', '\r\n').encode()).decode()}
        self.assertEqual(receipt_probe_origin(r), 'LEGACY_COVERAGE_TEMPLATE')
