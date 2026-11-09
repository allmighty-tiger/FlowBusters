"""Regression tests for auth / broken-access-control verification in classify().

The verifier must CONFIRM a credential/BAC finding when EITHER:
  1. the crew self-verified (verdict CONFIRMED) AND documented an independent
     state re-read showing a real effect, or
  2. the captured request/response trail shows a login that issued a usable
     credential.
It must stay conservative: a bare token/200 with no re-read or no captured
trail -> NEEDS_REVIEW; errors still take precedence -> CHECK_ERROR.
Detection is robust (CWE / mutation_type / source / url), NOT exact-source.
"""
import json
import unittest
from backend.runtime.verification import classify, _is_auth_finding


def crew_finding(cwes, verdict, independent_state_read, url='/api/login',
                 mutation_type=None, source='auth_check + script re-execution'):
    """A finding in the crew's free-text evidence shape (claim/action_*/...)."""
    evidence = {'claim': 'some flaw'}
    if independent_state_read is not None:
        evidence['independent_state_read'] = independent_state_read
    return {
        'id': 'F-001', 'source': source, 'verdict': verdict,
        'cwe': cwes, 'mutation_type': mutation_type, 'url_tested': url,
        'evidence': evidence,
    }


def trail_finding(login_ok=True, login_status=200, impacted=True, source='AUTH_CHECK'):
    """A finding in the structured {requests, responses} shape."""
    responses = [
        {'label': 'known_user_wrong_password', 'status_code': login_status,
         'response_body': json.dumps({'ok': login_ok, 'token': 'abc', 'user': 'x'}) if login_ok
         else json.dumps({'ok': False})},
    ]
    requests = [
        {'label': 'known_user_wrong_password', 'method': 'POST',
         'url': 'http://h/api/login', 'body': {'username': 'x', 'password': 'WRONG'}},
    ]
    if impacted:
        requests.append({'label': 'token_reads_dashboard', 'method': 'GET', 'url': 'http://h/api/dashboard/1'})
        responses.append({'label': 'token_reads_dashboard', 'status_code': 200,
                          'response_body': json.dumps({'ok': True})})
    return {'id': 'F-001', 'source': source, 'cwe': ['CWE-287'], 'url_tested': 'http://h/api/login',
            'evidence': {'summary': 'x', 'requests': requests, 'responses': responses}}


