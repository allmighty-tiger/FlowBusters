import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock
from backend.runtime.recorder import (
    DialogWaitTimeout,
    McpClient,
    RecordingError,
    add_ui_state,
    compact_snapshot,
    manifest_ids,
    network_sequence,
    optional_snapshot,
    record,
)

ROOT = Path(__file__).resolve().parents[2]


class RecorderTests(unittest.IsolatedAsyncioTestCase):
    async def run_recording(self, mode, recording_delay=0.05, recorder_env=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / 'crew/scripts').mkdir(parents=True)
        shutil.copy(ROOT / 'crew/scripts/synthesize_har.py', root / 'crew/scripts/synthesize_har.py')
        (root / 'scope.json').write_text(json.dumps({'allowed_domains': ['http://fixture.test'], 'allowed_paths_prefix': ['*']}))
        mcp = root / 'mcp.json'
        mcp.write_text(json.dumps({'mcpServers': {'playwright': {'command': sys.executable,
            'args': [str(ROOT / 'backend/tests/recorder_mcp_fixture.py'), mode]}}}))
        config = SimpleNamespace(target_url='http://fixture.test', flow_name='test', overall_timeout=10, auto_complete=False)
        opened = asyncio.Event()
        notices = []
        def notify(text):
            notices.append(text)
            if text.startswith('Browser ready'):
                opened.set()
        task = asyncio.create_task(record(config, root, mcp, recorder_env or os.environ, notify))
        await asyncio.wait_for(opened.wait(), 3)
        # No model process exists. Backend holds the MCP process until user Finish.
        await asyncio.sleep(recording_delay)
        self.assertFalse(task.done())
        calls = (root / 'calls.jsonl').read_text()
        self.assertNotIn('browser_close', calls)
        self.assertNotIn('browser_network_requests', calls)
        (root / 'recording_done.marker').write_text('user finished')
        if mode == 'missing':
            with self.assertRaises(RecordingError):
                await task
            self.assertFalse((root / 'recording_validated.marker').exists())
            self.assertNotIn('browser_close', (root / 'calls.jsonl').read_text())
            manifest = json.loads((root / 'flows/test/har_data/capture_manifest.json').read_text())
            self.assertEqual(manifest['missing'], [{'index': 3, 'part': 'response-body'}, {'index': 5, 'part': 'response-body'}])
            self.assertEqual(manifest['unrecoverable_response_bodies'], [3, 5])
        elif mode == 'navdetached':
            await task
            self.assertTrue((root / 'recording_validated.marker').exists())
            har = json.loads((root / 'flows/test/recording.har').read_text())
            self.assertEqual(len(har['log']['entries']), 2)
            texts = [e['response']['content']['text'] for e in har['log']['entries']]
            self.assertIn('{"ok": true}', texts)  # one entry captured
            self.assertIn('', texts)                # the detached entry is empty
            self.assertTrue((root / 'flows/test/har_data/unrecoverable_response_bodies.marker').exists())
        else:
            await task
            self.assertTrue((root / 'recording_validated.marker').exists())
            har = json.loads((root / 'flows/test/recording.har').read_text())
            self.assertEqual(len(har['log']['entries']), 2)
            self.assertTrue(all(e['response']['content']['text'] for e in har['log']['entries']))
            calls = [json.loads(line) for line in (root / 'calls.jsonl').read_text().splitlines()]
            self.assertEqual(calls[-1]['name'], 'browser_close')
            demo = json.loads((root / 'flows/test/demo.json').read_text())
            if mode == 'timeline':
                states = demo['workflow_timeline']['ui_states']
                self.assertEqual(len(states), 2)
                self.assertEqual(states[-1]['changes_from_previous']['appeared'],
                                 ['text: Total $80', 'status: Coupon applied',
                                  'button "Apply Coupon" [disabled]'])
                self.assertEqual(demo['workflow_timeline']['network_sequence'][0]['method'], 'GET')
            if mode in ('final_snapshot_error', 'snapshot_error'):
                self.assertIsNone(demo['final_snapshot'])
                self.assertIn('Target page has been closed', demo['warnings'][-1])
                snapshot_positions = [i for i, c in enumerate(calls) if c['name'] == 'browser_snapshot']
                dump_positions = [i for i, c in enumerate(calls) if c['name'] == 'browser_network_request']
                self.assertGreater(snapshot_positions[-1], max(dump_positions))
            if mode == 'modal':
                self.assertFalse(any('confirmation dialog' in n or 'Dialog cleared' in n for n in notices),
                                'dialog auto-recovery must not be surfaced to the user')
                self.assertEqual(sum(c['name'] == 'browser_handle_dialog' and c['args'].get('accept') is False for c in calls), 1)
                self.assertEqual(sum(c['name'] == 'browser_network_requests' for c in calls), 2)
            if mode == 'retry':
                self.assertEqual(sum(c['name'] == 'browser_network_request' and c['args'].get('index') == 3 and c['args'].get('part') == 'response-body' for c in calls), 2)
            if mode == 'bodyless':
                self.assertEqual(json.loads((root / 'flows/test/har_data/capture_manifest.json').read_text())['missing'], [])
                self.assertEqual([c['args']['index'] for c in calls if c['name'] == 'browser_network_request' and c['args'].get('part') == 'request-body'], [])
                self.assertNotIn('postData', har['log']['entries'][0]['request'])
            if mode == 'postbody':
                self.assertEqual([c['args']['index'] for c in calls if c['name'] == 'browser_network_request' and c['args'].get('part') == 'request-body'], [3])
                self.assertEqual(har['log']['entries'][0]['request']['postData']['text'],
                                 'username=x&password=y')

    async def test_modal_wait_then_saves_har(self):
        await self.run_recording('modal')

    async def test_backend_owns_recording_until_finish(self):
        await self.run_recording('normal')

    async def test_missing_body_retried_before_close(self):
        await self.run_recording('retry')

    async def test_permanent_missing_body_stops_pipeline(self):
        await self.run_recording('missing')

    async def test_bodyless_request_skips_request_body(self):
        await self.run_recording('bodyless')

    async def test_advertised_request_body_is_captured(self):
        await self.run_recording('postbody')

    async def test_nav_detached_response_body_is_non_fatal(self):
        await self.run_recording('navdetached')


    async def test_final_snapshot_failure_preserves_validated_har(self):
        await self.run_recording('final_snapshot_error')

    async def test_both_snapshots_optional(self):
        await self.run_recording('snapshot_error')

    async def test_changed_intermediate_ui_is_written_to_demo_timeline(self):
        env = dict(os.environ)
        env['FLOWBUSTERS_UI_SNAPSHOT_INTERVAL'] = '0.25'
        await self.run_recording('timeline', recording_delay=0.35, recorder_env=env)

    async def test_tool_error_retains_diagnostic(self):
        client = McpClient(None)
        client.rpc = AsyncMock(return_value={'isError': True, 'content': [
            {'type': 'text', 'text': 'Target page has been closed'}]})
        with self.assertRaisesRegex(RecordingError, 'Target page has been closed'):
            await client.call('browser_snapshot', {})

    async def test_rpc_error_retains_diagnostic(self):
        process = SimpleNamespace(stdin=SimpleNamespace(write=lambda data: None, drain=AsyncMock()),
            stdout=SimpleNamespace(readline=AsyncMock(return_value=
                b'{"id":1,"error":{"code":-32000,"message":"Session disconnected"}}\n')))
        with self.assertRaisesRegex(RecordingError, 'Session disconnected'):
            await McpClient(process).rpc('tools/list')

    async def test_snapshot_timeout_is_warning(self):
        client = SimpleNamespace(call=AsyncMock(side_effect=TimeoutError()))
        warnings = []
        self.assertIsNone(await optional_snapshot(client, warnings, 'Final', lambda _: None))
        self.assertIn('TimeoutError', warnings[0])

    def test_manifest_preserves_nonconsecutive_ids(self):
        self.assertEqual(manifest_ids('3. [POST] /login\n7. [GET] /items'), [3, 7])
        with self.assertRaises(RecordingError):
            manifest_ids('unknown format')

    def test_compact_snapshot_keeps_business_context_and_redacts_secrets(self):
        result = {'content': [{'type': 'text', 'text': '''### Page state
- Page URL: https://shop.test/checkout?token=abc&cart=12#access_token=xyz
- Page Title: Checkout
- Page Snapshot:
```yaml
- generic [ref=e1]:
  - heading "Checkout" [level=1] [ref=e2]
  - textbox "Coupon code" [ref=e3]: SAVE20
  - textbox "Password" [ref=e4]: hunter2
  - button "Apply Coupon" [disabled] [ref=e5]
  - text: Maximum discount is $20
```
'''}]}
        compact = compact_snapshot(result)
        self.assertEqual(compact['url'], 'https://shop.test/checkout?token=%5BREDACTED%5D&cart=12#access_token=%5BREDACTED%5D')
        self.assertEqual(compact['title'], 'Checkout')
        self.assertIn('button "Apply Coupon" [disabled]', compact['elements'])
        self.assertIn('text: Maximum discount is $20', compact['elements'])
        self.assertNotIn('hunter2', json.dumps(compact))
        self.assertNotIn('[ref=', json.dumps(compact))

    def test_ui_timeline_deduplicates_and_describes_delta(self):
        first = {'content': [{'type': 'text', 'text': '- Page URL: https://shop.test/cart\n- text: Total $100'}]}
        second = {'content': [{'type': 'text', 'text': '- Page URL: https://shop.test/cart\n- text: Total $80\n- status: Coupon applied'}]}
        timeline = []
        add_ui_state(timeline, first, 'initial', '2026-01-01T00:00:00Z')
        add_ui_state(timeline, first, 'change', '2026-01-01T00:00:01Z')
        add_ui_state(timeline, second, 'final', '2026-01-01T00:00:02Z')
        self.assertEqual(len(timeline), 2)
        self.assertEqual(timeline[0]['observations'], 2)
        self.assertEqual(timeline[1]['changes_from_previous']['appeared'],
                         ['text: Total $80', 'status: Coupon applied'])
        self.assertEqual(timeline[1]['changes_from_previous']['disappeared'], ['text: Total $100'])

    def test_network_sequence_contains_keys_not_values(self):
        outline = network_sequence([{'request': {
            'method': 'POST', 'url': 'https://shop.test/api/coupon?csrf=secret',
            'postData': {'text': '{"code":"SAVE20","cart_id":12}'},
        }, 'response': {'status': 200, 'content': {'text': '{"discount":20,"total":80}'}}}])
        self.assertEqual(outline[0]['request_body_keys'], ['cart_id', 'code'])
        self.assertEqual(outline[0]['response_body_keys'], ['discount', 'total'])
        self.assertNotIn('SAVE20', json.dumps(outline))
        self.assertNotIn('secret', json.dumps(outline))

    async def test_failed_recording_never_launches_agent(self):
        from backend.runtime.crew_runner import CrewConfig, run_crew
        with tempfile.TemporaryDirectory() as temp:
            config = CrewConfig(target_url='http://fixture.test', flow_name='test',
                run_dir=temp, crew_dir=str(ROOT / 'crew'), mcp_config='unused.json',
                claude_bin='claude', model='test', api_key='', display=':0',
                phase_timeout=10, overall_timeout=10)
            with patch('backend.runtime.crew_runner.record', AsyncMock(side_effect=RecordingError('Incomplete body capture'))), patch('backend.runtime.crew_runner.asyncio.create_subprocess_exec', AsyncMock()) as spawn:
                result = await run_crew(config, lambda event: None)
                spawn.assert_not_called()
                self.assertIn('Incomplete body capture', result['error'])


