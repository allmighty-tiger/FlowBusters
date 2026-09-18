import asyncio
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.runtime.crew_runner import CrewConfig, run_crew
from backend.runtime.orchestrator import Phase
from backend.runtime.ui_provenance import UIProvenanceError, validate_state_map
from backend.tests.recording_fixture import write_recording


TEXT = 'paragraph : This order has no actions requiring your attention.'
BAD = '/workflow_timeline/ui_states/5/elements/64'
GOOD = '/workflow_timeline/ui_states/5/elements/67'


class StateMapCorrectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.flow = 'new-refund'
        self.run, self.artifacts = write_recording(self.root, self.flow)
        self.map_path = self.artifacts / 'state_map.json'
        self.state = json.loads(self.map_path.read_text(encoding='utf-8'))
        demo_path = self.artifacts / 'demo.json'
        demo = json.loads(demo_path.read_text(encoding='utf-8'))
        states = [{'step': step, 'elements': []} for step in range(1, 7)]
        # Exact saved UIR-004-F2 mismatch: step 6, index 64 is just 'paragraph',
        # while the uniquely observed full text is at index 67.
        states[5]['elements'] = ['generic : filler'] * 64 + [
            'paragraph', 'region "Order actions" :',
            'heading "Order actions" [level=2]', TEXT]
        demo['workflow_timeline']['ui_states'] = states
        demo_path.write_text(json.dumps(demo), encoding='utf-8')
        self.state['semantic_ui_capture']['ui_state_count'] = 6
        self.state['observed_ui_rules'] = [{
            'schema_version': 1, 'id': 'UIR-004',
            'statement': 'Cancel order becomes unavailable once the refund is completed (terminal state)',
            'facts': [{'id': 'UIR-004-F2', 'type': 'explicit_ui_text',
                       'provenance_type': 'explicit_visible_ui_text', 'source_run': self.flow,
                       'artifact': 'demo.json', 'step': 6, 'text': TEXT, 'json_pointer': BAD}],
            'inference': {'provenance_type': 'agent_inference',
                          'text': 'No further mutating actions should be possible.',
                          'derived_from': ['UIR-004-F2']},
        }]
        self.map_path.write_text(json.dumps(self.state), encoding='utf-8')
        self.original = self.map_path.read_bytes()
        self.raw = {name: (self.artifacts / name).read_bytes() for name in ('demo.json', 'recording.har')}
        crew = Path(__file__).resolve().parents[2] / 'crew'
        for relative in ('agents/analyst/charter.md', 'skills/analyze-har/OBSERVED_UI_RULES.md'):
            target = self.run / 'crew' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(crew / relative, target)
        (self.run / '_post_recording_instructions.md').write_text('test instructions', encoding='utf-8')
        self.config = CrewConfig(
            target_url='http://localhost:3000', flow_name=self.flow, run_dir=str(self.root),
            crew_dir=str(crew), mcp_config='unused', claude_bin='never-run-real-agent',
            model='test', api_key='', display='', phase_timeout=10, overall_timeout=30)

    async def _run(self, proposals, *, live_agent=False):
        feedbacks = []
        events = []

        async def spawn(*args, **kwargs):
            proc = MagicMock(pid=123, returncode=0)
            proc.wait = AsyncMock(return_value=0)
            if args[args.index('--output-format') + 1] == 'stream-json':
                proc.stdout, proc.stderr = asyncio.StreamReader(), asyncio.StreamReader()
                proc.stdout.feed_eof()
                proc.stderr.feed_eof()
                if live_agent:
                    proc.returncode = None
                    asyncio.get_running_loop().call_later(0.01, lambda: setattr(proc, 'returncode', 0))
            else:
                self.assertEqual(args[args.index('--tools') + 1], 'Read')
                self.assertIn('--strict-mcp-config', args)
                self.assertIn('--bare', args)
                self.assertEqual(args[args.index('--setting-sources') + 1], '')
                workspace = Path(kwargs['cwd'])
                self.assertNotEqual(workspace, self.run)
                feedbacks.append(json.loads((workspace / 'validator_error.json').read_text(encoding='utf-8')))
                for name, raw in self.raw.items():
                    self.assertEqual((workspace / name).read_bytes(), raw)
                self.assertEqual(self.map_path.read_bytes(), self.original)
                proposal = proposals[min(len(feedbacks) - 1, len(proposals) - 1)]
                if isinstance(proposal, asyncio.TimeoutError):
                    proc.communicate = AsyncMock(side_effect=[proposal, (b'', b'')])
                    proc.returncode = None
                    proc.kill.side_effect = lambda: setattr(proc, 'returncode', -1)
                else:
                    envelope = {'is_error': False, 'result': json.dumps(proposal)}
                    proc.communicate = AsyncMock(return_value=(json.dumps(envelope).encode(), b''))
            return proc

        async def execute(*args, **kwargs):
            # The actual execute_validated_probes gate must pass first.
            validate_state_map(json.loads(self.map_path.read_text()), self.artifacts, self.flow)
            self.assertGreater(len(feedbacks), 0)

        with patch('backend.runtime.crew_runner.prepare_run_dir', return_value=self.run), \
             patch('backend.runtime.crew_runner.build_system_prompt', return_value='test'), \
             patch('backend.runtime.crew_runner.prepare_recording_evidence', new_callable=AsyncMock), \
             patch('backend.runtime.crew_runner.asyncio.create_subprocess_exec', side_effect=spawn), \
             patch('backend.runtime.probe_executor.execute_run', new_callable=AsyncMock, side_effect=execute) as executor:
            result = await run_crew(self.config, events.append)
        for name, raw in self.raw.items():
            self.assertEqual((self.artifacts / name).read_bytes(), raw)
        return result, feedbacks, executor, events

    def _proposal(self, corrected=True):
        state = deepcopy(self.state)
        if corrected:
            state['observed_ui_rules'][0]['facts'][0]['json_pointer'] = GOOD
        return {'state_map': state, 'correction_notes': f'UIR-004-F2: {BAD} is paragraph; exact text is at {GOOD}.'}

    async def test_exact_saved_mismatch_is_sent_to_analyst_corrected_and_gated_before_execution(self):
        with self.assertRaises(UIProvenanceError) as caught:
            validate_state_map(self.state, self.artifacts, self.flow)
        self.assertEqual(caught.exception.details['pointed_element'], 'paragraph')
        result, feedbacks, execute, events = await self._run([self._proposal()], live_agent=True)
        self.assertIsNone(result['error'])
        self.assertEqual(len(feedbacks), 1)
        details = feedbacks[0]['details']
        self.assertEqual(details['code'], 'EXPLICIT_UI_TEXT_MISMATCH')
        self.assertEqual((details['rule_id'], details['fact_id']), ('UIR-004', 'UIR-004-F2'))
        self.assertEqual((details['claimed_text'], details['pointed_element'], details['json_pointer']), (TEXT, 'paragraph', BAD))
        execute.assert_awaited_once()
        self.assertEqual((self.run / 'state_map_corrections/original_state_map.json').read_bytes(), self.original)
        self.assertTrue(any('correction 1/2 validated' in event.message for event in events))
        self.assertTrue(any('Analyst correction queued' in event.message for event in events))

    async def test_failed_correction_is_bounded_precise_and_never_executes_probes(self):
        result, feedbacks, execute, events = await self._run([self._proposal(False)], live_agent=True)
        self.assertEqual(len(feedbacks), 2)
        self.assertIn('failed after 2 attempts', result['error'])
        self.assertIn('UIR-004 fact UIR-004-F2: UI text differs', result['error'])
        execute.assert_not_awaited()
        self.assertEqual(self.map_path.read_bytes(), self.original)
        self.assertFalse(any(event.phase == Phase.COMPLETE for event in events))

    async def test_dropping_a_rule_or_fact_cannot_make_correction_pass(self):
        proposal = self._proposal()
        proposal['state_map']['observed_ui_rules'] = []
        result, feedbacks, execute, _ = await self._run([proposal])
        self.assertEqual(len(feedbacks), 2)
        self.assertIn('changed or dropped a rule/fact/claim', result['error'])
        execute.assert_not_awaited()
        self.assertEqual(self.map_path.read_bytes(), self.original)

    async def test_second_attempt_can_correct_first_rejected_proposal(self):
        result, feedbacks, execute, _ = await self._run([self._proposal(False), self._proposal()])
        self.assertIsNone(result['error'])
        self.assertEqual(len(feedbacks), 2)
        execute.assert_awaited_once()

    async def test_claim_cannot_be_rewritten_to_match_the_wrong_element(self):
        proposal = self._proposal(False)
        proposal['state_map']['observed_ui_rules'][0]['facts'][0]['text'] = 'paragraph'
        result, feedbacks, execute, _ = await self._run([proposal])
        self.assertEqual(len(feedbacks), 2)
        self.assertIn('changed or dropped a rule/fact/claim', result['error'])
        execute.assert_not_awaited()

    async def test_ambiguous_text_is_not_repaired_by_picking_a_matching_pointer(self):
        demo_path = self.artifacts / 'demo.json'
        demo = json.loads(demo_path.read_text(encoding='utf-8'))
        demo['workflow_timeline']['ui_states'][5]['elements'].append(TEXT)
        demo_path.write_text(json.dumps(demo), encoding='utf-8')
        self.raw['demo.json'] = demo_path.read_bytes()
        result, feedbacks, execute, _ = await self._run([self._proposal()])
        self.assertEqual(len(feedbacks), 2)
        self.assertIn('UI text fact is ambiguous', result['error'])
        execute.assert_not_awaited()
        self.assertEqual(self.map_path.read_bytes(), self.original)

    async def test_existing_receipt_prevents_correction_of_an_executed_map(self):
        receipt = self.run / 'reports' / self.flow / 'executions' / 'existing.json'
        receipt.parent.mkdir()
        receipt.write_text('{"execution_id": "existing"}', encoding='utf-8')
        result, feedbacks, execute, _ = await self._run([self._proposal()])
        self.assertIn('execution receipts already exist', result['error'])
        self.assertEqual(feedbacks, [])
        self.assertEqual(self.map_path.read_bytes(), self.original)
        execute.assert_not_awaited()

    async def test_correction_timeout_is_bounded_and_preserves_precise_source_error(self):
        result, feedbacks, execute, _ = await self._run([asyncio.TimeoutError()])
        self.assertEqual(len(feedbacks), 2)
        self.assertIn('TimeoutError', result['error'])
        self.assertIn('UIR-004 fact UIR-004-F2', result['error'])
        self.assertIn(BAD, result['error'])
        execute.assert_not_awaited()
        self.assertEqual(self.map_path.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
