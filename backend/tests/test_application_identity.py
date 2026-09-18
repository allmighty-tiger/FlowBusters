import json
import tempfile
import unittest
from pathlib import Path

import httpx

from backend.runtime.application_identity import (
    ApplicationIdentityError, discover_application_identity, issue_identity,
    validate_identity,
)

class ApplicationIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root / 'execution_keys').mkdir()
        (self.root / 'execution_keys' / 'receipt.key').write_bytes(b'x' * 32)
        self.run = self.root / 'runs' / 'new-run'; self.run.mkdir(parents=True)

    async def selection(self, payload=None, status=200):
        payload = payload if payload is not None else {
            'application_id': 'northstar-market', 'identity_version': '2'}
        transport = httpx.MockTransport(
            lambda request: httpx.Response(status, json=payload, request=request))
        return await discover_application_identity(
            'http://localhost:3000/orders', transport=transport)

    async def test_backend_issues_and_validates_identity(self):
        selection = await self.selection()
        issue_identity(self.run, self.root, 'http://localhost:3000/orders', selection)
        identity = validate_identity(self.run, self.root)
        self.assertEqual(identity['application_id'], 'northstar-market')
        self.assertEqual(identity['identity_version'], '2')
        self.assertEqual(identity['requirements_version'], '2026-09-17')

    async def test_same_origin_is_not_an_identity(self):
        self.assertIsNone(validate_identity(self.run, self.root))
        self.assertIsNone(await self.selection({
            'application_id': 'other-application', 'identity_version': '1'}))

    async def test_missing_or_malformed_endpoint_fails_unbound(self):
        self.assertIsNone(await self.selection({}, status=404))
        self.assertIsNone(await self.selection({'application_id': 'northstar-market'}))
        self.assertIsNone(await self.selection({
            'application_id': 'northstar-market', 'identity_version': '1'}))

    async def test_tampered_manifest_fails_closed(self):
        selection = await self.selection()
        issue_identity(self.run, self.root, 'http://localhost:3000', selection)
        path = self.run / 'application_identity.json'
        data = json.loads(path.read_text(encoding='utf-8')); data['application_id'] = 'other-application'
        path.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaisesRegex(ApplicationIdentityError, 'signature'):
            validate_identity(self.run, self.root)

    def test_unsigned_manifest_fails_closed(self):
        (self.run / 'application_identity.json').write_text(json.dumps({
            'schema_version': 1, 'application_id': 'northstar-market',
            'identity_version': '1', 'requirements_version': '2026-09-17',
            'product': 'Northstar Market', 'target_origin': 'http://localhost:3000',
            'issuer': 'flowbusters_backend',
        }), encoding='utf-8')
        with self.assertRaisesRegex(ApplicationIdentityError, 'invalid'):
            validate_identity(self.run, self.root)

if __name__ == '__main__':
    unittest.main()