class ModalWaitTests(unittest.IsolatedAsyncioTestCase):
    def modal(self):
        return {'isError': True, 'content': [{'type': 'text', 'text':
            'Tool does not handle the modal state; can be handled by browser_handle_dialog'}]}

    async def test_timeout_keeps_clear_reason(self):
        notices = []
        client = McpClient(None, notices.append, dialog_timeout=0)
        client.rpc = AsyncMock(return_value=self.modal())
        with self.assertRaisesRegex(DialogWaitTimeout, 'dialog remained open'):
            await client.call('browser_network_requests', {})
        self.assertEqual(client.rpc.await_count, 1)
        self.assertEqual(len(notices), 0)

    async def test_body_capture_resumes_after_auto_dismiss(self):
        client = McpClient(None, poll_interval=0)
        client.rpc = AsyncMock(side_effect=[self.modal(), {'content': []}, {'content': []}])
        await client.call('browser_network_request', {'index': 3, 'part': 'response-body'})
        # Modal read, auto-dismiss (as Cancel), then the re-read — the action is
        # never accepted.
        self.assertEqual([c.args[1]['name'] for c in client.rpc.await_args_list],
                         ['browser_network_request', 'browser_handle_dialog', 'browser_network_request'])

    async def test_stuck_dialog_bails_after_dismiss_attempts(self):
        notices = []
        client = McpClient(None, notices.append, dialog_timeout=1000, max_dismissals=2)
        client.rpc = AsyncMock(return_value=self.modal())
        with self.assertRaisesRegex(DialogWaitTimeout, 'still present after'):
            await client.call('browser_network_requests', {})
        self.assertEqual(client.rpc.await_count, 5)

    async def test_mutating_action_is_never_retried(self):
        client = McpClient(None)
        client.rpc = AsyncMock(return_value=self.modal())
        with self.assertRaises(RecordingError):
            await client.call('browser_navigate', {'url': 'http://fixture.test'})
        self.assertEqual(client.rpc.await_count, 1)

    async def test_timeline_snapshot_never_dismisses_user_dialog(self):
        client = McpClient(None)
        client.rpc = AsyncMock(return_value=self.modal())
        with self.assertRaises(RecordingError):
            await client.call('browser_snapshot', {}, recover_modal=False)
        self.assertEqual(client.rpc.await_count, 1)
