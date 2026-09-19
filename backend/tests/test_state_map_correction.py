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
# Default (model-path) saved fixture: the pointer is in the WRONG step-6 state
# (state index 5, which only holds filler), so the uniquely correct element
# (state index 6, index 67) is not reachable by an in-state index fix.
# Deterministic relocation finds 0 matches in the pointed state and is a no-op;
# only the bounded Analyst loop can relocate the pointer to the right state.
BAD = '/workflow_timeline/ui_states/5/elements/1'
GOOD = '/workflow_timeline/ui_states/6/elements/67'
# Deterministic variant: the pointer is in the right state (index 6) but the wrong
# index; the unique correct element sits at index 67, so the backend re-points it.
BAD_INSTATE = '/workflow_timeline/ui_states/6/elements/64'


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
        # Two distinct UI states both report step 6. The pointed state (index 5) holds
        # only filler and never the target text, so an in-state index fix cannot reach
        # it; the target lives uniquely in the other step-6 state (index 6, index 67).
        # This makes the DEFAULT saved mismatch repairable only by the bounded Analyst
        # loop (cross-state relocation), while the deterministic test uses a second,
        # in-state off-by-one pointer that the backend can fix on its own.
        states = [{'step': step, 'elements': []} for step in range(1, 6)]
        states.append({'step': 6, 'elements': ['generic : filler', 'generic : filler', 'paragraph']})
        states.append({'step': 6, 'elements': ['generic : filler'] * 64 + [
            'paragraph', 'region "Order actions" :',
            'heading "Order actions" [level=2]', TEXT]})
        demo['workflow_timeline']['ui_states'] = states
        demo_path.write_text(json.dumps(demo), encoding='utf-8')
        self.state['semantic_ui_capture']['ui_state_count'] = len(states)
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

    async def test_exact_saved_mismatch_is_repaired_deterministically_without_analyst(self):
        # A locator slip within the correct state: the pointer is one index low, so
        # the pointed element is a bare 'paragraph' but the exact text is uniquely at
        # the next index. The backend re-points it without any model call.
        self.state['observed_ui_rules'][0]['facts'][0]['json_pointer'] = BAD_INSTATE
        self.map_path.write_text(json.dumps(self.state), encoding='utf-8')
        self.original = self.map_path.read_bytes()
        self.raw['demo.json'] = (self.artifacts / 'demo.json').read_bytes()
        with self.assertRaises(UIProvenanceError) as caught:
            validate_state_map(self.state, self.artifacts, self.flow)
        self.assertEqual(caught.exception.details['pointed_element'], 'paragraph')
        result, feedbacks, execute, events = await self._run([self._proposal()], live_agent=True)
        self.assertIsNone(result['error'])
        self.assertEqual(feedbacks, [])  # no Analyst round-trip needed
        execute.assert_awaited_once()
        repaired = json.loads(self.map_path.read_text(encoding='utf-8'))
        self.assertEqual(
            repaired['observed_ui_rules'][0]['facts'][0]['json_pointer'], GOOD)
        audit = self.run / 'state_map_corrections'
        self.assertEqual((audit / 'original_state_map.json').read_bytes(), self.original)
        record = json.loads((audit / 'attempt-00-deterministic.json').read_text(encoding='utf-8'))
        self.assertEqual(record['method'], 'deterministic_relocation')
        self.assertEqual(record['changes'], [{
            'rule_id': 'UIR-004', 'fact_id': 'UIR-004-F2',
            'old_pointer': BAD_INSTATE, 'new_pointer': GOOD,
            'evidence': 'unique exact-text element in the referenced state'}])
        self.assertTrue(any('deterministic relocation validated' in event.message for event in events))

    async def test_deterministic_relocation_repairs_an_out_of_bounds_pointer(self):
        # The Analyst pointed at an index beyond the state's element array
        # (state 6 has 68 elements, so index 999 is unresolvable). The claimed text
        # exists uniquely at index 67 in that same state, so the pointer is provably
        # wrong and relocation re-points it — no Analyst round-trip. This is the
        # refund2 F3 case that the flaky model correction had been failing on.
        oob = '/workflow_timeline/ui_states/6/elements/999'
        self.state['observed_ui_rules'][0]['facts'][0]['json_pointer'] = oob
        self.map_path.write_text(json.dumps(self.state), encoding='utf-8')
        self.original = self.map_path.read_bytes()
        self.raw['demo.json'] = (self.artifacts / 'demo.json').read_bytes()
        with self.assertRaises(UIProvenanceError):
            validate_state_map(self.state, self.artifacts, self.flow)
        result, feedbacks, execute, events = await self._run([self._proposal()], live_agent=True)
        self.assertIsNone(result['error'])
        self.assertEqual(feedbacks, [])  # deterministic, not model
        execute.assert_awaited_once()
        repaired = json.loads(self.map_path.read_text(encoding='utf-8'))
        self.assertEqual(
            repaired['observed_ui_rules'][0]['facts'][0]['json_pointer'], GOOD)
        record = json.loads(
            (self.run / 'state_map_corrections/attempt-00-deterministic.json').read_text(encoding='utf-8'))
        self.assertEqual(record['changes'], [{
            'rule_id': 'UIR-004', 'fact_id': 'UIR-004-F2',
            'old_pointer': oob, 'new_pointer': GOOD,
            'evidence': 'unique exact-text element in the referenced state'}])

    async def test_deterministic_relocation_cannot_repair_a_genuine_mismatch(self):
        # A claimed text that does not appear anywhere in the referenced state has
        # zero matches, so deterministic relocation must leave the pointer untouched
        # and fall back to the bounded model correction, which (given a wrong
        # proposal) fails. Probes never run and the map stays unchanged.
        self.state['observed_ui_rules'][0]['facts'][0]['text'] = 'zzz-no-such-element'
        self.map_path.write_text(json.dumps(self.state), encoding='utf-8')
        self.original = self.map_path.read_bytes()
        result, feedbacks, execute, _ = await self._run([self._proposal(), self._proposal(False)])
        self.assertIsNotNone(result['error'])
        self.assertEqual(len(feedbacks), 2)  # deterministic pass no-op -> full model loop
        execute.assert_not_awaited()
        self.assertEqual(self.map_path.read_bytes(), self.original)

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
        demo['workflow_timeline']['ui_states'][6]['elements'].append(TEXT)
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
