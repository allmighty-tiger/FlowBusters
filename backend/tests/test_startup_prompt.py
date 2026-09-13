import tempfile
import unittest
import json
import os
from pathlib import Path
from unittest.mock import patch
from backend.runtime.crew_runner import build_system_prompt, prepare_run_dir, CrewConfig

class StartupPromptTests(unittest.TestCase):
    def test_deferred_content_and_recording_handoff(self):
        files=['agents/recorder/charter.md','skills/record-flow/SKILL.md',
            'agents/captain/charter.md','routing.md','agents/analyst/charter.md',
            'skills/analyze-har/SKILL.md','agents/saboteur/charter.md',
            'skills/mutate-flow/SKILL.md','agents/prober/charter.md',
            'skills/probe-flow/SKILL.md','skills/probe-flow/VERIFICATION.md']
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for i,f in enumerate(files):
                p=root/'crew'/f;p.parent.mkdir(parents=True,exist_ok=True)
                p.write_text(f'CONTENT_{i}_END')
            prompt=build_system_prompt(root,'test-flow')
            deferred=(root/'_post_recording_instructions.md').read_text()
            for i in range(2): self.assertIn(f'CONTENT_{i}_END',prompt)
            for i in range(2,len(files)):
                self.assertNotIn(f'CONTENT_{i}_END',prompt)
                self.assertIn(f'CONTENT_{i}_END',deferred)
            for token in ['scope.json','allowed_domains','allowed_paths_prefix',
                'block_production','recording_done.marker','browser_close','synthesize_har.py',
                '_post_recording_instructions.md','Do not end the session']:
                self.assertIn(token,prompt)
            self.assertIn('Resume at ANALYZE',deferred)
    def test_missing_required_instruction_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):build_system_prompt(Path(tmp),'test')

    def test_setup_paths_are_injected_into_run_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crew = root / 'crew-source'
            crew.mkdir()
            (crew / 'config.json').write_text('{}', encoding='utf-8')
            (root / 'scope.json').write_text(json.dumps({
                'allowed_domains': ['http://fixture.test'],
                'allowed_paths_prefix': ['*'],
                'setup_paths': ['/seed'],
            }), encoding='utf-8')
            cfg = CrewConfig(target_url='http://fixture.test', flow_name='run',
                run_dir=str(root), crew_dir=str(crew), mcp_config='mcp.json',
                claude_bin='claude', model='model', api_key='', display='',
                phase_timeout=1, overall_timeout=1)
            with patch.dict(os.environ, {'SETUP_PATHS': '/api/demo/reset,/fixtures/reset'}):
                run = prepare_run_dir(cfg, 'run')
            scope = json.loads((run / 'scope.json').read_text(encoding='utf-8'))
            self.assertEqual(scope['setup_paths'],
                             ['/seed', '/api/demo/reset', '/fixtures/reset'])
