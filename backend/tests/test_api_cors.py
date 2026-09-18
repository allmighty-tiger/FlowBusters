import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api.main import _configured_cors_origins, app


class ApiCorsTests(unittest.TestCase):
    def test_localhost_configuration_includes_equivalent_ipv4_loopback(self):
        with patch.dict(
            'os.environ', {'CORS_ORIGINS': 'http://localhost:3001'}, clear=False
        ):
            origins = _configured_cors_origins()

        self.assertEqual(
            origins,
            ['http://localhost:3001', 'http://127.0.0.1:3001'],
        )

    def test_local_flowbusters_frontend_can_read_reports_api(self):
        with TestClient(app) as client:
            response = client.options(
                '/api/assessments/reports',
                headers={
                    'Origin': 'http://127.0.0.1:3001',
                    'Access-Control-Request-Method': 'GET',
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get('access-control-allow-origin'),
            'http://127.0.0.1:3001',
        )

    def test_unlisted_origin_is_not_granted_cors_access(self):
        with TestClient(app) as client:
            response = client.options(
                '/api/assessments/reports',
                headers={
                    'Origin': 'http://example.invalid',
                    'Access-Control-Request-Method': 'GET',
                },
            )

        self.assertNotIn('access-control-allow-origin', response.headers)


if __name__ == '__main__':
    unittest.main()
