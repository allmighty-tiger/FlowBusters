import unittest
from copy import deepcopy

from backend.runtime.business_requirements import (
    apply_backend_requirements,
    load_business_requirements,
    trusted_report_run_id,
)
from backend.runtime.verification import classify


INVARIANT = {
    'operator': 'sum_lte',
    'terms': [
        ['order', 'cancellation', 'reimbursementAmount'],
        ['order', 'refund', 'completedAmount'],
    ],
    'limit': ['order', 'originalAmount'],
}


def finding(observed=True):
    state = {'order': {'originalAmount': 100, 'cancellation': {'reimbursementAmount': 100},
                       'refund': {'completedAmount': 100}}}
    return {
        'title': 'Cancel then complete may reimburse twice',
        'verification': {
            'predicate': 'business_rule_must_hold',
            'rule': {'source': 'observed_ui', 'provenance': {'agent_supplied': True}},
            'before': {'sequence': 1, 'status_code': 200, 'complete': True,
                       'request': {'method': 'GET'}, 'response': {'actual': deepcopy(state)}},
            'actions': [{'sequence': 2, 'request': {'method': 'POST'},
                         'response': {'actual': deepcopy(state)}}],
            'after': {'sequence': 3, 'status_code': 200, 'complete': True,
                      'request': {'method': 'GET'}, 'response': {'actual': deepcopy(state)}},
            'invariant': deepcopy(INVARIANT),
            'violation': {'observed': observed, 'description': 'agent prose'},
        },
        '_rule_provenance_valid': True,
    }


class BusinessRequirementTests(unittest.TestCase):
    def test_registry_records_new_retrospective_northstar_requirement(self):
        rules = load_business_requirements()
        rule = next(item for item in rules if item['id'] == 'NSM-RETURN-CAP-2026-09-16')
        self.assertEqual(rule['asserted_on'], '2026-09-16')
        self.assertEqual(rule['effective_from'], '2026-09-16')
        self.assertEqual(rule['application_mode'], 'retrospective_evidence_evaluation')
        self.assertEqual(rule['allowed_run_ids'], ['northstar-refund-v4-reanalysis'])
        self.assertIn('does not claim that the requirement existed before that run',
                      rule['assertion_context'])

    def test_exact_origin_and_predicate_attach_controlled_requirement(self):
        record = finding()
        result = finding()
        result['finding_id'] = 'F-001'
        report = {'target_url': 'http://localhost:3000/orders',
                  'findings': [record], 'results': [result]}
        apply_backend_requirements(report, trusted_run_id='northstar-refund-v4-reanalysis')
        self.assertTrue(record['_backend_requirement_valid'])
        self.assertEqual(record['backend_requirement']['id'], 'NSM-RETURN-CAP-2026-09-16')
        self.assertTrue(result['_backend_requirement_valid'])
        self.assertEqual(result['backend_requirement']['id'], 'NSM-RETURN-CAP-2026-09-16')
        status, reason = classify(record)
        self.assertEqual(status, 'CONFIRMED')
        self.assertIn('asserted on 2026-09-16', reason)
        self.assertIn('after northstar-refund-v4-reanalysis executed', reason)
        self.assertIn('does not claim that the requirement existed before that run', reason)
        self.assertIn('applied retrospectively', reason)

    def test_different_predicate_does_not_match(self):
        record = finding()
        record['verification']['invariant']['terms'] = [['order', 'totalReturned']]
        apply_backend_requirements({'target_url': 'http://localhost:3000',
                                    'findings': [record], 'results': []},
                                   trusted_run_id='northstar-refund-v4-reanalysis')
        self.assertNotIn('backend_requirement', record)
        self.assertEqual(classify(record)[0], 'NEEDS_REVIEW')

    def test_agent_cannot_supply_backend_requirement_flag(self):
        record = finding()
        record['_backend_requirement_valid'] = True
        record['backend_requirement'] = {'id': 'FAKE'}
        apply_backend_requirements({'target_url': 'http://other.test',
                                    'run_id': 'northstar-refund-v4-reanalysis',
                                    'findings': [record], 'results': []},
                                   trusted_run_id='different-run')
        self.assertNotIn('_backend_requirement_valid', record)
        self.assertNotIn('backend_requirement', record)
        self.assertEqual(classify(record)[0], 'NEEDS_REVIEW')

    def test_other_run_with_same_origin_and_invariant_is_not_allowed(self):
        record = finding()
        apply_backend_requirements({'target_url': 'http://localhost:3000',
                                    'findings': [record], 'results': []},
                                   trusted_run_id='different-run')
        self.assertNotIn('backend_requirement', record)
        self.assertEqual(classify(record)[0], 'NEEDS_REVIEW')

    def test_missing_or_agent_spoofed_run_context_fails_closed(self):
        for report in (
                {'target_url': 'http://localhost:3000', 'findings': [finding()], 'results': []},
                {'target_url': 'http://localhost:3000',
                 'run_id': 'northstar-refund-v4-reanalysis',
                 'validated': True, 'findings': [finding()], 'results': []}):
            apply_backend_requirements(report)
            self.assertNotIn('backend_requirement', report['findings'][0])
            self.assertEqual(classify(report['findings'][0])[0], 'NEEDS_REVIEW')

    def test_trusted_run_id_requires_canonical_matching_report_path(self):
        canonical = ('runs/northstar-refund-v4-reanalysis/reports/'
                     'northstar-refund-v4-reanalysis/findings.json')
        substituted = ('runs/northstar-refund-v4-reanalysis/reports/'
                       'different-run/findings.json')
        self.assertEqual(trusted_report_run_id(canonical),
                         'northstar-refund-v4-reanalysis')
        self.assertIsNone(trusted_report_run_id(substituted))
        self.assertIsNone(trusted_report_run_id('findings.json'))


if __name__ == '__main__':
    unittest.main()
