from copy import deepcopy
import unittest
from backend.runtime.probe_executor import _is_stale_unexecuted_draft
from backend.runtime.verification import _claim_coverage_error


class PendingDraftTests(unittest.TestCase):
    def setUp(self):
        self.executed = {'predicate': 'business_rule_must_hold',
                         'invariant': {'operator': 'sum_lte', 'terms': [['amount']], 'limit': ['limit']}}
        self.draft = dict(deepcopy(self.executed), violation={'observed': None, 'description': 'Pending'})

    def test_null_pending_result_can_be_replaced(self):
        self.assertTrue(_is_stale_unexecuted_draft(self.draft, self.executed))

    def test_claimed_boolean_or_capture_cannot_be_replaced(self):
        for value in (True, False):
            draft = deepcopy(self.draft)
            draft['violation']['observed'] = value
            self.assertFalse(_is_stale_unexecuted_draft(draft, self.executed))
        for field in ('before', 'after', 'actions', 'setup'):
            self.assertFalse(_is_stale_unexecuted_draft(dict(self.draft, **{field: {}}), self.executed))

    def test_different_invariant_cannot_be_replaced(self):
        self.draft['invariant']['limit'] = ['different']
        self.assertFalse(_is_stale_unexecuted_draft(self.draft, self.executed))

    def test_amount_limit_does_not_settle_duplicate_effect_claim(self):
        reason = _claim_coverage_error({'title': 'Duplicate request is not idempotent'},
            dict(self.executed, actions=[{'status_code': 201}, {'status_code': 409}]))
        self.assertIn('201, 409', reason)
        self.assertIn('duplicate records or effects', reason)
