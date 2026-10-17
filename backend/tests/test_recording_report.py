import json
import tempfile
import unittest
from pathlib import Path

from backend.runtime.verification import load_report
from backend.tests.recording_fixture import write_recording


class RecordingReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run, self.artifacts = write_recording(self.root, 'refund')
        self.report = self.run / 'reports/refund/findings.json'

    def test_empty_refund_report_uses_validated_recording_metadata_without_writes(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        report = load_report(self.report)
        self.assertEqual(report['flow_name'], 'refund')
        self.assertEqual(report['target_url'], 'http://localhost:3000/')
        self.assertEqual(report['run_timestamp'], '2026-09-17T04:01:45Z')
        self.assertEqual(report['recording_metadata']['source'], 'validated_recording')
        for key in ('planned_executions', 'execution_attempts', 'completed_executions', 'controls_held'):
            self.assertEqual(report['summary'][key], 0)
        self.assertEqual(report['findings'], [])
        self.assertEqual(report['results'], [])
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_invalid_recording_cannot_supply_trusted_metadata(self):
        self.report.write_text(json.dumps({'findings': [], 'recording_metadata': {
            'flow_name': 'invented', 'source': 'validated_recording'}}), encoding='utf-8')
        (self.run / 'recording_validated.marker').write_text('999', encoding='utf-8')
        report = load_report(self.report)
        self.assertNotIn('recording_metadata', report)
        self.assertNotIn('run_timestamp', report)

    def test_invalid_recording_date_is_not_fabricated(self):
        demo_path = self.artifacts / 'demo.json'
        demo = json.loads(demo_path.read_text(encoding='utf-8'))
        demo['timestamp_end'] = 'not-a-date'
        demo_path.write_text(json.dumps(demo), encoding='utf-8')
        report = load_report(self.report)
        self.assertNotIn('recording_metadata', report)
        self.assertNotIn('run_timestamp', report)


if __name__ == '__main__':
    unittest.main()
