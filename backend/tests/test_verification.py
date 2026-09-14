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

    def test_same_violation_from_multiple_sources_collapses(self):
        # Regression: the state-lock bug reported by the crew's mutation script,
        # its verified-delete stub, and the deterministic probe must collapse to
        # the single strongest (the CONFIRMED probe finding); a different CWE
        # (the auth bypass) must survive as its own finding.
        conf = example()['verification']
        for snap in (conf['before'], conf['action'], conf['after']):
            snap['resource_id'] = '/api/dashboard/1'
        findings = [
            {'id': 'F-001', 'source': 'MUTATION_SCRIPT', 'cwe': ['CWE-841'],
             'url_tested': 'http://h/api/dashboard/1/parts/3', 'verification': 'CONFIRMED'},
            {'id': 'F-003', 'source': 'STATE_LOCK_PROBE', 'cwe': ['CWE-841', 'CWE-670'],
             'url_tested': 'http://h/api/dashboard/1/parts/1', 'verification': conf},
            {'id': 'F-DEL', 'source': 'VERIFIED_DELETE', 'cwe': ['CWE-841'],
             'url_tested': 'http://h/api/dashboard/1/parts/3',
             'verification': {'predicate': 'approved_item_must_remain', 'rule': {'source': 'agent', 'reference': 'x'}}},
            {'id': 'F-002', 'source': 'MUTATION_SCRIPT', 'cwe': ['CWE-287', 'CWE-300'],
             'url_tested': 'http://h/api/login', 'verification': 'CONFIRMED'},
        ]
        n = normalize_report({'findings': findings, 'results': []})
        ids = sorted(f['id'] for f in n['findings'])
        self.assertEqual(ids, ['F-002', 'F-003'], 'three state-lock reports + one auth bug -> two distinct findings')
        kept = {f['id']: f for f in n['findings']}
        self.assertEqual(kept['F-003']['verification_status'], 'CONFIRMED')
        self.assertEqual(sorted(kept['F-003'].get('deduplicated_from', [])), ['F-001', 'F-DEL'])
        self.assertEqual(n['summary']['reported_findings'], 2)

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
        self.assertEqual(probe[0]['verification_status'], 'CONFIRMED')
        self.assertNotIn('execution', probe[0], 'crew NOT_EXECUTED stub must not overwrite probe')
        self.assertEqual(probe[0]['verification']['after']['item_ids'], [2])

if __name__ == '__main__': unittest.main()
