import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.api.main import app


class ReportApiErrorTests(unittest.TestCase):
    def test_invalid_gate_is_visible_without_crashing_other_reports_or_trusting_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            from backend.runtime.probe_executor import key_for
            key_for(root, create=True)
            for name in ('valid', 'untrusted'):
                folder = root / 'runs' / name / 'reports' / name
                folder.mkdir(parents=True)
                (folder / 'findings.json').write_text(json.dumps({'findings': [], 'summary': {'confirmed': 99}}))
            (root / 'runs/untrusted/candidate_gate.json').write_text(json.dumps({
                'flow': 'untrusted', 'signature': '0' * 64, 'allowed_scripts': []}))
            with patch.dict('os.environ', {'ARTIFACTS_DIR': str(root)}), TestClient(app) as client:
                response = client.get('/api/assessments/reports')
                self.assertEqual(response.status_code, 200)
                rows = {r['flow_name']: r for r in response.json()['reports']}
                self.assertEqual(rows['valid']['confirmed'], 0)
                self.assertEqual(rows['untrusted']['load_error'], 'Candidate gate signature invalid')
                self.assertNotIn('confirmed', rows['untrusted'])
                detail = client.get('/api/assessments/report?flow_name=untrusted')
                self.assertEqual(detail.status_code, 422)
                self.assertIn('Candidate gate signature invalid', detail.json()['detail'])
