import unittest
from copy import deepcopy

from backend.runtime.business_requirements import apply_backend_requirements, load_business_requirements, trusted_report_run_id
from backend.runtime.verification import classify

INVARIANT = {'operator': 'sum_lte', 'terms': [['order', 'totalReturned']], 'limit': ['order', 'originalAmount']}
NORTHSTAR = {'application_id': 'northstar-market', 'identity_version': '1',
             'requirements_version': '2026-09-17', 'product': 'Northstar Market'}

def finding(observed=True):
    state = {'order': {'originalAmount': 100, 'totalReturned': 130}}
    return {'title': 'Combined return exceeds the order amount', 'severity': 'Critical',
            'verification': {'predicate': 'business_rule_must_hold', 'rule': {'source': 'agent_inference'},
                'before': {'sequence': 1, 'status_code': 200, 'complete': True, 'request': {'method': 'GET'}, 'response': {'actual': deepcopy(state)}},
                'actions': [{'sequence': 2, 'status_code': 200, 'complete': True, 'request': {'method': 'POST'}, 'response': {'actual': deepcopy(state)}}],
                'after': {'sequence': 3, 'status_code': 200, 'complete': True, 'request': {'method': 'GET'}, 'response': {'actual': deepcopy(state)}},
                'invariant': deepcopy(INVARIANT), 'violation': {'observed': observed, 'description': '130 > 100'}}}

class BusinessRequirementTests(unittest.TestCase):
    def test_registry_records_product_versioned_requirement(self):
        rule = load_business_requirements()[0]
        self.assertEqual(rule['id'], 'NSM-TOTAL-RETURN-CAP-2026-09-17')
        self.assertEqual(rule['application_id'], 'northstar-market')
        self.assertEqual(rule['requirements_version'], '2026-09-17')
        self.assertNotIn('allowed_run_ids', rule)

    def test_multiple_runs_of_same_authenticated_application_match(self):
        for run_id in ('northstar-a', 'northstar-cross-flow-b'):
            record, result = finding(), finding()
            report = {'run_timestamp': '2026-09-18T00:00:00Z', 'findings': [record], 'results': [result]}
            apply_backend_requirements(report, trusted_run_id=run_id, trusted_identity=NORTHSTAR)
            for item in (record, result):
                self.assertEqual(classify(item)[0], 'CONFIRMED')
                self.assertEqual(item['backend_requirement']['application_mode'], 'prospective_evaluation')

    def test_other_application_on_same_origin_does_not_match(self):
        record = finding()
        apply_backend_requirements({'target_url': 'http://localhost:3000', 'findings': [record], 'results': []},
                                   trusted_run_id='other-run', trusted_identity={'application_id': 'other-app', 'identity_version': '1', 'requirements_version': '2026-09-17'})
        self.assertNotIn('backend_requirement', record)
        self.assertEqual(classify(record)[0], 'NEEDS_REVIEW')

    def test_missing_or_agent_spoofed_identity_fails_closed(self):
        for report in ({'findings': [finding()], 'results': []},
                       {'application_identity': NORTHSTAR, 'validated': True, 'findings': [finding()], 'results': []}):
            apply_backend_requirements(report, trusted_run_id='legacy-run')
            self.assertNotIn('backend_requirement', report['findings'][0])

    def test_exact_predicate_required(self):
        record = finding(); record['verification']['invariant']['terms'] = [['order', 'refund', 'completedAmount']]
        apply_backend_requirements({'findings': [record], 'results': []}, trusted_run_id='run', trusted_identity=NORTHSTAR)
        self.assertNotIn('backend_requirement', record)

    def test_wrong_requirements_version_fails_closed(self):
        record = finding(); identity = dict(NORTHSTAR, requirements_version='2026-09-18')
        apply_backend_requirements({'findings': [record], 'results': []},
                                   trusted_run_id='run', trusted_identity=identity)
        self.assertNotIn('backend_requirement', record)

    def test_earlier_evidence_is_explicitly_retrospective(self):
        record = finding(); report = {'run_timestamp': '2026-09-16T12:00:00Z', 'findings': [record], 'results': []}
        apply_backend_requirements(report, trusted_run_id='migrated-run', trusted_identity=NORTHSTAR)
        self.assertTrue(record['backend_requirement']['retrospective'])
        self.assertEqual(record['backend_requirement']['application_mode'], 'retrospective_evidence_evaluation')

    def test_agent_flag_is_removed(self):
        record = finding(); record['_backend_requirement_valid'] = True; record['backend_requirement'] = {'id': 'FAKE'}
        record['verification']['invariant']['limit'] = ['order', 'other']
        apply_backend_requirements({'findings': [record], 'results': []}, trusted_run_id='run', trusted_identity=NORTHSTAR)
        self.assertNotIn('_backend_requirement_valid', record)
        self.assertNotIn('backend_requirement', record)

    def test_free_form_user_or_specification_reference_is_not_authority(self):
        for source in ('user', 'specification'):
            record = finding(); record['verification']['rule'] = {
                'source': source, 'reference': 'agent-written link or sentence'}
            self.assertEqual(classify(record)[0], 'NEEDS_REVIEW')

    def test_trusted_run_id_requires_canonical_matching_report_path(self):
        self.assertEqual(trusted_report_run_id('runs/example/reports/example/findings.json'), 'example')
        self.assertIsNone(trusted_report_run_id('runs/example/reports/other/findings.json'))

if __name__ == '__main__':
    unittest.main()
