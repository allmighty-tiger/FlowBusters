import asyncio
import tempfile
import unittest
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from backend.runtime.crew_runner import (
    ArtifactWatcher,
    CrewConfig,
    ProbeContractError,
    _cross_flow_artifact_events,
    _cross_flow_final_message,
    run_crew,
)
from backend.runtime.orchestrator import Phase, ProgressEvent, RunMode, run_flowbusters
from backend.runtime.probe_executor import execute_run
from backend.runtime.ui_provenance import UIProvenanceError


class RunModeProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_probe_contract_stops_agent_without_false_correction_wait(self):
        for defect in (
            ProbeContractError('literal violation.observed must be boolean, not NoneType'),
        ):
            with self.subTest(defect=type(defect).__name__), tempfile.TemporaryDirectory() as folder:
                run = Path(folder)
                (run / '_post_recording_instructions.md').write_text('test contract', encoding='utf-8')
                flow = run / 'flows' / 'test-flow'
                flow.mkdir(parents=True)
                for artifact in ('demo.json', 'recording.har'):
                    (flow / artifact).write_text('{}', encoding='utf-8')
                draft = run / 'reports' / 'test-flow' / 'findings.json'
                draft.parent.mkdir(parents=True)
                draft.write_text('{"findings": []}', encoding='utf-8')
                original_draft = draft.read_bytes()
                config = CrewConfig(
                    target_url='http://localhost:3000', flow_name='test-flow', run_dir=folder,
                    crew_dir=folder, mcp_config='test-mcp.json', claude_bin='unused', model='unused',
                    api_key='', display='', phase_timeout=30, overall_timeout=60)
                proc = MagicMock(pid=123, returncode=None)
                proc.stdout, proc.stderr = asyncio.StreamReader(), asyncio.StreamReader()

                def terminate():
                    proc.returncode = -15
                    proc.stdout.feed_eof()
                    proc.stderr.feed_eof()

                proc.terminate.side_effect = terminate
                proc.wait = AsyncMock(return_value=-15)
                events = []
                with patch('backend.runtime.crew_runner.prepare_run_dir', return_value=run), \
                     patch('backend.runtime.crew_runner.build_system_prompt', return_value='test'), \
                     patch('backend.runtime.crew_runner.prepare_recording_evidence', new_callable=AsyncMock), \
                     patch('backend.runtime.endpoint_catalog.prepare_endpoint_catalog'), \
                     patch('backend.runtime.crew_runner.asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=proc), \
                     patch.object(ArtifactWatcher, 'check', side_effect=defect) as gate, \
                     patch('backend.runtime.crew_runner.execute_validated_probes', new_callable=AsyncMock) as execute, \
                     patch('backend.runtime.crew_runner.load_report') as report:
                    result = await asyncio.wait_for(run_crew(config, events.append), timeout=2)
                proc.terminate.assert_called_once()
                self.assertEqual(proc.wait.await_count, 2)
                gate.assert_called_once()
                execute.assert_not_awaited()
                report.assert_not_called()
                self.assertEqual(draft.read_bytes(), original_draft)
                self.assertIn(str(defect), result['error'])
                failures = [event for event in events if event.phase == Phase.FAILED]
                self.assertEqual(len(failures), 1)
                self.assertIn(str(defect), failures[0].message)
                self.assertFalse(any('Waiting for a corrected file' in event.message for event in events))
                self.assertFalse(any(event.phase == Phase.COMPLETE for event in events))

    async def _capture_mode(self, cross_flow_inputs=None):
        events = []

        async def fake_run_crew(config, callback):
            callback(ProgressEvent(Phase.ANALYZE, 'test event'))
            return {}

        with tempfile.TemporaryDirectory() as folder, patch(
                'backend.runtime.crew_runner.run_crew', fake_run_crew):
            await run_flowbusters(
                'http://localhost:3000', flow_name='mode-test', run_dir=folder,
                progress_cb=events.append, cross_flow_inputs=cross_flow_inputs,
            )
        return events

    async def test_recorded_flow_keeps_recorded_mode_by_default(self):
        events = await self._capture_mode()
        self.assertEqual(events[0].run_mode, RunMode.RECORDED_FLOW)

    async def test_cross_flow_events_have_explicit_cross_flow_mode(self):
        events = await self._capture_mode({'target_url': 'http://localhost:3000', 'sources': []})
        self.assertEqual(events[0].run_mode, RunMode.CROSS_FLOW)

    def test_cross_flow_artifacts_are_user_facing_and_drafts_are_not_verified(self):
        self.assertEqual(_cross_flow_artifact_events('demo.json'), [])
        self.assertEqual(_cross_flow_artifact_events('recording.har'), [])
        model_events = _cross_flow_artifact_events('state_map.json')
        self.assertEqual(model_events[0].message, 'Application state model built')
        self.assertTrue(model_events[0].done)
        self.assertEqual(model_events[1].message, 'Identifying cross-flow conflicts')
        for name in ('findings.json', 'remediation.md'):
            event = _cross_flow_artifact_events(name)[0]
            self.assertIn('Draft', event.message)
            self.assertFalse(event.done)

    def test_cross_flow_loading_is_emitted_once_at_the_backend_source(self):
        source = inspect.getsource(run_crew)
        self.assertEqual(source.count("f'Loading {source_count} source flows'"), 1)

    def test_completed_run_counts_keep_categories_separate(self):
        summary = {
            'finding_count': 5,
            'confirmed': 0,
            'needs_review': 1,
            'not_reproduced': 4,
            'not_executed': 0,
            'errors': 0,
            'planned_executions': 6,
            'completed_executions': 6,
            'execution_attempts': 6,
            'execution_errors': 0,
            'process_errors': 0,
            'evidence_contract_errors': 0,
            'trace_mismatches': 0,
            'partial_coverage': 2,
            'excluded_setup_executions': 1,
            'deduplicated_primary_chains': 0,
            'execution_result_counts': {'needs_review': 2, 'not_reproduced': 4},
        }
        self.assertEqual(
            _cross_flow_final_message(summary, 12.3),
            'Final security findings (5 normalized): 0 confirmed, 1 need review, '
            '4 not reproduced, 0 coverage gaps, 0 check errors. '
            'Partial coverage: 2 supplementary scenarios. '
            'Excluded setup-path executions: 1. '
            'Probes: 6/6 authenticated terminal receipts from 6 attempts; '
            '0 process errors, 0 evidence-contract errors, 0 trace mismatches. '
            'Primary execution results: 2 need review, '
            '4 not reproduced. Deduplicated primary chains: 0. '
            'Total duration: 12.3s.',
        )

    def test_candidate_and_probe_counts_are_named_explicitly(self):
        source = inspect.getsource(run_crew)
        self.assertIn("f'Conflict hypotheses: {candidate_count} validated candidates'", source)
        self.assertIn("f'Preparing {script_count} executable probes'", source)


class ProbeProgressTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run = self.root / 'runs' / 'cross-flow-test'
        (self.run / 'mutations' / 'cross-flow-test').mkdir(parents=True)
        (self.run / 'reports' / 'cross-flow-test').mkdir(parents=True)
        (self.run / 'mutations' / 'cross-flow-test' / '001_cancel_then_refund.py').write_text(
            'print("unused")', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _receipt():
        return {
            'execution_id': 'receipt-1', 'exit_code': 0,
            'parsed_result': {'outcome': 'NOT_REPRODUCED'},
            'contract_validation': {'version': 1, 'status': 'valid', 'category': None, 'error': None},
        }

    async def test_recorded_flow_probe_message_remains_unchanged(self):
        messages = []
        with patch('backend.runtime.probe_executor.execute', AsyncMock(return_value=self._receipt())), patch(
                'backend.runtime.probe_executor.validate_probe_output', return_value=self._receipt()['contract_validation']):
            await execute_run(self.run, 'cross-flow-test', self.root, messages.append)
        self.assertEqual(messages, ['Executing 001_cancel_then_refund.py with transport capture'])

    async def test_cross_flow_probe_uses_readable_name_and_actual_terminal_count(self):
        events = []
        with patch('backend.runtime.probe_executor.execute', AsyncMock(return_value=self._receipt())), patch(
                'backend.runtime.probe_executor.validate_probe_output', return_value=self._receipt()['contract_validation']):
            await execute_run(
                self.run, 'cross-flow-test', self.root, lambda message: None,
                execution_progress=events.append,
            )
        self.assertEqual(events[0]['message'], 'Executing probe 1/1: Cancel then refund')
        self.assertRegex(events[1]['message'], r'^Probe 1/1 completed: Cancel then refund \(\d+\.\d+s\)$')
        self.assertEqual(events[-1]['message'],
            'Execution complete: 1/1 probes attempted; 1 authenticated terminal receipt; '
            '0 process errors; 0 evidence-contract errors; 0 trace mismatches; 0 pending')
        self.assertNotIn('.py', events[0]['message'])
        self.assertIn('001_cancel_then_refund.py', events[0]['technical_detail'])

    async def test_evidence_contract_failure_stops_before_remaining_probe(self):
        second = self.run / 'mutations' / 'cross-flow-test' / '002_second.py'
        second.write_text('print("unused")', encoding='utf-8')
        invalid = {'version': 1, 'status': 'invalid',
                   'category': 'evidence_contract_error',
                   'error': 'Probe output contract error: invariant missing'}
        receipt = {**self._receipt(), 'contract_validation': invalid}
        events = []
        mocked = AsyncMock(return_value=receipt)
        with patch('backend.runtime.probe_executor.execute', mocked), patch(
                'backend.runtime.probe_executor.validate_probe_output', return_value=invalid):
            await execute_run(self.run, 'cross-flow-test', self.root,
                              lambda message: None, execution_progress=events.append)
        self.assertEqual(mocked.await_count, 1)
        self.assertIn('evidence validation failed', events[1]['message'])
        self.assertEqual(events[-1]['message'],
            'Execution stopped: 1/2 probes attempted; 1 authenticated terminal receipt; '
            '0 process errors; 1 evidence-contract errors; 0 trace mismatches; 1 pending')

    def test_artifact_gate_rejects_literal_missing_invariant(self):
        broken = "verification = {'predicate': 'business_rule_must_hold'}\n"
        for path in (self.run / 'mutations' / 'cross-flow-test').glob('*.py'):
            path.write_text(broken, encoding='utf-8')
        for index in (2, 3):
            (self.run / 'mutations' / 'cross-flow-test' / f'00{index}_broken.py').write_text(
                broken, encoding='utf-8')
        with self.assertRaisesRegex(ProbeContractError, 'requires a supported executable invariant'):
            ArtifactWatcher(self.run, 'cross-flow-test').check()

    def test_artifact_gate_rejects_literal_null_violation_before_execution(self):
        broken = (
            "verification = {'predicate': 'business_rule_must_hold', "
            "'invariant': {'operator': 'sum_lte', 'terms': [['order', 'totalReturned']], "
            "'limit': ['order', 'originalAmount']}, "
            "'violation': {'observed': None, 'description': 'backend will compute'}}\n"
        )
        for path in (self.run / 'mutations' / 'cross-flow-test').glob('*.py'):
            path.write_text(broken, encoding='utf-8')
        for index in (2, 3):
            (self.run / 'mutations' / 'cross-flow-test' / f'00{index}_broken.py').write_text(
                broken, encoding='utf-8')
        with self.assertRaisesRegex(
                ProbeContractError,
                'literal violation.observed must be boolean, not NoneType'):
            ArtifactWatcher(self.run, 'cross-flow-test').check()


if __name__ == '__main__':
    unittest.main()
