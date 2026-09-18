import unittest
from copy import deepcopy

from backend.runtime.business_requirements import apply_backend_requirements
from backend.runtime.verification import classify, normalize_report
from backend.tests.test_business_requirements import NORTHSTAR, finding

class CrossFlowRequirementTests(unittest.TestCase):
    def test_same_rule_is_applied_to_every_matching_finding_and_result(self):
        first, second = finding(), finding(); second['title'] = 'Different action chain, same rule'
        result_a, result_b = deepcopy(first), deepcopy(second)
        report = {'run_timestamp': '2026-09-18T00:00:00Z', 'findings': [first, second], 'results': [result_a, result_b]}
        apply_backend_requirements(report, trusted_run_id='cross-new', trusted_identity=NORTHSTAR)
        self.assertEqual([classify(item)[0] for item in report['findings']], ['CONFIRMED', 'CONFIRMED'])
        self.assertEqual([classify(item)[0] for item in report['results']], ['CONFIRMED', 'CONFIRMED'])

    def test_mass_assignment_amount_bound_does_not_prove_input_control(self):
        record = finding(False); record['mutation_type'] = 'MASS_ASSIGNMENT'
        record['verification']['actions'][0]['request']['body'] = {'requestedAmount': 999999, 'status': 'completed'}
        status, reason = classify(record)
        self.assertEqual(status, 'NEEDS_REVIEW'); self.assertIn('not proof', reason)

    def test_negative_misleading_title_is_replaced_but_draft_is_preserved(self):
        record = finding(False)
        record.update({'id': 'F-001', 'title': 'Cancel action accepted', 'source': 'MUTATION_SCRIPT',
                       'cwe': ['CWE-841'], 'script': 'probe.py', 'mutation_type': 'STATE_INTERLEAVING'})
        record['verification']['actions'][0].update(status_code=409)
        item = normalize_report({'findings': [record], 'results': []})['findings'][0]
        self.assertEqual(item['verification_status'], 'NOT_REPRODUCED')
        self.assertNotIn('accepted', item['title'].lower())
        self.assertEqual(item['original_hypothesis'], 'Cancel action accepted')
        self.assertEqual(item['severity'], 'Not applicable')

if __name__ == '__main__':
    unittest.main()
