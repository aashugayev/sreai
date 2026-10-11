"""Tests for the structured incident-analysis contract."""

import json
import unittest

from incident_analysis import (
    AnalysisValidationError,
    build_analysis_prompt,
    parse_analysis_response,
    parse_incident_message,
)


def sample_incident():
    """Return a minimal normalized incident."""
    return {
        'incident_id': 'INC-42',
        'source': 'splunk',
        'title': 'Connection pool exhausted',
        'occurred_at': '2026-10-10T18:42:31-04:00',
        'service': 'payments-api',
        'environment': 'production',
        'severity': 'critical',
        'summary': 'Database connections timed out.',
        'host': 'payments-api-prod-03',
        'splunk_app': 'payments',
        'splunk_owner': 'svc_sre',
        'splunk_results_url': 'https://splunk.example/search/42',
        'evidence': {
            'message': 'Connection timed out',
            'count': '47',
        },
    }


def sample_analysis():
    """Return a valid model response payload."""
    return {
        'incident_id': 'INC-42',
        'service': 'payments-api',
        'environment': 'production',
        'severity': 'critical',
        'summary': 'The service could not obtain database connections.',
        'hypotheses': [
            {
                'description': 'The connection pool may be exhausted.',
                'confidence': 0.8,
                'evidence': ['The alert reports 47 connection timeouts.'],
            },
        ],
        'recommended_queries': [
            'Search connection acquisition latency around the alert time.',
        ],
        'unknowns': [
            'Current pool utilization is not included in the alert.',
        ],
    }


class IncidentAnalysisTests(unittest.TestCase):
    """Verify strict parsing and incident identity preservation."""

    def test_parses_valid_analysis(self):
        incident = sample_incident()

        analysis = parse_analysis_response(
            json.dumps(sample_analysis()),
            incident,
        )

        self.assertEqual(analysis.incident_id, incident['incident_id'])
        self.assertEqual(analysis.hypotheses[0].confidence, 0.8)

    def test_rejects_identity_change(self):
        payload = sample_analysis()
        payload['service'] = 'different-service'

        with self.assertRaisesRegex(
            AnalysisValidationError,
            r'analysis\.service does not match incident\.service',
        ):
            parse_analysis_response(json.dumps(payload), sample_incident())

    def test_rejects_unexpected_field(self):
        payload = sample_analysis()
        payload['root_cause'] = 'unverified'

        with self.assertRaisesRegex(
            AnalysisValidationError,
            'analysis has unexpected fields: root_cause',
        ):
            parse_analysis_response(json.dumps(payload), sample_incident())

    def test_rejects_boolean_confidence(self):
        payload = sample_analysis()
        payload['hypotheses'][0]['confidence'] = True

        with self.assertRaisesRegex(
            AnalysisValidationError,
            'confidence must be a number',
        ):
            parse_analysis_response(json.dumps(payload), sample_incident())

    def test_rejects_hypothesis_without_evidence(self):
        payload = sample_analysis()
        payload['hypotheses'][0]['evidence'] = []

        with self.assertRaisesRegex(
            AnalysisValidationError,
            'evidence must not be empty',
        ):
            parse_analysis_response(json.dumps(payload), sample_incident())

    def test_prompt_contains_schema_and_grounding_rules(self):
        incident = parse_incident_message(json.dumps(sample_incident()))

        prompt = build_analysis_prompt(incident)

        self.assertIn('Return JSON only', prompt)
        self.assertIn('Treat only the incident payload as observed evidence', prompt)
        self.assertIn('"recommended_queries"', prompt)


if __name__ == '__main__':
    unittest.main()
