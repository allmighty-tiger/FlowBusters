import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.runtime.crew_runner import (
    execute_validated_probes,
    prepare_recording_evidence,
)
from backend.runtime.reanalyze import (
    ReanalysisError,
    prepare_reanalysis_inputs,
    validate_destination,
    validate_reanalysis_copy,
    validate_recording_source,
)
from backend.runtime.ui_provenance import UIProvenanceError


class ReanalysisTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_id = 'recorded-source'
        self.destination_id = 'recorded-source-reanalysis'
        self.source = self.root / 'runs' / self.source_id
        self.artifacts = self.source / 'flows' / self.source_id
        har_data = self.artifacts / 'har_data'
        har_data.mkdir(parents=True)
        scope = {
            'allowed_domains': ['http://localhost:3000'],
            'allowed_paths_prefix': ['*'],
            'block_production': False,
        }
        demo = {
            'schema_version': 2,
            'source': 'backend-mcp-recorder',
            'flow_name': self.source_id,
            'target_url': 'http://localhost:3000/order',
            'timestamp_start': '2026-09-16T10:00:00Z',
            'timestamp_end': '2026-09-16T10:01:00Z',
            'initial_snapshot': 'initial',
            'final_snapshot': 'final',
            'warnings': [],
            'request_count': 2,
            'workflow_timeline': {
                'ui_states': [
                    {'step': 1, 'elements': ['button "Refund"'],
                     'changes_from_previous': {'appeared': [], 'disappeared': []}},
                    {'step': 2, 'elements': ['text "Pending"'],
                     'changes_from_previous': {'appeared': [], 'disappeared': []}},
                ],
                'network_sequence': [
                    {'method': 'GET', 'url': 'http://localhost:3000/api/order'},
                    {'method': 'POST', 'url': 'http://localhost:3000/api/order/refund'},
                ],
            },
        }
        har = {'log': {'version': '1.2', 'creator': {'name': 'test', 'version': '1'},
                       'entries': [
                           {'request': {'method': 'GET', 'url': 'http://localhost:3000/api/order'},
                            'response': {'status': 200, 'content': {'text': '{}'}}},
                           {'request': {'method': 'POST', 'url': 'http://localhost:3000/api/order/refund'},
                            'response': {'status': 200, 'content': {'text': '{}'}}},
                       ]}}
        capture = {'request_ids': [1, 2], 'missing': [],
                   'unrecoverable_response_bodies': []}
        (self.source / 'scope.json').write_text(json.dumps(scope), encoding='utf-8')
        (self.source / 'recording_validated.marker').write_text('2', encoding='utf-8')
        (self.artifacts / 'demo.json').write_text(json.dumps(demo), encoding='utf-8')
        (self.artifacts / 'recording.har').write_text(json.dumps(har), encoding='utf-8')
        (har_data / 'capture_manifest.json').write_text(json.dumps(capture), encoding='utf-8')
        (self.artifacts / 'state_map.json').write_text('failed source state map', encoding='utf-8')
        mutations = self.source / 'mutations' / self.source_id
        mutations.mkdir(parents=True)
        (mutations / 'draft.py').write_text('raise RuntimeError()', encoding='utf-8')
        reports = self.source / 'reports' / self.source_id
        reports.mkdir(parents=True)
        (reports / 'findings.json').write_text('{"draft": true}', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _hashes(folder):
        return {
            path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in folder.rglob('*') if path.is_file()
        }

    async def test_reanalysis_recording_phase_never_starts_browser(self):
        config = SimpleNamespace(
            cross_flow_inputs=None,
            reanalysis_inputs={'source_run': self.source_id},
            flow_name=self.destination_id,
            run_dir=str(self.root),
        )
        events = []
        with patch('backend.runtime.crew_runner.record', new=AsyncMock()) as browser, patch(
                'backend.runtime.reanalyze.prepare_reanalysis_inputs') as copier:
            await prepare_recording_evidence(
                config, self.root / 'runs' / self.destination_id,
                'unused-mcp.json', {}, events.append)
        browser.assert_not_awaited()
        copier.assert_called_once()
        self.assertIn('Playwright was not started', events[0].technical_detail)

    def test_invalid_source_is_rejected_before_destination_creation(self):
        (self.source / 'recording_validated.marker').write_text('7', encoding='utf-8')
        destination = self.root / 'runs' / self.destination_id
        with self.assertRaisesRegex(ReanalysisError, 'count.*disagree'):
            validate_recording_source(self.root, self.source_id)
        self.assertFalse(destination.exists())

    def test_copy_is_isolated_and_preserves_every_source_hash(self):
        before = self._hashes(self.source)
        inputs = validate_recording_source(self.root, self.source_id)
        destination = validate_destination(
            self.root, self.source_id, self.destination_id)
        destination.mkdir()
        prepare_reanalysis_inputs(
            destination, self.destination_id, inputs, root=self.root)
        self.assertEqual(before, self._hashes(self.source))
        self.assertEqual(
            (self.artifacts / 'demo.json').read_bytes(),
            (destination / 'flows' / self.destination_id / 'demo.json').read_bytes())
        self.assertEqual(
            (self.artifacts / 'recording.har').read_bytes(),
            (destination / 'flows' / self.destination_id / 'recording.har').read_bytes())
        self.assertFalse((destination / 'flows' / self.destination_id / 'state_map.json').exists())
        self.assertFalse((destination / 'mutations').exists())
        self.assertFalse((destination / 'reports').exists())
        manifest = validate_reanalysis_copy(
            destination, self.destination_id, inputs, root=self.root)
        self.assertEqual(manifest['source_run'], self.source_id)
        self.assertEqual(manifest['destination_run'], self.destination_id)
        self.assertEqual(
            manifest['copied_artifacts']['demo.json']['sha256'],
            hashlib.sha256((self.artifacts / 'demo.json').read_bytes()).hexdigest())

    async def test_failed_state_map_validation_cannot_execute_probes(self):
        destination = self.root / 'runs' / self.destination_id
        flow = destination / 'flows' / self.destination_id
        flow.mkdir(parents=True)
        (flow / 'state_map.json').write_text('{}', encoding='utf-8')
        executor = AsyncMock()
        with patch('backend.runtime.probe_executor.execute_run', executor):
            with self.assertRaisesRegex(UIProvenanceError, 'schema_version must be 2'):
                await execute_validated_probes(
                    destination, self.destination_id, self.root, lambda _: None)
        executor.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
