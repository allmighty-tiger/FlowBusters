import unittest
from copy import deepcopy
from backend.runtime.verification import classify, normalize_report, capture_deletion_check


def example(status=200, removed=True):
    before = dict(resource_id='r1', principal_id='u1', sequence=1, status_code=200,
        complete=True, state='APPROVED', item_ids=[1, 2], request={'method':'GET'}, response={'items':[1,2]})
    after = {**deepcopy(before), 'sequence':3, 'item_ids':[2] if removed else [1,2]}
    return {'verification': {'predicate':'approved_item_must_remain',
        'rule': {'source':'user', 'reference':'Approved items cannot be deleted'},
        'before':before, 'after':after,
        'action':dict(resource_id='r1', principal_id='u1', sequence=2, method='DELETE', item_id=1,
            request={'method':'DELETE'}, response={'status_code':status})}}


class VerificationTests(unittest.TestCase):
    def authorize(self, record):
        record['_backend_requirement_valid'] = True
        record['backend_requirement'] = {
            'id': 'TEST-RULE', 'product': 'Test application',
            'asserted_on': '2026-09-17', 'requirements_version': 'test-v1',
            'retrospective': False, 'assertion_context': 'Controlled test fixture.',
            'application_identity': {'application_id': 'test-app', 'identity_version': '1'},
        }
        return record

    def business_rule(self, observed=True):
        return {'verification': {
            'predicate': 'business_rule_must_hold',
            'rule': {'source': 'specification', 'reference': 'Returns must not exceed the original payment'},
            'before': {'sequence': 1, 'status_code': 200, 'complete': True,
                       'request': {'method': 'GET'}, 'response': {'refund': 'pending'}},
            'actions': [
                {'sequence': 2, 'request': {'method': 'POST', 'url': '/cancel'},
                 'response': {'status_code': 200}},
                {'sequence': 3, 'request': {'method': 'POST', 'url': '/refund/complete'},
                 'response': {'status_code': 200}},
            ],
            'after': {'sequence': 4, 'status_code': 200, 'complete': True,
                      'request': {'method': 'GET'}, 'response': {'totalReturned': 120}},
            'violation': {'observed': observed,
                          'description': 'Cancellation reimbursement and refund were both applied.' if observed
                                         else 'The conflicting second action was rejected and total returned stayed bounded.'},
        }}

    def test_capture_reads_after_500(self):
        v=example(500)['verification']; snapshots=iter([v['before'],v['after']])
        r=capture_deletion_check(lambda: next(snapshots), lambda: v['action'],
            rule=v['rule'],resource_id='r1',principal_id='u1',item_id=1)
        self.assertEqual(classify(r)[0], 'CONFIRMED')
    def test_capture_reads_after_exception(self):
        v=example()['verification']; calls=[]
        def read():
            calls.append(1); return v['before'] if len(calls)==1 else v['after']
        def fail(): raise TimeoutError()
        r=capture_deletion_check(read,fail,rule=v['rule'],resource_id='r1',principal_id='u1',item_id=1)
        self.assertEqual(len(calls),2)
        self.assertEqual(classify(r)[0], 'CHECK_ERROR')
    def test_200_without_change(self):
        self.assertEqual(classify(example(200, False))[0], 'NOT_REPRODUCED')
    def test_500_with_change(self):
        self.assertEqual(classify(example(500))[0], 'CONFIRMED')
    def test_failed_state_read(self):
        r=example(); r['verification']['after']['status_code']=404
        self.assertEqual(classify(r)[0], 'CHECK_ERROR')
    def test_missing_and_inferred_evidence(self):
        self.assertEqual(classify({'outcome':'BUG_FOUND','status_code':200})[0], 'NEEDS_REVIEW')
        r=example();r['verification']['rule']['source']='agent'
        self.assertEqual(classify(r)[0], 'NEEDS_REVIEW')
    def test_wrong_identity_and_order(self):
        for key,value in [('principal_id','other'),('sequence',0)]:
            r=example();r['verification']['after'][key]=value
            self.assertEqual(classify(r)[0], 'NEEDS_REVIEW')
    def test_linkage_and_legacy(self):
        r={'findings':[{'id':'F-1'}], 'results':[{**example(), 'finding_id':'F-1'}]}
        n=normalize_report(r)
        self.assertEqual(n['findings'][0]['verification_status'],'CONFIRMED')
        self.assertNotIn('verification',r['findings'][0])
        old=normalize_report({'results':[{'outcome':'BUG_FOUND','script':'old'}]})
        self.assertEqual(old['summary']['confirmed'],0)
        self.assertEqual(old['findings'][0]['verification_status'],'NEEDS_REVIEW')

    def test_business_rule_with_complete_state_evidence(self):
        positive_status, positive_reason = classify(self.authorize(self.business_rule()))
        negative_status, negative_reason = classify(self.business_rule(False))
        self.assertEqual(positive_status, 'CONFIRMED')
        self.assertEqual(negative_status, 'NOT_REPRODUCED')
        self.assertIn('exact executable invariant', positive_reason)
        self.assertEqual(negative_reason,
                         'The captured final state did not violate the backend-evaluated invariant.')

    def test_agent_description_cannot_add_race_claim_to_normalized_verdict(self):
        record = self.authorize(self.business_rule())
        record['verification']['violation']['description'] = (
            'Critical race condition was exploited concurrently.'
        )
        status, reason = classify(record)
        self.assertEqual(status, 'CONFIRMED')
        self.assertNotIn('race', reason.lower())
        self.assertNotIn('concurrent', reason.lower())

    def test_business_rule_requires_complete_evidence(self):
        record = self.business_rule()
        del record['verification']['after']['response']
        self.assertEqual(classify(record)[0], 'NEEDS_REVIEW')

    def test_negative_business_invariant_precedes_missing_rule_reference(self):
        record = self.business_rule(False)
        record['verification']['rule'] = {'source': 'agent', 'reference': ''}
        self.assertEqual(classify(record)[0], 'NOT_REPRODUCED')

    def test_positive_business_invariant_still_requires_rule_reference(self):
        record = self.business_rule(True)
        record['verification']['rule'] = {'source': 'agent', 'reference': ''}
        status, reason = classify(record)
        self.assertEqual(status, 'NEEDS_REVIEW')
        self.assertIn('user, specification, or validated observed-UI', reason)

    def test_positive_business_invariant_rejects_free_form_observed_ui(self):
        record = self.business_rule(True)
        record['verification']['rule'] = {
            'source': 'observed_ui',
            'reference': 'price-adjustment/state_map.json observed_ui_rules[2]',
        }
        status, reason = classify(record)
        self.assertEqual(status, 'NEEDS_REVIEW')
        self.assertIn('did not validate', reason)

    def test_validated_observed_ui_facts_do_not_bind_an_arithmetic_rule(self):
        record = self.business_rule(True)
        record['verification']['rule'] = {'source': 'observed_ui', 'provenance': {}}
        record['_rule_provenance_valid'] = True
        status, reason = classify(record)
        self.assertEqual(status, 'NEEDS_REVIEW')
        self.assertIn('does not bind those facts to this arithmetic invariant', reason)
        self.assertIn('UI action availability is not evidence', reason)

    def test_price_conservation_invariant_does_not_decide_client_input_trust(self):
        record = self.business_rule(False)
        record.update({
            'title': 'Price adjustment endpoint may trust a client-supplied adjustment amount',
            'mutation_type': 'PRICING_TAMPER',
        })
        state = {'order': {'originalAmount': 100, 'totalReturned': 30,
                           'priceProtection': {'adjustmentAmount': 30, 'currentPrice': 70}}}
        record['verification'].update({
            'rule': {'source': 'agent_inference'},
            'actions': [{
                'sequence': 2,
                'request': {'method': 'POST', 'url': '/api/order/price-adjustment',
                            'body': {'adjustmentAmount': 999999}},
                'response': {'actual': deepcopy(state)},
            }],
            'after': {'sequence': 3, 'status_code': 200, 'complete': True,
                      'request': {'method': 'GET', 'url': '/api/order'},
                      'response': {'actual': deepcopy(state)}},
            'invariant': {
                'operator': 'sum_lte',
                'terms': [['order', 'priceProtection', 'adjustmentAmount'],
                          ['order', 'priceProtection', 'currentPrice']],
                'limit': ['order', 'originalAmount'],
            },
        })
        status, reason = classify(record)
        self.assertEqual(status, 'NEEDS_REVIEW')
        self.assertIn('adjustmentAmount=999999', reason)
        self.assertIn('action response recorded adjustmentAmount=30', reason)
        self.assertIn('final state recorded adjustmentAmount=30', reason)
        self.assertIn('cannot determine whether the server trusted', reason)

    def test_incomplete_negative_evidence_does_not_bypass_validation(self):
        record = self.business_rule(False)
        record['verification']['rule'] = {'source': 'agent', 'reference': ''}
        del record['verification']['after']['response']
        self.assertEqual(classify(record)[0], 'NEEDS_REVIEW')

    def test_mismatched_provenance_precedes_negative_invariant(self):
        record = self.business_rule(False)
        record['_provenance_error'] = 'Signed HTTP trace does not match the reported evidence.'
        status, reason = classify(record)
        self.assertEqual(status, 'NEEDS_REVIEW')
        self.assertIn('does not match', reason)

    def test_rejected_raw_attempt_is_not_reproduced(self):
        self.assertEqual(classify({'outcome': 'REJECTED'})[0], 'NOT_REPRODUCED')
        self.assertEqual(classify({'outcome': 'NEEDS_REVIEW',
                                   'original_outcome': 'REJECTED'})[0], 'NOT_REPRODUCED')

    def test_secondary_auth_cwe_does_not_override_business_verification(self):
        record = self.authorize(self.business_rule())
        record['cwe'] = ['CWE-841', 'CWE-306']
        self.assertEqual(classify(record)[0], 'CONFIRMED')

    def test_summary_recomputes_critical_counts(self):
        confirmed = self.authorize(self.business_rule())
        confirmed.update({'id': 'F-1', 'severity': 'Critical'})
        review = {'id': 'F-2', 'severity': 'Critical'}
        report = normalize_report({'findings': [confirmed, review], 'results': [],
                                   'summary': {'critical_findings': 99}})
        self.assertEqual(report['summary']['critical_findings'], 1)
        self.assertEqual(report['summary']['potential_critical_findings'], 1)

    def test_confirmed_result_inherits_verified_linked_finding(self):
        finding = self.authorize(self.business_rule())
        finding.update({'id': 'F-1', 'severity': 'Critical'})
        report = normalize_report({'findings': [finding], 'results': [{
            'script': 'interleave.py', 'finding_id': 'F-1',
            'outcome': 'CONFIRMED',
        }]})
        self.assertEqual(report['findings'][0]['verification_status'], 'CONFIRMED')
        self.assertEqual(report['results'][0]['outcome'], 'CONFIRMED')
        self.assertEqual(report['summary']['bugs_found'], 1)

    def test_non_dict_verification_does_not_crash_render(self):
        # Regression: the crew can emit verification as a bare string (e.g.
        # "CONFIRMED"). normalize_report must not throw; the malformed finding
        # degrades to NEEDS_REVIEW and a well-formed probe finding stays intact.
        r = {"findings": [
            {"id": "F-001", "source": "MUTATION_SCRIPT", "verification": "CONFIRMED"},
            {"id": "F-003", "source": "STATE_LOCK_PROBE", "verification": example()['verification']},
        ], "results": []}
        n = normalize_report(r)
        by_id = {f['id']: f for f in n['findings']}
        self.assertEqual(by_id['F-001']['verification_status'], 'NEEDS_REVIEW')
        self.assertEqual(by_id['F-003']['verification_status'], 'CONFIRMED')

    def test_only_equivalent_action_chains_and_invariants_collapse(self):
        verification = self.business_rule()['verification']
        verification['invariant'] = {
            'operator': 'sum_lte', 'terms': [['refund'], ['cancellation']],
            'limit': ['originalAmount'],
        }
        duplicate = deepcopy(verification)
        different_chain = deepcopy(verification)
        different_chain['actions'][0]['request']['body'] = {'amount': 9999}
        findings = [
            {'id': 'F-001', 'source': 'MUTATION_SCRIPT', 'cwe': ['CWE-841'],
             'url_tested': 'http://h/api/order', 'verification': verification},
            {'id': 'F-002', 'source': 'MUTATION_SCRIPT', 'cwe': ['CWE-841'],
             'url_tested': 'http://h/api/order', 'verification': duplicate},
            {'id': 'F-003', 'source': 'MUTATION_SCRIPT', 'cwe': ['CWE-841'],
             'url_tested': 'http://h/api/order', 'verification': different_chain},
        ]
        normalized = normalize_report({'findings': findings, 'results': []})
        self.assertEqual(len(normalized['findings']), 2)
        kept = next(f for f in normalized['findings'] if f.get('deduplicated_from'))
        self.assertEqual(kept['deduplicated_from'], ['F-002'])
        self.assertIn('F-003', [f['id'] for f in normalized['findings']])
        self.assertEqual(normalized['summary']['deduplicated_primary_chains'], 1)

    def test_authenticated_state_changing_chain_deduplicates_across_draft_labels(self):
        invariant = {
            'operator': 'sum_lte',
            'terms': [['order', 'cancellation', 'reimbursementAmount'],
                      ['order', 'refund', 'completedAmount']],
            'limit': ['order', 'originalAmount'],
        }

        def capture(method, path, body=None):
            return {'request': {'method': method, 'url': f'http://h{path}', 'body': body}}

        def finding(identifier, observed, trace, action_mode, cwe, severity):
            record = self.business_rule(observed)
            record.update({'id': identifier, 'source': 'MUTATION_SCRIPT',
                           'script': f'{identifier}.py', 'url_tested': 'http://h/api/order',
                           'cwe': cwe, 'severity': severity, 'execution_trace': trace})
            if action_mode == 'last_only':
                record['verification']['actions'] = [record['verification']['actions'][-1]]
            record['verification']['invariant'] = deepcopy(invariant)
            return record

        reset = capture('POST', '/api/demo/reset')
        request = capture('POST', '/api/order/refund/request')
        cancel = capture('POST', '/api/order/cancel')
        complete = capture('POST', '/api/order/refund/complete')
        read = capture('GET', '/api/order')
        positive_trace = [reset, request, read, cancel, complete, read]
        reverse_trace_a = [reset, request, read, complete, cancel, read]
        reverse_trace_b = [reset, request, complete, read, cancel, read]
        records = [
            finding('F-001', True, positive_trace, 'all', ['CWE-841'], 'High'),
            finding('F-003', True, positive_trace, 'all', ['CWE-362', 'CWE-841'], 'Critical'),
            finding('F-002', False, reverse_trace_a, 'all', ['CWE-841'], 'High'),
            finding('F-008', False, reverse_trace_b, 'last_only', ['CWE-841'], 'High'),
        ]
        for record in records:
            if record['verification']['violation']['observed']:
                self.authorize(record)
        results = [{
            'script': record['script'], 'finding_id': record['id'],
            'outcome': 'CONFIRMED' if record['verification']['violation']['observed'] else 'NOT_REPRODUCED',
            'verification': deepcopy(record['verification']),
        } for record in records]
        normalized = normalize_report({
            'findings': records, 'results': results,
            '_setup_paths': ['/api/demo/reset'],
        })
        self.assertEqual([record['id'] for record in normalized['findings']], ['F-001', 'F-002'])
        self.assertEqual(normalized['findings'][0]['deduplicated_from'], ['F-003'])
        self.assertEqual(normalized['findings'][1]['deduplicated_from'], ['F-008'])
        self.assertEqual(normalized['summary']['deduplicated_primary_chains'], 2)
        self.assertEqual(normalized['summary']['deduplicated_execution_results'], 2)
        by_finding = {result['finding_id']: result for result in normalized['results']}
        self.assertEqual(by_finding['F-003']['deduplicated_into'], 'F-001')
        self.assertEqual(by_finding['F-008']['deduplicated_into'], 'F-002')
        self.assertNotIn('race-condition', by_finding['F-003']['verification_reason'].lower())
        self.assertIn('does not independently establish concurrent execution',
                      by_finding['F-003']['verification_reason'])

    def test_different_authenticated_state_changing_chains_do_not_deduplicate(self):
        invariant = {'operator': 'sum_lte', 'terms': [['order', 'totalReturned']],
                     'limit': ['order', 'originalAmount']}
        first = self.business_rule(False)
        second = self.business_rule(False)
        for identifier, record, path in (
                ('F-004', first, '/api/order/refund/complete'),
                ('F-005', second, '/api/order/refund/request')):
            record.update({'id': identifier, 'source': 'MUTATION_SCRIPT', 'cwe': ['CWE-841'],
                           'url_tested': 'http://h/api/order', 'execution_trace': [
                               {'request': {'method': 'POST', 'url': f'http://h{path}', 'body': None}},
                           ]})
            record['verification']['invariant'] = deepcopy(invariant)
        normalized = normalize_report({'findings': [first, second], 'results': []})
        self.assertEqual([record['id'] for record in normalized['findings']], ['F-004', 'F-005'])
        self.assertEqual(normalized['summary']['deduplicated_primary_chains'], 0)

    def test_deterministic_finding_not_clobbered_by_llm_evidence(self):
        # Regression: a STATE_LOCK_PROBE finding whose ID matched an LLM crew
        # evidence artifact must keep its confirmed evidence; the artifact's
        # NOT_EXECUTED stub must not overwrite it.
        import json
        import tempfile
        from pathlib import Path
        from backend.runtime.verification import load_report
        report = {"findings": [
            {"id": "F-005", "source": "STATE_LOCK_PROBE", "severity": "High",
             "title": "probe", "evidence": {"summary": "probe evidence"},
             "verification": example()['verification']},
        ], "results": []}
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'evidence').mkdir()
            (base / 'evidence' / 'F-005.json').write_text(json.dumps(
                {"id": "F-005", "source": "VERIFIED_DELETE",
                 "verification": {"predicate": "approved_item_must_remain",
                                    "rule": {"source": "agent", "reference": "x"},
                                    "execution": {"status": "NOT_EXECUTED",
                                                   "missing_precondition": "isolated resource"}}}))
            (base / 'findings.json').write_text(json.dumps(report))
            out = load_report(base / 'findings.json')
        probe = [f for f in out['findings'] if f.get('source') == 'STATE_LOCK_PROBE']
        self.assertTrue(probe, 'probe finding was lost by the evidence join')
        # Raw state evidence remains intact, but an unsigned legacy capture
        # cannot establish who executed it under the provenance contract.
        self.assertEqual(probe[0]['verification_status'], 'NEEDS_REVIEW')
        self.assertNotIn('execution', probe[0], 'crew NOT_EXECUTED stub must not overwrite probe')
        self.assertEqual(probe[0]['verification']['after']['item_ids'], [2])

    def test_load_report_exposes_configured_setup_path_findings_separately(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from backend.runtime.verification import load_report
        report = {'findings': [
            {'id': 'F-SETUP', 'title': 'Reset loop', 'url_tested':
             '/api/demo/reset -> /api/refund', 'evidence': 'x'},
            {'id': 'F-REAL', 'title': 'Cancel then refund', 'url_tested':
             '/api/order/cancel -> /api/refund', 'evidence': 'x'},
        ], 'results': []}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / 'reports' / 'flow'
            report_dir.mkdir(parents=True)
            (root / 'scope.json').write_text(json.dumps({'setup_paths': ['/api/demo/reset']}), encoding='utf-8')
            path = report_dir / 'findings.json'
            path.write_text(json.dumps(report), encoding='utf-8')
            with patch.dict('os.environ', {'SETUP_PATHS': ''}):
                out = load_report(path)
        self.assertEqual([finding['id'] for finding in out['findings']], ['F-REAL'])
        self.assertEqual([item['id'] for item in out['excluded_setup_executions']],
                         ['F-SETUP'])

if __name__ == '__main__': unittest.main()
