import tempfile
import unittest
from pathlib import Path
from backend.runtime.crew_runner import build_system_prompt

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
