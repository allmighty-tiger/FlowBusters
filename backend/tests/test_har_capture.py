import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "crew" / "scripts" / "synthesize_har.py"


DETAIL = """#3 [GET] http://flowshop.test:18923/api/dashboard/1

  General
    status:    [200] OK
    duration:  12ms
    type:      fetch
    mimeType:  application/json

  Request headers
    accept: application/json
    cookie: session=test

  Response headers
    content-length: 47
    content-type: application/json
"""


class HarCaptureTests(unittest.TestCase):
    def run_builder(self, include_body=True):
        tmp = tempfile.TemporaryDirectory()
        directory = Path(tmp.name)
        (directory / "network_requests.log").write_text(
            "3. [GET] http://flowshop.test:18923/api/dashboard/1 => [200] OK\n"
        )
        (directory / "request_003.log").write_text(DETAIL)
        if include_body:
            (directory / "request_003_response_body.txt").write_text(
                '{"status":"APPROVED","parts":[{"id":7}]}'
            )
        output = directory / "recording.har"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(directory), str(output)],
            text=True, capture_output=True,
        )
        return tmp, output, result

    def test_separate_response_body_is_written_to_har(self):
        tmp, output, result = self.run_builder()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = json.loads(output.read_text())["log"]["entries"][0]
        self.assertEqual(entry["request"]["url"], "http://flowshop.test:18923/api/dashboard/1")
        self.assertEqual(entry["response"]["content"]["text"],
                         '{"status":"APPROVED","parts":[{"id":7}]}')

    def test_missing_json_response_body_fails_capture_gate(self):
        tmp, output, result = self.run_builder(include_body=False)
        self.addCleanup(tmp.cleanup)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing response body", result.stderr)
        self.assertFalse(output.exists())

    def test_missing_body_with_unrecoverable_marker_is_allowed(self):
        tmp, output, result = self.run_builder(include_body=False)
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "unrecoverable_response_bodies.marker").write_text('{"indexes": [3]}')
        result = subprocess.run(
            [sys.executable, str(SCRIPT), Path(tmp.name), output],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = json.loads(output.read_text())["log"]["entries"][0]
        self.assertEqual(entry["response"]["content"]["text"], "")

    def test_unrecoverable_marker_with_body_present_is_allowed(self):
        tmp, output, result = self.run_builder(include_body=True)
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "unrecoverable_response_bodies.marker").write_text('{"indexes": [3]}')
        result = subprocess.run(
            [sys.executable, str(SCRIPT), Path(tmp.name), output],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
