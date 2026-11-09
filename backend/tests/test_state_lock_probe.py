import json
import unittest
from unittest.mock import patch

from backend.runtime.state_lock_probe import run_probe, find_child_mutation_target
from backend.runtime.verification import classify


def state(parts):
    return json.dumps({"status": "APPROVED", "parts": [{"id": i} for i in parts]})


TARGET = {
    "base": "http://flowshop.test:18923",
    "parent_path": "/api/dashboard/1",
    "children_key": "parts",
    "child_delete_paths": ["/api/dashboard/1/parts"],
    "lifecycle_field": "status",
    "lifecycle_posts": [],
    "locked_states": ["APPROVED"],
    "cookie": "session=test",
    "login": None,
}


class StateLockProbeTests(unittest.TestCase):
    def test_confirmed_even_when_rule_is_inferred(self):
        # The probe is deterministic: it re-derives the rule from the app's own
        # captured behavior, so complete evidence CONFIRMs even though the rule
        # is self-referenced (source "agent") rather than user/specification.
        replies = iter([
            (200, state([7, 8])),  # baseline
            (200, state([7, 8])),  # ensure_locked state read
            (200, state([7, 8])),  # before action
            (500, "server error"), # action (server error does not block confirmation)
            (200, state([8])),     # after action — child 7 removed
        ])
        with patch("backend.runtime.state_lock_probe._do_request", side_effect=lambda *a, **k: next(replies)):
            finding = run_probe(dict(TARGET))
        self.assertIsNotNone(finding)
        self.assertEqual(finding["source"], "STATE_LOCK_PROBE")
        self.assertEqual(classify(finding)[0], "CONFIRMED")
        self.assertEqual(finding["verification"]["action"]["response"]["status_code"], 500)

    def test_agent_rule_from_llm_crew_stays_needs_review(self):
        # The same evidence emitted by the LLM crew (no STATE_LOCK_PROBE source)
        # must NOT confirm on a self-inferred rule — the crew must cite a rule.
        replies = iter([
            (200, state([7, 8])),
            (200, state([7, 8])),
            (200, state([7, 8])),
            (200, "server error"),
            (200, state([8])),
        ])
        with patch("backend.runtime.state_lock_probe._do_request", side_effect=lambda *a, **k: next(replies)):
            finding = run_probe(dict(TARGET))
        finding = dict(finding)
        finding["source"] = "MUTATION_SCRIPT"  # pretend the LLM produced it
        self.assertEqual(classify(finding)[0], "NEEDS_REVIEW")

    def test_200_without_observed_deletion_is_not_a_finding(self):
        replies = iter([
            (200, state([7, 8])),
            (200, state([7, 8])),
            (200, state([7, 8])),
            (200, '{"ok":true}'),
            (200, state([7, 8])),
        ])
        with patch("backend.runtime.state_lock_probe._do_request", side_effect=lambda *a, **k: next(replies)):
            finding = run_probe(dict(TARGET))
        self.assertIsNone(finding)

    def test_target_found_without_cookie_when_login_present(self):
        # Browser-recorded HARs carry no outgoing Cookie header; the probe must
        # fall back to the recorded login instead of bailing on a missing cookie.
        import tempfile
        from pathlib import Path
        har = {"log": {"entries": [
            {"request": {"method": "POST", "url": "http://flowshop.test:18923/api/login",
                          "headers": [{"name": "content-type", "value": "application/x-www-form-urlencoded"}],
                          "postData": {"text": "username=x&password=y"}},
             "response": {"status": 200, "content": {"text": ""}}},
            {"request": {"method": "GET", "url": "http://flowshop.test:18923/api/dashboard/1",
                          "headers": [{"name": "user-agent", "value": "x"}]},
             "response": {"status": 200, "content": {"text": json.dumps(
                 {"ok": True, "dashboard": {"id": 1, "state": "APPROVED"}, "parts": [{"id": 1}, {"id": 2}]})}}},
            {"request": {"method": "POST", "url": "http://flowshop.test:18923/api/dashboard/1/submit",
                          "headers": [], "postData": {"text": "{}"}},
             "response": {"status": 200, "content": {"text": ""}}},
        ]}}
        with tempfile.TemporaryDirectory() as tmp:
            har_path = Path(tmp) / "recording.har"
            har_path.write_text(json.dumps(har))
            target = find_child_mutation_target(har_path)
        self.assertIsNotNone(target, "probe should find the target via the recorded login, no cookie")
        self.assertEqual(target["cookie"], None)
        self.assertIsNotNone(target["login"])
        self.assertIn("login", target["login"]["url"])

    def test_probe_finding_id_never_collides_with_crew_evidence(self):
        # Regression: merge_finding must assign an ID that cannot match an LLM
        # crew evidence file (evidence/<id>.json), else load_report's ID-join
        # clobbers the probe's confirmed evidence with the crew's stub.
        import re
        import tempfile
        from pathlib import Path
        from backend.runtime.state_lock_probe import merge_finding
        probe_finding = {"source": "STATE_LOCK_PROBE", "title": "probe", "severity": "High",
                         "evidence": {"summary": "x"}, "verification": {}}
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            rep_dir = run_dir / 'reports' / 'flow'
            rep_dir.mkdir(parents=True)
            (rep_dir / 'findings.json').write_text(json.dumps({'findings': [{'id': 'F-001'}, {'id': 'F-002'}], 'results': []}))
            fid = merge_finding(run_dir, 'flow', probe_finding)
            data = json.loads((rep_dir / 'findings.json').read_text())
        probe = [f for f in data['findings'] if f.get('source') == 'STATE_LOCK_PROBE']
        self.assertTrue(probe, 'probe finding not merged')
        self.assertEqual(probe[0]['id'], fid)
        self.assertNotRegex(probe[0]['id'], r'^F-\d{3}$', 'probe ID must not be a crew-style F-NNN')


if __name__ == "__main__":
    unittest.main()
