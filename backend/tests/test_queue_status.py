"""Per-probe rejection decisions are authenticated separately from HTTP receipts."""
import json
from pathlib import Path
import tempfile
import unittest

from backend.runtime.queue_status import assessment_status, load_rejections, save_rejections, status_message
from backend.runtime.probe_executor import reconcile
from backend.runtime.verification import normalize_report


class QueueStatusTests(unittest.TestCase):
    def test_status_distinguishes_errors_rejections_and_unfinished_queue(self):
        base = {'planned_executions': 6, 'execution_attempts': 6, 'execution_errors': 0}
        self.assertEqual(assessment_status(base)['status'], 'COMPLETED')
        error = assessment_status(dict(base, execution_errors=1))
        self.assertEqual(error['status'], 'COMPLETED_WITH_ERRORS')
        self.assertEqual(error['pending'], 0)
        rejected = assessment_status(dict(base, execution_attempts=5, preflight_rejected=1))
        self.assertEqual(rejected, dict(status='COMPLETED_WITH_ERRORS', planned=6,
                                       executed=5, skipped=1, failed=1, pending=0))
        self.assertEqual(assessment_status(dict(base, execution_attempts=2))['status'], 'FAILED')
        self.assertIn('planned 6, executed 5, skipped 1, failed 1, pending 0', status_message(rejected))

    def test_rejection_maps_to_one_finding_and_unsigned_extra_stays_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = root / 'runs/flow'
            scripts = run / 'mutations/flow'; scripts.mkdir(parents=True)
            script = scripts / 'bad.py'; script.write_text('PROBE_PLAN = {}')
            report_dir = run / 'reports/flow'; report_dir.mkdir(parents=True)
            save_rejections(run, 'flow', root, {script: 'Numeric operand missing: order.requestedAmount'})
            draft = {'findings': [{'id': 'F-001', 'script': 'bad.py', 'title': 'Invalid plan'},
                                  {'id': 'F-002', 'title': 'No observed terminal endpoint'}]}
            report = normalize_report(reconcile(draft, report_dir, root))
            self.assertEqual([(f['id'], f['verification_status']) for f in report['findings']], [('F-001', 'CHECK_ERROR')])
            self.assertEqual([h['id'] for h in report['unexecuted_hypotheses']], ['F-002'])
            self.assertEqual(report['results'], [])
            self.assertEqual(report['assessment_status']['pending'], 0)
            self.assertEqual(report['assessment_status']['skipped'], 1)
            again = normalize_report(reconcile(report, report_dir, root))
            self.assertEqual(again['unexecuted_hypotheses'], report['unexecuted_hypotheses'])
            self.assertEqual(again['summary'], report['summary'])
            path = run / 'preflight_rejections.json'
            data = json.loads(path.read_text()); data['rejections'][0]['error'] = 'forged'
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'authentication failed'):
                load_rejections(run, root)

    def test_agent_flag_cannot_create_backend_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = root / 'runs/flow'
            scripts = run / 'mutations/flow'; scripts.mkdir(parents=True)
            (scripts / 'probe.py').write_text('print(1)')
            report_dir = run / 'reports/flow'; report_dir.mkdir(parents=True)
            report = normalize_report(reconcile({'findings': [{'id': 'F-1', 'script': 'probe.py',
                '_preflight_error': 'agent claims preflight happened'}]}, report_dir, root))
            self.assertNotEqual(report['findings'][0]['verification_status'], 'CHECK_ERROR')
            self.assertEqual(report['assessment_status']['pending'], 1)
            self.assertEqual(report['summary']['preflight_rejected'], 0)
