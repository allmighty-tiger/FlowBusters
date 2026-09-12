import unittest
from unittest.mock import Mock
from backend.runtime.verification import classify, normalize_report, capture_deletion_check


class NotExecutedTests(unittest.TestCase):
    def gap(self):
        return {'execution': {'status': 'NOT_EXECUTED',
            'missing_precondition': 'No resource with different ownership available.',
            'next_step': 'Prepare ownership and replay with the other user.'}}

    def test_preserved_linked_and_counted_once(self):
        report = normalize_report({'findings': [{'id': 'F-006', 'related_findings': ['F-005']}],
            'results': [dict(self.gap(), finding_id='F-006')]})
        self.assertEqual(report['findings'][0]['verification_status'], 'NOT_EXECUTED')
        self.assertEqual(report['summary']['not_executed'], 1)
        self.assertEqual(report['summary']['confirmed'], 0)
        self.assertEqual(report['summary']['errors'], 0)
        self.assertIn('next_step', report['findings'][0]['execution'])
        self.assertEqual(normalize_report(report), report)

    def test_result_without_finding_is_visible_in_count(self):
        self.assertEqual(normalize_report({'findings': [], 'results': [self.gap()]})['summary']['not_executed'], 1)

    def test_errors_and_action_conflicts_not_hidden(self):
        self.assertEqual(classify(dict(self.gap(), error_message='Timeout'))[0], 'CHECK_ERROR')
        self.assertEqual(classify(dict(self.gap(), verification={'action': {'method': 'DELETE'}}))[0], 'NEEDS_REVIEW')
        self.assertEqual(classify({'execution': {'status': 'NOT_EXECUTED'}})[0], 'NEEDS_REVIEW')
        self.assertEqual(classify({'title': 'not exercisable'})[0], 'NEEDS_REVIEW')

    def test_missing_fixture_prevents_action(self):
        delete = Mock()
        read = Mock(return_value={'status_code': 200, 'complete': True, 'state': 'DRAFT',
            'item_ids': [2], 'resource_id': 'r', 'principal_id': 'p'})
        capture = capture_deletion_check(read, delete, rule={}, resource_id='r', principal_id='p', item_id=2)
        self.assertEqual(classify(capture)[0], 'NOT_EXECUTED')
        delete.assert_not_called()
        read.assert_called_once()

    def test_failed_setup_read_stays_error(self):
        capture = capture_deletion_check(Mock(side_effect=TimeoutError), Mock(),
            rule={}, resource_id='r', principal_id='p', item_id=2)
        self.assertEqual(classify(capture)[0], 'CHECK_ERROR')
