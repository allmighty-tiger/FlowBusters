import tempfile
import threading
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json

import httpx

from backend.runtime.application_identity import (
    discover_application_identity, validate_identity,
)
from backend.runtime.business_requirements import apply_backend_requirements
from backend.runtime.crew_runner import CrewConfig, prepare_run_dir
from backend.runtime.verification import normalize_report


INVARIANT = {
    'operator': 'sum_lte',
    'terms': [['order', 'totalReturned']],
    'limit': ['order', 'originalAmount'],
}
def record(total, violated):
    state = {'order': {'totalReturned': total, 'originalAmount': 100}}
    before = {'sequence': 1, 'status_code': 200, 'complete': True,
               'request': {'method': 'GET', 'endpoint': '/api/order'},
               'response': {'actual': deepcopy(state)}}
    action = {'sequence': 2, 'status_code': 200, 'complete': True,
              'request': {'method': 'POST', 'endpoint': '/api/order/refund/complete'},
              'response': {'actual': deepcopy(state)}}
    after = {**deepcopy(before), 'sequence': 3}
    return {
        'title': 'Agent draft title', 'severity': 'Critical',
        'verification': {
            'predicate': 'business_rule_must_hold',
            'rule': {'source': 'agent_inference'},
            'before': before, 'actions': [action], 'after': after,
            'invariant': deepcopy(INVARIANT),
            'violation': {'observed': violated, 'description': f'{total} > 100'},
        },
    }


class ApplicationIdentityEndToEndSmokeTests(unittest.IsolatedAsyncioTestCase):
    """Temporary-data smoke through issuance, validation, and report normalization."""

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'execution_keys').mkdir()
        (self.root / 'execution_keys' / 'receipt.key').write_bytes(b's' * 32)
        self.run = self.root / 'runs' / 'future-northstar'; self.run.mkdir(parents=True)
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
            'application_id': 'northstar-market', 'identity_version': '2'}, request=request))
        selection = await discover_application_identity(
            'http://localhost:3000', transport=transport)
        config = CrewConfig(
            target_url='http://localhost:3000/order', flow_name='future-northstar',
            run_dir=str(self.root), crew_dir=str(Path(__file__).parents[2] / 'crew'),
            mcp_config='', claude_bin='', model='', api_key='', display='',
            phase_timeout=1, overall_timeout=1, application_identity=selection)
        self.run = prepare_run_dir(config, 'future-northstar')
        self.identity = validate_identity(self.run, self.root)

    def normalize(self, total, violated, identity=None, invariant=None):
        finding, result = record(total, violated), record(total, violated)
        if invariant is not None:
            finding['verification']['invariant'] = deepcopy(invariant)
            result['verification']['invariant'] = deepcopy(invariant)
        report = {'run_timestamp': '2026-09-18T00:00:00Z',
                  'findings': [finding], 'results': [result]}
        apply_backend_requirements(
            report, trusted_run_id='future-northstar',
            trusted_identity=self.identity if identity is None else identity)
        normalized = normalize_report(report)
        return (normalized['findings'][0]['verification_status'],
                normalized['results'][0]['outcome'])

    async def test_signed_violation_confirms_finding_and_execution_result(self):
        self.assertEqual(self.normalize(130, True), ('CONFIRMED', 'CONFIRMED'))

    async def test_signed_non_violation_is_negative_for_both_units(self):
        self.assertEqual(self.normalize(100, False),
                         ('NOT_REPRODUCED', 'NOT_REPRODUCED'))

    async def test_different_predicate_cannot_inherit_rule(self):
        other = {'operator': 'sum_lte', 'terms': [['order', 'currentPrice']],
                 'limit': ['order', 'originalAmount']}
        self.assertEqual(self.normalize(130, True, invariant=other),
                         ('NEEDS_REVIEW', 'NEEDS_REVIEW'))

    async def test_wrong_or_missing_version_cannot_inherit_rule(self):
        for identity in ({}, {**self.identity, 'requirements_version': '2099-01-01'}):
            with self.subTest(identity=identity):
                self.assertEqual(self.normalize(130, True, identity=identity),
                                 ('NEEDS_REVIEW', 'NEEDS_REVIEW'))

    async def test_other_app_on_same_port_cannot_obtain_northstar_identity(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
            'application_id': 'other-app', 'identity_version': '1'}, request=request))
        self.assertIsNone(await discover_application_identity(
            'http://localhost:3000', transport=transport))

    async def test_real_http_handshake_accepts_match_and_rejects_same_origin_mismatch(self):
        class Handler(BaseHTTPRequestHandler):
            payload = {'application_id': 'northstar-market', 'identity_version': '1'}
            def do_GET(self):
                body = json.dumps(self.payload).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body))); self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addAsyncCleanup(self._stop_server, server, thread)
        origin = f'http://127.0.0.1:{server.server_port}'
        registry = self.root / 'applications-smoke.json'
        registry.write_text(json.dumps({'schema_version': 1, 'applications': [{
            'application_id': 'northstar-market', 'identity_version': '1',
            'requirements_version': '2026-09-17', 'product': 'Northstar Market',
            'identity_endpoint': '/.well-known/flowbusters-identity',
            'allowed_origins': [origin],
        }]}), encoding='utf-8')
        self.assertEqual((await discover_application_identity(
            origin, registry_path=registry))['application_id'], 'northstar-market')
        Handler.payload = {'application_id': 'other-app', 'identity_version': '1'}
        self.assertIsNone(await discover_application_identity(origin, registry_path=registry))

    async def _stop_server(self, server, thread):
        server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
