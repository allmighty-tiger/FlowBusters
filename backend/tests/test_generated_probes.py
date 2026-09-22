"""Atomic plan compilation and exact candidate binding, without network calls."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from backend.runtime.generated_probes import binding_contract, compile_plans, render_plan, validate_plan
from backend.runtime.candidate_correction import _bind_candidate_scripts, _script_contract


def plan():
    value = {'schema_version': 1, 'title': 'Compensation bound', 'origin': 'http://localhost:3000',
            'setup': '/api/demo/reset', 'state_read': '/api/order', 'claim_type': 'amount_bound',
            'actions': [{'method': 'POST', 'path': '/api/order/price-adjustment', 'body': {}},
                        {'method': 'POST', 'path': '/api/order/refund/request', 'body': {}},
                        {'method': 'POST', 'path': '/api/order/refund/complete', 'body': {}}],
            'predicate': 'business_rule_must_hold',
            'invariant': {'operator': 'sum_lte', 'terms': [['order', 'totalReturned']],
                          'limit': ['order', 'originalAmount']}}
    value.update(rule_origin='AI_INFERRED', inferred_invariant={
        'statement': 'Combined compensation should not exceed the original payment',
        'predicate': deepcopy(value['invariant']),
        'required_actions': [{'method': a['method'], 'path': a['path']} for a in value['actions']],
        'terminal_state': [{'path': ['order', 'refund', 'status'], 'equals': 'completed'}]},
        supporting_observations=[{'source_run': 'flow', 'artifact': 'demo.json',
                                 'json_pointer': '/label', 'value': 'Original payment'}])
    return value


class GeneratedProbeTests(unittest.TestCase):
    def test_binding_requires_exact_order_body_predicate_not_endpoint_subset(self):
        original = plan()
        expected = binding_contract(original)
        for variant in ('order', 'body', 'predicate', 'superset'):
            changed = deepcopy(original)
            if variant == 'order': changed['actions'].reverse()
            if variant == 'body': changed['actions'][0]['body'] = {'adjustmentAmount': 130}
            if variant == 'predicate': changed['invariant']['terms'] = [['order', 'currentPrice']]
            if variant == 'superset': changed['actions'].append({'method': 'POST', 'path': '/api/order/cancel', 'body': {}})
            changed['inferred_invariant']['predicate'] = deepcopy(changed['invariant'])
            changed['inferred_invariant']['required_actions'] = [
                {'method': a['method'], 'path': a['path']} for a in changed['actions']]
            self.assertEqual(_bind_candidate_scripts({'execution_contract': expected},
                {'a.py': binding_contract(changed)})[0], [], variant)
        self.assertEqual(_bind_candidate_scripts({'execution_contract': expected}, {'renamed.py': expected}),
                         (['renamed.py'], None))
        self.assertEqual(_bind_candidate_scripts({'execution_contract': expected}, {'a.py': expected, 'b.py': expected})[0], [])

    def test_compile_preserves_draft_and_refuses_after_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory); folder = run / 'mutations' / 'flow'; folder.mkdir(parents=True)
            script = folder / 'test.py'; draft = '"""Original hypothesis."""\nPROBE_PLAN = ' + repr(plan())
            script.write_text(draft, encoding='utf-8')
            compile_plans(run, 'flow')
            self.assertEqual((run / 'generated_probe_drafts' / 'test.py').read_text(), draft)
            self.assertEqual(_script_contract(script), binding_contract(plan()))
            compiled = script.read_text(encoding='utf-8')
            script.write_text(compiled + '\nprint("changed chain")', encoding='utf-8')
            self.assertIsNone(_script_contract(script))
            script.write_text(compiled, encoding='utf-8')
            compile_plans(run, 'flow')
            receipts = run / 'reports' / 'flow' / 'executions'; receipts.mkdir(parents=True)
            (receipts / 'terminal.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'after execution'): compile_plans(run, 'flow')

    def test_missing_invariant_and_custom_code_fail_before_execution(self):
        invalid = plan(); invalid.pop('invariant')
        with self.assertRaisesRegex(ValueError, 'invariant'): validate_plan(invalid)
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory); folder = run / 'mutations' / 'flow'; folder.mkdir(parents=True)
            script = folder / 'test.py'
            script.write_text('PROBE_PLAN = ' + repr(plan()) + '\nprint("unattributed")')
            with self.assertRaisesRegex(ValueError, 'custom code'): compile_plans(run, 'flow')

    def test_unsupported_claim_cannot_borrow_monetary_predicate(self):
        invalid = plan(); invalid['claim_type'] = 'mass_assignment'
        with self.assertRaisesRegex(ValueError, 'amount_bound'): render_plan(invalid)
        invalid.update(predicate='unsupported_business_rule', invariant=None,
                       unsupported_reason='Client field persistence has no supported predicate')
        invalid.pop('inferred_invariant')
        self.assertIn('unsupported_reason', render_plan(invalid))

    def test_supported_plan_requires_inference_not_a_comment(self):
        for field in ('rule_origin', 'inferred_invariant', 'supporting_observations'):
            invalid = plan(); invalid.pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_plan(invalid)

    def test_required_terminal_action_cannot_be_omitted(self):
        invalid = plan(); invalid['actions'].pop()
        with self.assertRaisesRegex(ValueError, 'omits a required action'):
            validate_plan(invalid)
