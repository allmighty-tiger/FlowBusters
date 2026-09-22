"""Regression: saved executions can be reported without product registries."""
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.runtime.coverage_plans import receipt_probe_origin, prepare_coverage_probes
from backend.runtime.business_requirements import apply_backend_requirements


class ReportWithoutProductConfigTests(unittest.TestCase):
    def test_origin_label_never_reads_deleted_configuration(self):
        with patch.object(Path, 'read_text', side_effect=AssertionError('No registry reads')):
            for source, expected in (
                ('MUTATION_SCRIPT', 'UNKNOWN'),
                ('BACKEND_COVERAGE', 'BACKEND_COVERAGE'),
                ('VERIFICATION_PROBE', 'VERIFICATION_PROBE'),
            ):
                self.assertEqual(receipt_probe_origin({
                    'source': source, 'script': '00_coverage_old.py',
                    'script_source_base64': 'Q09ORklHID0ge30=',
                }), expected)
            self.assertEqual(prepare_coverage_probes('unused', 'unused', 'unused'), [])

    def test_old_owner_approval_is_removed_without_reading_a_registry(self):
        record = {'backend_requirement': {'id': 'old-product-rule'},
                  '_backend_requirement_valid': True}
        report = {'application_identity': {'application_id': 'old-product'},
                  'findings': [record], 'results': [dict(record)]}
        with patch.object(Path, 'read_text', side_effect=AssertionError('No registry reads')):
            apply_backend_requirements(report)
        self.assertNotIn('application_identity', report)
        for row in report['findings'] + report['results']:
            self.assertNotIn('backend_requirement', row)
            self.assertNotIn('_backend_requirement_valid', row)
