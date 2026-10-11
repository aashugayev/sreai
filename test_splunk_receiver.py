"""Tests for Splunk webhook normalization."""

import json
from pathlib import Path
import unittest

from splunk_receiver import WebhookValidationError, normalize_splunk_alert


SAMPLE_PATH = Path(__file__).parent / 'sample_data' / 'splunk_alert.json'


class NormalizeSplunkAlertTests(unittest.TestCase):
    """Verify the raw Splunk-to-incident contract."""

    def setUp(self):
        self.payload = json.loads(SAMPLE_PATH.read_text(encoding='utf-8'))

    def test_normalizes_sample_alert(self):
        signal = normalize_splunk_alert(self.payload)

        self.assertEqual(signal.source, 'splunk')
        self.assertEqual(signal.service, 'payments-api')
        self.assertEqual(signal.environment, 'production')
        self.assertEqual(signal.severity, 'critical')
        self.assertEqual(signal.evidence['count'], '47')

    def test_rejects_missing_saved_search_field(self):
        del self.payload['result']['service']

        with self.assertRaisesRegex(
            WebhookValidationError,
            r'webhook\.result\.service must be a non-empty string',
        ):
            normalize_splunk_alert(self.payload)

    def test_rejects_non_object_result(self):
        self.payload['result'] = []

        with self.assertRaisesRegex(
            WebhookValidationError,
            'webhook.result must be a JSON object',
        ):
            normalize_splunk_alert(self.payload)


if __name__ == '__main__':
    unittest.main()
