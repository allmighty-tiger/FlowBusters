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
            'skills/analyze-har/SKILL.md','skills/analyze-har/OBSERVED_UI_RULES.md',
            'agents/saboteur/charter.md',
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
            self.assertIn('explicit_ui_text + explicit_visible_ui_text + demo.json', deferred)
            self.assertIn('ui_element_transition + observed_ui_affordance + demo.json', deferred)
            self.assertIn('api_field_transition + api_state_fact + recording.har', deferred)
            self.assertIn('A visible element at one sampled step is explicit_ui_text', deferred)
            self.assertIn('Check each rule independently', deferred)
            self.assertIn('never pad them with unrelated UI facts', deferred)

    def test_actual_rule_contract_is_embedded_in_analysis_instructions(self):
        import shutil
        source = Path(__file__).resolve().parents[2] / 'crew'
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            shutil.copytree(source, run / 'crew')
            build_system_prompt(run, 'test-flow')
            continuation = (run / '_post_recording_instructions.md').read_text(encoding='utf-8')
            contract = (source / 'skills/analyze-har/OBSERVED_UI_RULES.md').read_text(encoding='utf-8')
            self.assertEqual(continuation.count(contract), 1)
            self.assertLess(continuation.index(contract), continuation.index('# agents/saboteur/charter.md'))
            self.assertIn('inside EACH observed_ui_rules item', continuation)
            self.assertIn('Initial GET/HEAD', continuation)
            self.assertIn('0 < before_step < after_step', continuation)
            self.assertIn('relabel an observed baseline read as `inferred`', continuation)
            self.assertIn('not the\ndefinition of that rule', continuation)
            self.assertIn('an API-only\n  rule does not belong in `observed_ui_rules`', continuation)
            self.assertIn('Agent inference (unverified):', continuation)
            self.assertIn('"id": "UIR-002-F2"', continuation)
            self.assertIn('/workflow_timeline/ui_states/4/elements/65', continuation)
            self.assertIn('changes_from_previous/appeared/8` is INVALID', continuation)
            self.assertIn('at most TWO dedicated Analyst correction attempts', continuation)
            self.assertIn('validator_error.json', continuation)
            (run / 'crew/skills/analyze-har/OBSERVED_UI_RULES.md').unlink()
            with self.assertRaises(FileNotFoundError):
                build_system_prompt(run, 'test-flow')
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
