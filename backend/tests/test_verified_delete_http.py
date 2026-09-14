"""Real HTTP capture -> saved evidence -> report ingestion regression tests."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend.runtime.verification import load_report

spec = importlib.util.spec_from_file_location('verified_delete', Path(__file__).parents[2] / 'crew/scripts/verified_delete.py')
executor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(executor)


class HttpEvidenceTest(unittest.TestCase):
    def run_case(self, status=200, removes=True, read_error=False, source='user'):
        state = {'ids': [2, 3], 'acted': False}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def reply(self, code, body):
                self.send_response(code)
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())
            def do_GET(self):
                if read_error and state['acted']:
                    return self.reply(404, {})
                self.reply(200, {'dashboard': {'state': 'APPROVED'},
                                'parts': [{'id': i} for i in state['ids']]})
            def do_DELETE(self):
                state['acted'] = True
                if removes:
                    state['ids'].remove(2)
                self.reply(status, {'ok': removes})
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f'http://127.0.0.1:{server.server_port}'
            config = dict(finding_id='F-002', read_url=origin + '/state',
                          delete_url=origin + '/parts/2', item_id=2,
                          state_path=['dashboard', 'state'], items_path=['parts'],
                          complete_collection=True, isolated_resource=True,
                          rule={'source': source, 'reference': 'Approved items must remain'},
                          remediation='Enforce approval lock on DELETE.')
            capture = executor.execute(config)
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / 'evidence').mkdir()
                (root / 'evidence/F-002.json').write_text(json.dumps(capture))
                report = root / 'findings.json'
                report.write_text(json.dumps({'findings': [{'id': 'F-002', 'title': 'Old summary'}],
                    'results': [{'finding_id': 'F-002', 'outcome': 'BUG_FOUND'}]}))
                return load_report(report)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_deleted_capture_and_linkage(self):
        report = self.run_case()
        finding = report['findings'][0]
        self.assertEqual(finding['verification_status'], 'CONFIRMED')
        self.assertEqual(len(finding['evidence']['requests']), 3)
        self.assertEqual(finding['verification']['before']['item_ids'], [2, 3])
        self.assertEqual(finding['verification']['after']['item_ids'], [3])
        self.assertIn('parts', finding['evidence']['responses'][2]['body'])
        self.assertEqual(report['results'][0]['outcome'], 'CONFIRMED')
        self.assertEqual(finding['remediation'], 'Enforce approval lock on DELETE.')

    def test_500_with_deletion(self):
        self.assertEqual(self.run_case(status=500)['summary']['confirmed'], 1)

    def test_200_without_deletion(self):
        self.assertEqual(self.run_case(removes=False)['summary']['not_reproduced'], 1)

    def test_blocked_delete(self):
        self.assertEqual(self.run_case(status=403, removes=False)['summary']['not_reproduced'], 1)

    def test_failed_read_not_confirmed_or_double_counted(self):
        report = self.run_case(read_error=True)
        self.assertEqual(report['summary']['confirmed'], 0)
        self.assertEqual(report['summary']['errors'], 1)

    def test_inferred_rule_not_confirmed(self):
        self.assertEqual(self.run_case(source='agent')['summary']['needs_review'], 1)
