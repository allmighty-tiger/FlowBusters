"""Real Node application + real capture subprocess + signed report loading.

No live Portal, recording browser, repository runs or application data is used.
"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import httpx

from backend.runtime import application_identity
from backend.runtime.application_model import collect, prepare_inputs
from backend.runtime.coverage_plans import prepare_coverage_probes, render_probe
from backend.runtime.crew_runner import execute_validated_probes
from backend.runtime.probe_executor import execute_run, load_receipts, validate_probe_output
from backend.runtime.verification import load_report
from backend.runtime.endpoint_catalog import prepare_endpoint_catalog, load_endpoint_catalog, preflight_endpoints
from backend.tests.recording_fixture import write_recording


INVARIANT = {'operator': 'sum_lte', 'terms': [['order', 'totalReturned']],
             'limit': ['order', 'originalAmount']}
NORTHSTAR = Path(__file__).resolve().parents[3].parent / 'northstar-market'
CHAINS = {'price': ['/api/order/price-adjustment'],
          'refund': ['/api/order/refund/request', '/api/order/refund/complete'],
          'cancel': ['/api/order/cancel']}


@unittest.skipUnless((NORTHSTAR / 'server.js').exists() and shutil.which('node'),
                     'Local Northstar checkout and installed Node are required')
class NorthstarCoverageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fb-northstar-evidence-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = subprocess.Popen(['node', '-e',
            "const {createApp}=require(process.argv[1]); const {app}=createApp({logger:{log(){},error(){}}});"
            "const s=app.listen(0,'127.0.0.1',()=>console.log(s.address().port));",
            str(NORTHSTAR / 'server.js')], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_server)
        self.origin = 'http://127.0.0.1:' + self.server.stdout.readline().strip()
        self.registry = self.root / 'applications.json'
        self.registry.write_text(json.dumps({'schema_version': 1, 'applications': [{
            'application_id': 'northstar-market', 'identity_version': '2',
            'requirements_version': '2026-09-17', 'product': 'Northstar Market',
            'identity_endpoint': '/.well-known/flowbusters-identity',
            'allowed_origins': [self.origin]}]}), encoding='utf-8')
        original = application_identity.load_applications
        self.registry_patch = patch.object(application_identity, 'load_applications',
                                            side_effect=lambda *_: original(self.registry))
        self.registry_patch.start(); self.addCleanup(self.registry_patch.stop)
        self.selection = asyncio.run(application_identity.discover_application_identity(self.origin))
        self.assertEqual(self.selection['identity_version'], '2')

    def stop_server(self):
        self.server.terminate(); self.server.wait(timeout=10)
        self.server.stdout.close(); self.server.stderr.close()

    def recording(self, name, chain):
        run, artifacts = write_recording(self.root, name)
        entries = []
        with httpx.Client(base_url=self.origin) as client:
            client.post('/api/demo/reset', json={}).raise_for_status()
            for method, endpoint in [('GET', '/api/order')] + [('POST', path) for path in chain]:
                response = client.request(method, endpoint, **({'json': {}} if method == 'POST' else {}))
                response.raise_for_status()
                request = {'method': method, 'url': self.origin + endpoint}
                if method == 'POST': request['postData'] = {'mimeType': 'application/json', 'text': '{}'}
                entries.append({'request': request, 'response': {'status': response.status_code,
                    'content': {'text': response.text, 'mimeType': 'application/json'}}})
        for path in run.rglob('*.json'):
            path.write_text(path.read_text(encoding='utf-8').replace('http://localhost:3000', self.origin), encoding='utf-8')
        demo_path = artifacts / 'demo.json'; demo = json.loads(demo_path.read_text())
        demo['request_count'] = len(entries)
        demo['workflow_timeline']['network_sequence'] = [e['request'] for e in entries]
        demo_path.write_text(json.dumps(demo), encoding='utf-8')
        (artifacts / 'recording.har').write_text(json.dumps({'log': {'version': '1.2',
            'creator': {'name': 'integration fixture'}, 'entries': entries}}), encoding='utf-8')
        (artifacts / 'har_data' / 'capture_manifest.json').write_text(json.dumps({
            'request_ids': list(range(1, len(entries) + 1)), 'missing': [], 'unrecoverable_response_bodies': []}), encoding='utf-8')
        (run / 'recording_validated.marker').write_text(str(len(entries)), encoding='utf-8')
        state_path = artifacts / 'state_map.json'; state = json.loads(state_path.read_text())
        state['transitions'][0].update(entries[-1]['request'])
        state['critical_endpoints'] = [entries[-1]['request']]
        state_path.write_text(json.dumps(state), encoding='utf-8')
        application_identity.issue_identity(run, self.root, self.origin, self.selection)
        return run

    def execute_and_load(self, run, expected_count=1):
        asyncio.run(execute_validated_probes(run, run.name, self.root, lambda _: None))
        reports = run / 'reports' / run.name
        receipts = load_receipts(reports, self.root)
        self.assertEqual(len(receipts), expected_count)
        for receipt in receipts.values():
            self.assertEqual(receipt['exit_code'], 0)
            self.assertEqual(validate_probe_output(receipt, enforce_contract=True)['status'], 'valid',
                             validate_probe_output(receipt, enforce_contract=True))
        report = load_report(reports / 'findings.json')
        return report, list(receipts.values())

    def test_four_distinct_chains_real_signed_evidence_and_final_get(self):
        sources = []
        expected = {'price': (130, 130, 0, 0), 'refund': (200, 0, 200, 0),
                    'cancel': (200, 0, 0, 200)}
        for name, chain in CHAINS.items():
            with self.subTest(flow=name):
                run = self.recording(name, chain); sources.append(run)
                report, receipts = self.execute_and_load(run)
                self.assertEqual([f['verification_status'] for f in report['findings']], ['CONFIRMED'])
                self.assertEqual([r['outcome'] for r in report['results']], ['CONFIRMED'])
                self.assertEqual(report['results'][0]['probe_origin'], 'BACKEND_COVERAGE')
                v = receipts[0]['parsed_result']['verification']; order = v['after']['response']['order']
                self.assertEqual(v['after']['request']['method'], 'GET')
                self.assertEqual(v['invariant'], INVARIANT)
                self.assertIs(v['violation']['observed'], True)
                self.assertEqual((order['totalReturned'], order['priceProtection']['adjustmentAmount'],
                    order['refund']['completedAmount'], order['cancellation']['reimbursementAmount']), expected[name])
        before = {p: p.read_bytes() for run in sources for p in run.rglob('*') if p.is_file()}
        inputs = collect(self.root, list(CHAINS), self.origin)
        run = self.root / 'runs' / 'cross'; run.mkdir()
        prepare_inputs(run, run.name, inputs, root=self.root)
        application_identity.issue_identity(run, self.root, self.origin, inputs['application_identity'])
        (run / 'scope.json').write_text((sources[0] / 'scope.json').read_text(), encoding='utf-8')
        state = json.loads((sources[0] / 'flows' / 'price' / 'state_map.json').read_text())
        state.update(flow_name='cross')
        state['transitions'][0]['ui_context']['source_run'] = 'price'
        state['semantic_ui_capture']['ui_state_count'] = 6
        (run / 'flows' / 'cross' / 'state_map.json').write_text(json.dumps(state), encoding='utf-8')
        report, receipts = self.execute_and_load(run)
        self.assertEqual(report['findings'][0]['verification_status'], 'CONFIRMED')
        self.assertEqual(report['results'][0]['outcome'], 'CONFIRMED')
        v = receipts[0]['parsed_result']['verification']; order = v['after']['response']['order']
        self.assertEqual([a['request']['url'].replace(self.origin, '') for a in v['actions']],
                         CHAINS['price'] + CHAINS['refund'])
        self.assertTrue(all(a['request']['body'] == {} for a in v['actions']))
        self.assertEqual((order['totalReturned'], order['priceProtection']['adjustmentAmount'],
            order['refund']['completedAmount'], order['cancellation']['reimbursementAmount']), (130, 30, 100, 0))
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        # Copied raw artifact mutation cannot authorize a coverage plan.
        har = run / 'cross_flow_sources' / 'price' / 'recording.har'
        har.write_text(har.read_text().replace('"status": 200', '"status": 201'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'snapshot manifest'):
            prepare_coverage_probes(run, run.name, self.root)

    def test_normal_controls_exact_predicate_and_wrong_identity(self):
        for name, chain in CHAINS.items():
            with self.subTest(control=name):
                run = self.recording('normal-' + name, chain)
                folder = run / 'mutations' / run.name; folder.mkdir(parents=True)
                script = folder / 'normal.py'
                script.write_text(render_probe(self.origin, '/api/demo/reset', '/api/order',
                    [{'endpoint': path, 'body': {}} for path in chain], INVARIANT, 'Normal amount bound'), encoding='utf-8')
                asyncio.run(execute_run(run, run.name, self.root, lambda _: None))
                report = load_report(run / 'reports' / run.name / 'findings.json')
                self.assertEqual(report['findings'][0]['verification_status'], 'NOT_REPRODUCED')
                self.assertEqual(report['results'][0]['outcome'], 'NOT_REPRODUCED')
        run = self.recording('positive', CHAINS['price'])
        report, _ = self.execute_and_load(run)
        self.assertEqual(report['findings'][0]['verification_status'], 'CONFIRMED')
        path = run / 'application_identity.json'; identity = json.loads(path.read_text())
        identity['application_id'] = 'other-app'  # signature does not authenticate this edit
        path.write_text(json.dumps(identity), encoding='utf-8')
        report = load_report(run / 'reports' / run.name / 'findings.json')
        self.assertEqual(report['findings'][0]['verification_status'], 'NEEDS_REVIEW')
        self.assertEqual(report['results'][0]['outcome'], 'NEEDS_REVIEW')

    def test_signed_other_identity_and_different_predicate_cannot_get_rule(self):
        for wrong_app in (False, True):
            run = self.recording('wrong-' + str(wrong_app), CHAINS['price'])
            if wrong_app:
                registry = json.loads(self.registry.read_text())
                other = {**registry['applications'][0], 'application_id': 'other-app', 'product': 'Other app'}
                registry['applications'].append(other)
                self.registry.write_text(json.dumps(registry), encoding='utf-8')
                (run / 'application_identity.json').unlink()  # isolated fixture only
                application_identity.issue_identity(run, self.root, self.origin, other)
                self.assertEqual(prepare_coverage_probes(run, run.name, self.root), [])
            invariant = deepcopy(INVARIANT)
            if not wrong_app: invariant['terms'] = [['order', 'priceProtection', 'adjustmentAmount']]
            folder = run / 'mutations' / run.name; folder.mkdir(parents=True)
            (folder / 'test.py').write_text(render_probe(self.origin, '/api/demo/reset', '/api/order',
                [{'endpoint': CHAINS['price'][0], 'body': {'adjustmentAmount': 130}}], invariant,
                'Final amount bound'), encoding='utf-8')
            asyncio.run(execute_run(run, run.name, self.root, lambda _: None))
            report = load_report(run / 'reports' / run.name / 'findings.json')
            self.assertEqual(report['findings'][0]['verification_status'], 'NEEDS_REVIEW')
            self.assertEqual(report['results'][0]['outcome'], 'NEEDS_REVIEW')

    def test_incomplete_refund_and_missing_identity_do_not_generate_coverage(self):
        run = self.recording('pending-only', [CHAINS['refund'][0]])
        self.assertEqual(prepare_coverage_probes(run, run.name, self.root), [])
        run = self.recording('unbound', CHAINS['price'])
        (run / 'application_identity.json').unlink()  # temporary fixture only
        self.assertEqual(prepare_coverage_probes(run, run.name, self.root), [])

    def test_contradiction_stops_remaining_probes_and_keeps_terminal_receipt(self):
        run = self.recording('bad-output', CHAINS['price'])
        folder = run / 'mutations' / run.name; folder.mkdir(parents=True)
        code = render_probe(self.origin, '/api/demo/reset', '/api/order',
            [{'endpoint': CHAINS['price'][0], 'body': {'adjustmentAmount': 130}}], INVARIANT, 'Amount bound')
        (folder / '01_bad.py').write_text(code.replace("'observed': total > limit", "'observed': total < limit"), encoding='utf-8')
        (folder / '02_never.py').write_text(code, encoding='utf-8')
        asyncio.run(execute_run(run, run.name, self.root, lambda _: None))
        receipts = load_receipts(run / 'reports' / run.name, self.root)
        self.assertEqual(len(receipts), 1)
        receipt = next(iter(receipts.values()))
        error = validate_probe_output(receipt, enforce_contract=True)
        self.assertEqual(error['category'], 'evidence_contract_error')
        self.assertIn('scenario=primary', error['error'])
        self.assertIn('130', error['error']); self.assertIn('100', error['error'])

    def test_invalid_state_map_blocks_coverage_generation_and_execution(self):
        run = self.recording('bad-analysis', CHAINS['price'])
        state = run / 'flows' / run.name / 'state_map.json'
        state.write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            asyncio.run(execute_validated_probes(run, run.name, self.root, lambda _: None))
        self.assertFalse(list(run.rglob('*.py')))
        self.assertEqual(load_receipts(run / 'reports' / run.name, self.root), {})

    def test_real_legacy_404_is_reviewable_and_amount_variants_share_one_issue(self):
        run = self.recording('amount-variants', CHAINS['price'])
        prepare_coverage_probes(run, run.name, self.root)
        folder = run / 'mutations' / run.name; folder.mkdir(parents=True)
        for name, actions in [('amount', [{'endpoint': CHAINS['price'][0], 'body': {'adjustmentAmount': 999999, 'amount': 999999}}]),
                              ('missing', [{'endpoint': '/api/order/refund', 'body': {}}, {'endpoint': '/api/order/cancel', 'body': {}}])]:
            code = render_probe(self.origin, '/api/demo/reset', '/api/order', actions, INVARIANT, name)
            code = code.replace("'response': response.json()", "'response': response.json() if 'application/json' in response.headers.get('content-type', '') else response.text")
            (folder / (name + '.py')).write_text(code, encoding='utf-8')
        # Legacy receipts have no endpoint catalog; preserve the signed 404 as evidence.
        asyncio.run(execute_run(run, run.name, self.root, lambda _: None))
        reports = run / 'reports' / run.name
        original = {p: p.read_bytes() for p in (reports / 'executions').glob('*.json')}
        report = load_report(reports / 'findings.json')
        self.assertEqual(len(original), 3)
        self.assertEqual(report['summary']['confirmed'], 2)
        self.assertEqual(report['summary']['unique_vulnerabilities'], 1)
        self.assertEqual(report['summary']['needs_review'], 1)
        self.assertEqual(report['summary']['coverage_gaps'], 1)
        self.assertEqual(report['summary']['controls_held'], 0)
        self.assertEqual(report['summary']['probe_origin_counts']['AI_PROBE'], 2)
        self.assertEqual(report['summary']['probe_origin_counts']['BACKEND_COVERAGE'], 1)
        missing = next(r for r in report['results'] if r['script'] == 'missing.py')
        self.assertEqual(missing['outcome'], 'NEEDS_REVIEW')
        self.assertEqual(missing['evidence_validation_status'], 'valid')
        self.assertEqual(original, {p: p.read_bytes() for p in original})

    def test_pending_and_completed_payout_have_distinct_signed_verdicts(self):
        run = self.recording('semantic-coverage', CHAINS['price'])
        folder = run / 'mutations' / run.name
        folder.mkdir(parents=True)
        actions = [{'endpoint': '/api/order/price-adjustment', 'body': {}},
                   {'endpoint': '/api/order/refund/request', 'body': {}}]
        for name, chain in [('pending', actions), ('completed', actions + [
                {'endpoint': '/api/order/refund/complete', 'body': {}}])]:
            (folder / (name + '.py')).write_text(render_probe(
                self.origin, '/api/demo/reset', '/api/order', chain, INVARIANT, name), encoding='utf-8')
        asyncio.run(execute_run(run, run.name, self.root, lambda _: None))
        reports = run / 'reports' / run.name
        raw = {p: p.read_bytes() for p in (reports / 'executions').glob('*.json')}
        report = load_report(reports / 'findings.json')
        results = {r['script']: r for r in report['results']}
        self.assertEqual(results['pending.py']['outcome'], 'NEEDS_REVIEW')
        self.assertEqual(results['completed.py']['outcome'], 'CONFIRMED')
        self.assertEqual(report['summary']['controls_held'], 0)
        self.assertEqual(len(load_receipts(reports, self.root)), 2)
        self.assertEqual(raw, {p: p.read_bytes() for p in raw})

    def test_invalid_later_probe_blocks_first_backend_coverage_probe(self):
        run = self.recording('invalid-plan', CHAINS['price'])
        prepare_coverage_probes(run, run.name, self.root)
        folder = run / 'mutations' / run.name
        folder.mkdir(parents=True)
        (folder / 'later.py').write_text(
            "verification = {'predicate': 'business_rule_must_hold'}", encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Probe plan preflight failed'):
            asyncio.run(execute_run(run, run.name, self.root, lambda _: None))
        self.assertEqual(load_receipts(run / 'reports' / run.name, self.root), {})

    def test_candidate_correction_three_sources_to_signed_execution(self):
        from types import SimpleNamespace
        from copy import deepcopy
        from backend.runtime.application_model import collect, prepare_inputs
        from backend.runtime.candidate_correction import correct_candidates
        sources = [self.recording('src-' + name, CHAINS[name]) for name in ('price', 'refund', 'cancel')]
        run = self.root / 'runs' / 'cross-correction'; run.mkdir()
        prepare_inputs(run, run.name, collect(self.root, [p.name for p in sources], self.origin), root=self.root)
        folder = run / 'mutations' / run.name; folder.mkdir(parents=True)
        actions = [{'endpoint': '/api/order/price-adjustment', 'body': {}},
                   {'endpoint': '/api/order/refund/request', 'body': {}},
                   {'endpoint': '/api/order/refund/complete', 'body': {}}]
        (folder / 'probe.py').write_text(render_probe(self.origin, '/api/demo/reset', '/api/order', actions, INVARIANT, 'combined'), encoding='utf-8')
        c = {'id': 'C1', 'title': 'combined', 'source_runs': ['src-price'],
             'rule': {'source': 'user', 'reference': 'hypothesis only'}, 'scripts': ['probe.py'],
             'actions': [a['endpoint'] for a in actions]}
        original = {'schema_version': 1, 'candidates': [c]}
        path = run / 'flows' / run.name / 'cross_flow_candidates.json'
        path.write_text(json.dumps(original))
        corrected = deepcopy(original); c = corrected['candidates'][0]
        c['source_runs'] = ['src-price', 'src-refund']; c['source_facts'] = []
        for name in c['source_runs']:
            entry = json.loads((run / 'cross_flow_sources' / name / 'recording.har').read_text())['log']['entries'][1]
            c['source_facts'].append({'source_run': name, 'artifact': 'recording.har', 'json_pointer': '/log/entries/1',
                                     'method': entry['request']['method'], 'url': entry['request']['url'], 'status': entry['response']['status']})
        config = SimpleNamespace(run_dir=self.root)
        from backend.runtime.candidate_correction import validate_candidate
        fake = deepcopy(c)
        fake['source_facts'] = fake['source_facts'][:1]
        with self.assertRaisesRegex(ValueError, 'Every declared source'):
            validate_candidate(fake, run, self.root)
        fake = deepcopy(c); fake['source_facts'][1]['status'] = 999
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            validate_candidate(fake, run, self.root)
        with patch('backend.runtime.candidate_correction.propose', return_value=corrected) as agent:
            self.assertEqual(asyncio.run(correct_candidates(config, run, run.name, {}, lambda _: None)), 1)
            self.assertEqual(agent.call_count, 1)
            self.assertIn('two distinct', agent.call_args.args[4]['errors'][0]['message'])
        asyncio.run(execute_run(run, run.name, self.root, lambda _: None))
        report = load_report(run / 'reports' / run.name / 'findings.json')
        self.assertEqual(report['summary']['completed_executions'], 1)
        self.assertEqual(report['summary']['trace_mismatches'], 0)
        self.assertEqual(report['validated_ai_candidates'], 1)
        self.assertEqual(len(load_receipts(run / 'reports' / run.name, self.root)), 1)

    def test_candidate_correction_exhausted_never_executes(self):
        from types import SimpleNamespace
        from backend.runtime.application_model import collect, prepare_inputs
        from backend.runtime.candidate_correction import correct_candidates
        sources = [self.recording('fail-' + name, CHAINS[name]) for name in ('price', 'refund', 'cancel')]
        run = self.root / 'runs' / 'cross-failure'; run.mkdir()
        prepare_inputs(run, run.name, collect(self.root, [p.name for p in sources], self.origin), root=self.root)
        data = {'schema_version': 1, 'candidates': [{'id': 'C1', 'source_runs': ['fail-price']}]}
        (run / 'flows' / run.name / 'cross_flow_candidates.json').write_text(json.dumps(data))
        with patch('backend.runtime.candidate_correction.propose', return_value=data) as agent:
            with self.assertRaisesRegex(ValueError, 'Cross-flow could not be checked'):
                asyncio.run(correct_candidates(SimpleNamespace(run_dir=self.root), run, run.name, {}, lambda _: None))
            self.assertEqual(agent.call_count, 2)
        self.assertEqual(load_receipts(run / 'reports' / run.name, self.root), {})

    def test_endpoint_catalog_guides_generation_and_rejects_guessed_route(self):
        run = self.recording('inventory', CHAINS['price'])
        prepare_endpoint_catalog(run, run.name, self.root)
        catalog = load_endpoint_catalog(run, self.root)
        self.assertEqual(catalog['workflows']['refund'], CHAINS['refund'])
        script = run / 'guess.py'
        code = "BASE = " + repr(self.origin) + "\nREFUND = f'{BASE}/api/order/refund'\nimport httpx\nhttpx.post(REFUND)\n"
        script.write_text(code, encoding='utf-8')
        self.assertIn('Endpoint selection error', preflight_endpoints(script, catalog))
        script.write_text(code.replace('/api/order/refund\'', '/api/order/refund/request\''), encoding='utf-8')
        self.assertIsNone(preflight_endpoints(script, catalog))
        # Dynamic targets are independently enforced by the actual HTTP harness.
        from backend.runtime.probe_executor import execute
        script.write_text(code.replace("httpx.post(REFUND)", "httpx.post(''.join([REFUND]))"), encoding='utf-8')
        receipt = asyncio.run(execute(script, run / 'reports' / run.name, root=self.root, cwd=run))
        self.assertNotEqual(receipt['exit_code'], 0)
        self.assertEqual(receipt['trace'], [])
        self.assertIn('Endpoint selection error', receipt['stderr'])
        path = run / 'endpoint_catalog.json'
        path.write_text(path.read_text().replace('/refund/request', '/refund'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'authentication'):
            load_endpoint_catalog(run, self.root)

    def test_reanalysis_uses_copied_evidence_and_its_own_catalog_and_receipts(self):
        from backend.runtime.reanalyze import validate_recording_source, prepare_reanalysis_inputs
        source = self.recording('source-price', CHAINS['price'])
        original = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
        inputs = validate_recording_source(self.root, source.name)
        run = self.root / 'runs' / 'isolated-reanalysis'; run.mkdir()
        prepare_reanalysis_inputs(run, run.name, inputs, self.root)
        application_identity.issue_identity(run, self.root, self.origin, inputs['application_identity'])
        state = json.loads((source / 'flows' / source.name / 'state_map.json').read_text())
        state['flow_name'] = run.name
        (run / 'flows' / run.name / 'state_map.json').write_text(json.dumps(state), encoding='utf-8')
        report, receipts = self.execute_and_load(run)
        self.assertEqual(report['summary']['unique_vulnerabilities'], 1)
        self.assertEqual(report['results'][0]['probe_origin'], 'BACKEND_COVERAGE')
        self.assertEqual(len(receipts), 1)
        self.assertEqual(original, {p: p.read_bytes() for p in original})
