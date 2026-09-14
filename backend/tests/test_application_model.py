import json
import tempfile
import unittest
from pathlib import Path
from backend.runtime.application_model import catalog, collect, origin, prepare_inputs, source


class ApplicationModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ['refund', 'cancel']:
            for folder, filename, data in [
                ('reports', 'findings.json', {'target_url': 'http://localhost:3000'}),
                ('flows', 'state_map.json', {'transitions': [{'name': name}]}),
                ('flows', 'recording.har', {'log': {'entries': [{'request': {'method': 'POST'}}]}}),
                ('flows', 'demo.json', {'workflow_timeline': []}),
            ]:
                path = self.root / 'runs' / name / folder / name / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data), encoding='utf-8')

    def test_catalog(self):
        apps = catalog(self.root)
        self.assertEqual(len(apps), 1)
        self.assertEqual(len(apps[0]['runs']), 2)
        self.assertTrue(apps[0]['runs'][0]['eligible'])

    def test_origin(self):
        self.assertEqual(origin('https://EXAMPLE.com:443/path'), 'https://example.com')
        self.assertEqual(origin('http://[::1]:3000'), 'http://[::1]:3000')
        with self.assertRaises(ValueError):
            origin('http://user:password@example.com')

    def test_reject_bad_selection(self):
        for names in [['refund'], ['refund', 'refund'], ['../refund', 'cancel']]:
            with self.assertRaises(ValueError):
                collect(self.root, names, 'http://localhost:3000')
        with self.assertRaises(ValueError):
            collect(self.root, ['refund', 'cancel'], 'http://localhost:3001')

    def test_snapshot_provenance_and_source_preservation(self):
        inputs = collect(self.root, ['refund', 'cancel'], 'http://localhost:3000')
        dest = self.root / 'runs' / 'cross-test'
        prepare_inputs(dest, 'cross-test', inputs)
        manifest = json.loads((dest / 'cross_flow_inputs.json').read_text())
        self.assertEqual(len(manifest['sources']), 2)
        self.assertEqual(len(manifest['sources'][0]['sha256']), 64)
        har = json.loads((dest / 'flows/cross-test/recording.har').read_text())
        self.assertEqual([e['_source_run'] for e in har['log']['entries']], ['refund', 'cancel'])
        self.assertEqual(len(catalog(self.root)[0]['runs']), 2)
        with self.assertRaises(ValueError):
            collect(self.root, ['cross-test', 'refund'], 'http://localhost:3000')

    def test_corrupt_source_not_catalogued(self):
        _, artifact = source(self.root, 'refund')
        artifact('flows', 'state_map.json').write_text('broken')
        self.assertEqual(len(catalog(self.root)[0]['runs']), 1)


if __name__ == '__main__':
    unittest.main()