class AuthVerificationTests(unittest.TestCase):
    # --- robust detection ---------------------------------------------------
    def test_detects_by_cwe_despite_freetext_source(self):
        self.assertTrue(_is_auth_finding(crew_finding(['CWE-287'], 'CONFIRMED', 're-read ok')))
        self.assertTrue(_is_auth_finding(crew_finding(['CWE-862', 'CWE-639'], 'CONFIRMED', 're-read ok')))

    def test_detects_idor_by_mutation_type(self):
        rec = {'cwe': [], 'mutation_type': 'FORCED_BROWSING / ROLE_SWAP (broken access control)',
               'source': 'script re-execution + direct state-read probing', 'url_tested': '/api/dashboard/1'}
        self.assertTrue(_is_auth_finding(rec))

    def test_non_auth_not_detected(self):
        rec = {'cwe': ['CWE-841'], 'mutation_type': 'REPLAY_ATTACK', 'url_tested': '/api/dashboard/1/parts/1'}
        self.assertFalse(_is_auth_finding(rec))

    # --- path 1: crew verdict + independent re-read -------------------------
    def test_crew_confirmed_with_reread_is_confirmed(self):
        status, reason = classify(crew_finding(['CWE-287'], 'CONFIRMED',
                                               'GET /api/dashboard/1 re-read returned real owner data'))
        self.assertEqual(status, 'CONFIRMED')
        self.assertIn('independent re-read', reason)

    def test_idor_crew_confirmed_with_reread_is_confirmed(self):
        # The "remove parts from someone else's board" case (BAC/CWE-639).
        status, _ = classify(crew_finding(['CWE-862', 'CWE-639'], 'CONFIRMED',
                                          'parts [1,2,3] -> [2,3]; state DRAFT -> APPROVED',
                                          url='/api/dashboard/1',
                                          mutation_type='FORCED_BROWSING / ROLE_SWAP'))
        self.assertEqual(status, 'CONFIRMED')

    def test_crew_verdict_alone_without_reread_needs_review(self):
        # Safety bar: CONFIRMED verdict with no independent re-read must NOT confirm.
        self.assertEqual(
            classify(crew_finding(['CWE-287'], 'CONFIRMED', None))[0], 'NEEDS_REVIEW')

    def test_crew_not_confirmed_with_reread_needs_review(self):
        # Re-read present but the crew didn't assert CONFIRMED.
        self.assertEqual(
            classify(crew_finding(['CWE-287'], 'NEEDS_REVIEW', 're-read ok'))[0], 'NEEDS_REVIEW')

    # --- path 2: captured login trail ---------------------------------------
    def test_trail_login_issuing_token_is_confirmed(self):
        self.assertEqual(classify(trail_finding(impacted=True))[0], 'CONFIRMED')

    def test_trail_token_only_no_impact_is_confirmed(self):
        # A login that issues a usable credential is a bypass even with no
        # captured protected call.
        self.assertEqual(classify(trail_finding(impacted=False))[0], 'CONFIRMED')

    def test_trail_login_rejected_is_needs_review(self):
        self.assertEqual(
            classify(trail_finding(login_ok=False, login_status=401, impacted=False))[0], 'NEEDS_REVIEW')

    def test_trail_no_requests_is_needs_review(self):
        self.assertEqual(
            classify({'id': 'F', 'source': 'AUTH_CHECK', 'cwe': ['CWE-287'],
                      'url_tested': 'http://h/api/login', 'evidence': {}})[0], 'NEEDS_REVIEW')

    # --- error precedence preserved -----------------------------------------
    def test_error_still_check_error(self):
        rec = crew_finding(['CWE-287'], 'CONFIRMED', 're-read ok')
        rec['error_message'] = 'probe failed'
        self.assertEqual(classify(rec)[0], 'CHECK_ERROR')


def linked(idor_result):
    """A finding with no own proof, but a linked result carrying the proof."""
    return ({'id': 'F-003', 'source': 'MUTATION_SCRIPT', 'verdict': 'CONFIRMED',
             'cwe': ['CWE-639'], 'url_tested': '/api/orders/3',
             'evidence': {'summary': 'intruder deleted another user order'}},
            idor_result)


class LinkedResultSignalTests(unittest.TestCase):
    def test_proof_in_linked_result_confirms(self):
        rec, res = linked({'script': '06_idor.py', 'finding_id': 'F-003',
                           'response_snippet': '{"other_user_read_leak":true,"object_gone":true}'})
        from backend.runtime.verification import classify
        status, reason = classify(rec, [res])
        self.assertEqual(status, 'CONFIRMED')
        self.assertIn('demonstrated by', reason)

    def test_bare_200_in_linked_result_stays_needs_review(self):
        rec, res = linked({'script': '06_idor.py', 'finding_id': 'F-003',
                           'response_snippet': '{"ok": true}'})
        from backend.runtime.verification import classify
        self.assertEqual(classify(rec, [res])[0], 'NEEDS_REVIEW')

    def test_verdict_confirmed_but_no_signal_anywhere_needs_review(self):
        rec = {'id': 'F-004', 'source': 'MUTATION_SCRIPT', 'verdict': 'CONFIRMED',
               'cwe': ['CWE-639'], 'url_tested': '/api/dashboard/1',
               'evidence': {'summary': 'any session can approve'}}
        from backend.runtime.verification import classify
        self.assertEqual(classify(rec, [])[0], 'NEEDS_REVIEW')

    def test_before_after_delta_in_finding_confirms(self):
        rec = {'id': 'F-005', 'source': 'MUTATION_SCRIPT', 'verdict': 'CONFIRMED',
               'cwe': ['CWE-639'], 'url_tested': '/api/orders/3',
               'evidence': {'before': {'state': 'DRAFT'}, 'after': {'state': 'APPROVED'}}}
        from backend.runtime.verification import classify
        self.assertEqual(classify(rec, [])[0], 'CONFIRMED')

if __name__ == '__main__':
    unittest.main()
