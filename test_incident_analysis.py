"""Tests for the structured incident-analysis contract."""

import json
import unittest

from incident_analysis import (
    AnalysisValidationError,
    build_analysis_prompt,
    parse_analysis_response,
    parse_incident_message,
)
from incident_report import render_incident_report


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
        'summary_evidence': [
            {
                'source_id': 'INCIDENT',
                'observation': 'The alert reports 47 connection timeouts.',
            },
        ],
        'hypotheses': [
            {
                'description': 'The connection pool may be exhausted.',
                'confidence': 0.8,
                'evidence': [
                    {
                        'source_id': 'INCIDENT',
                        'observation': 'The alert reports 47 connection timeouts.',
                    },
                ],
            },
        ],
        'recommended_actions': [
            {
                'action': 'Compare pool waiters with configured pool capacity.',
                'rationale': 'This will establish whether the pool is saturated.',
                'evidence': [
                    {
                        'source_id': 'INCIDENT',
                        'observation': 'The alert reports connection timeouts.',
                    },
                ],
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

    def test_rejects_citation_to_source_not_collected(self):
        payload = sample_analysis()
        payload['summary_evidence'][0]['source_id'] = 'TOOL-1'

        with self.assertRaisesRegex(
            AnalysisValidationError,
            'does not match a collected evidence source',
        ):
            parse_analysis_response(json.dumps(payload), sample_incident())

    def test_accepts_citation_to_successful_tool_source(self):
        payload = sample_analysis()
        payload['summary_evidence'][0]['source_id'] = 'TOOL-1'

        analysis = parse_analysis_response(
            json.dumps(payload),
            sample_incident(),
            available_source_ids=['TOOL-1'],
        )

        self.assertEqual(analysis.summary_evidence[0].source_id, 'TOOL-1')

    def test_renders_review_report_with_evidence_sources(self):
        incident = sample_incident()
        analysis = parse_analysis_response(
            json.dumps(sample_analysis()),
            incident,
        )

        report = render_incident_report(
            analysis,
            incident,
            [
                {
                    'source_id': 'TOOL-1',
                    'tool_name': 'knowledge_search',
                    'arguments': {'query': 'database connections'},
                    'result': {
                        'isError': False,
                        'structuredContent': {
                            'documents': [{'id': 'runbook-payments-db-pool'}],
                        },
                    },
                    'is_error': False,
                },
            ],
        )

        self.assertIn('# Incident Investigation Report', report)
        self.assertIn('[INCIDENT] The alert reports 47 connection timeouts.', report)
        self.assertIn('confidence 80%', report)
        self.assertIn('**Human review required.', report)
        self.assertIn('did not approve or execute any action', report)
        self.assertIn('[TOOL-1] knowledge_search', report)
        self.assertIn('runbook-payments-db-pool', report)

    def test_prompt_contains_schema_and_grounding_rules(self):
        incident = parse_incident_message(json.dumps(sample_incident()))

        prompt = build_analysis_prompt(incident)

        self.assertIn('Return JSON only', prompt)
        self.assertIn('Treat only the incident payload as observed evidence', prompt)
        self.assertIn('"recommended_queries"', prompt)


if __name__ == '__main__':
    unittest.main()
