"""Tests for the Ollama tool-calling incident-analysis loop."""

import json
from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import MagicMock, patch

from incident_analysis import parse_incident_message
from local_tool_registry import build_default_registry
from ollama_client import (
    AgentLoopError,
    generate_chat_message,
    generate_response,
    main,
    run_agent_loop,
    to_ollama_tool_definitions,
)


def sample_incident():
    """Return a normalized incident suitable for the agent loop."""
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
    """Return a valid final analysis response."""
    return json.dumps({
        'incident_id': 'INC-42',
        'service': 'payments-api',
        'environment': 'production',
        'severity': 'critical',
        'summary': 'The service could not obtain database connections.',
        'summary_evidence': [
            {
                'source_id': 'INCIDENT',
                'observation': 'Database connection requests timed out.',
            },
        ],
        'hypotheses': [
            {
                'description': 'The connection pool may be exhausted.',
                'confidence': 0.8,
                'evidence': [
                    {
                        'source_id': 'INCIDENT',
                        'observation': 'Database connection requests timed out.',
                    },
                ],
            },
        ],
        'recommended_actions': [
            {
                'action': 'Check pool utilization.',
                'rationale': 'The alert reports connection timeouts.',
                'evidence': [
                    {
                        'source_id': 'INCIDENT',
                        'observation': 'Database connection requests timed out.',
                    },
                ],
            },
        ],
        'recommended_queries': ['Check pool utilization around the alert.'],
        'unknowns': ['The cause of the pool exhaustion is unknown.'],
    })


class AgentLoopTests(unittest.TestCase):
    """Verify bounded tool execution and model handoff."""

    def setUp(self):
        self.incident = parse_incident_message(json.dumps(sample_incident()))
        self.options = {
            'host': 'http://localhost:11434',
            'model': 'test-model',
            'timeoutseconds': 30,
        }

    def test_executes_tool_and_returns_final_model_response(self):
        tool_message = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'function': {
                        'name': 'splunk_search',
                        'arguments': {
                            'query': 'HikariPool waiting',
                            'service': 'payments-api',
                            'environment': 'production',
                        },
                    },
                },
            ],
        }
        with patch(
            'ollama_client.generate_chat_message',
            side_effect=[
                tool_message,
                {'role': 'assistant', 'content': 'The related fixture events were checked.'},
                {'role': 'assistant', 'content': sample_analysis()},
            ],
        ) as generate:
            response = run_agent_loop(self.incident, self.options)

        self.assertEqual(response, sample_analysis())
        self.assertEqual(generate.call_count, 3)
        second_messages = generate.call_args_list[1].args[0]
        tool_result_message = next(
            item for item in second_messages if item.get('role') == 'tool'
        )
        tool_result = json.loads(tool_result_message['content'])
        self.assertFalse(tool_result['isError'])
        self.assertEqual(tool_result['source_id'], 'TOOL-1')
        self.assertEqual(tool_result['structuredContent']['total_matches'], 3)
        self.assertIn('tools', generate.call_args_list[0].kwargs)
        self.assertNotIn('response_format', generate.call_args_list[0].kwargs)
        self.assertNotIn('tools', generate.call_args.kwargs)
        self.assertIn('response_format', generate.call_args.kwargs)

    def test_assigns_unique_sources_to_multiple_tool_calls(self):
        tool_message = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'function': {
                        'name': 'splunk_search',
                        'arguments': {
                            'query': 'HikariPool waiting',
                            'service': 'payments-api',
                            'environment': 'production',
                        },
                    },
                },
                {
                    'function': {
                        'name': 'knowledge_search',
                        'arguments': {'query': 'payments database pool'},
                    },
                },
            ],
        }
        evidence_sources = []
        with patch(
            'ollama_client.generate_chat_message',
            side_effect=[
                tool_message,
                {'role': 'assistant', 'content': 'Both sources were checked.'},
                {'role': 'assistant', 'content': sample_analysis()},
            ],
        ):
            run_agent_loop(
                self.incident,
                self.options,
                max_tool_calls=2,
                evidence_sources=evidence_sources,
            )

        self.assertEqual(
            [source['source_id'] for source in evidence_sources],
            ['TOOL-1', 'TOOL-2'],
        )

    def test_stops_before_exceeding_tool_call_limit(self):
        tool_message = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'function': {
                        'name': 'splunk_search',
                        'arguments': {'query': 'pool'},
                    },
                },
            ],
        }
        with patch(
            'ollama_client.generate_chat_message',
            side_effect=[tool_message, tool_message],
        ) as generate:
            with self.assertRaisesRegex(AgentLoopError, 'maximum of 1'):
                run_agent_loop(
                    self.incident,
                    self.options,
                    max_tool_calls=1,
                )

        self.assertEqual(generate.call_count, 2)

    def test_executes_local_code_search_tool(self):
        tool_message = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'function': {
                        'name': 'search_code',
                        'arguments': {'query': 'connection pool exhausted'},
                    },
                },
            ],
        }
        with patch(
            'ollama_client.generate_chat_message',
            side_effect=[
                tool_message,
                {'role': 'assistant', 'content': 'The sample service has a pool-exhaustion path.'},
                {'role': 'assistant', 'content': sample_analysis()},
            ],
        ) as generate:
            run_agent_loop(self.incident, self.options)

        tool_result_message = next(
            item
            for item in generate.call_args_list[1].args[0]
            if item.get('role') == 'tool'
        )
        tool_result = json.loads(tool_result_message['content'])
        self.assertFalse(tool_result['isError'])
        self.assertGreater(
            tool_result['structuredContent']['total_matches'],
            0,
        )

    def test_executes_local_knowledge_search_tool(self):
        tool_message = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'function': {
                        'name': 'knowledge_search',
                        'arguments': {
                            'query': 'payments database connection pool timeout',
                        },
                    },
                },
            ],
        }
        with patch(
            'ollama_client.generate_chat_message',
            side_effect=[
                tool_message,
                {'role': 'assistant', 'content': 'The runbook and prior incident were retrieved.'},
                {'role': 'assistant', 'content': sample_analysis()},
            ],
        ) as generate:
            evidence_sources = []
            run_agent_loop(
                self.incident,
                self.options,
                evidence_sources=evidence_sources,
            )

        self.assertEqual(evidence_sources[0]['source_id'], 'TOOL-1')
        self.assertFalse(evidence_sources[0]['is_error'])
        first_request = generate.call_args_list[0]
        self.assertIn(
            'knowledge_search',
            [tool['function']['name'] for tool in first_request.kwargs['tools']],
        )
        tool_result_message = next(
            item
            for item in generate.call_args_list[1].args[0]
            if item.get('role') == 'tool'
        )
        tool_result = json.loads(tool_result_message['content'])
        self.assertFalse(tool_result['isError'])
        self.assertEqual(
            tool_result['structuredContent']['documents'][0]['id'],
            'runbook-payments-db-pool',
        )

    def test_analysis_main_prints_the_cited_report(self):
        evidence_source = {
            'source_id': 'TOOL-1',
            'tool_name': 'knowledge_search',
            'arguments': {'query': 'payments database'},
            'result': {
                'isError': False,
                'structuredContent': {
                    'documents': [{'id': 'runbook-payments-db-pool'}],
                },
            },
            'is_error': False,
        }

        def run_loop(*args, evidence_sources, **kwargs):
            evidence_sources.append(evidence_source)
            return sample_analysis()

        output = StringIO()
        with patch('ollama_client.load_options', return_value=self.options):
            with patch('ollama_client.run_agent_loop', side_effect=run_loop):
                with redirect_stdout(output):
                    main(
                        prompt=json.dumps(sample_incident()),
                        analyze_incident=True,
                    )

        report = output.getvalue()
        self.assertIn('# Incident Investigation Report', report)
        self.assertIn('Proposed remediation', report)
        self.assertIn('Human review required', report)
        self.assertIn('[TOOL-1] knowledge_search', report)

    def test_rejects_malformed_tool_arguments(self):
        tool_message = {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {
                    'function': {
                        'name': 'splunk_search',
                        'arguments': '{"query": "pool"}',
                    },
                },
            ],
        }
        with patch(
            'ollama_client.generate_chat_message',
            return_value=tool_message,
        ):
            with self.assertRaisesRegex(
                AgentLoopError,
                'non-object arguments',
            ):
                run_agent_loop(self.incident, self.options)

    def test_caps_model_request_timeout_to_agent_deadline(self):
        with patch('ollama_client.time.monotonic', return_value=10):
            with patch(
                'ollama_client.generate_chat_message',
                side_effect=[
                    {'role': 'assistant', 'content': 'Ready for final output.'},
                    {'role': 'assistant', 'content': sample_analysis()},
                ],
            ) as generate:
                run_agent_loop(
                    self.incident,
                    self.options,
                    timeout_seconds=2,
                )

        self.assertEqual(generate.call_args.kwargs['timeout'], 2)

    def test_sends_tools_and_chat_history_to_ollama(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            'message': {'role': 'assistant', 'content': 'done'},
        }).encode('utf-8')
        mcp_tools = build_default_registry().list_tools()['tools']
        tools = to_ollama_tool_definitions(mcp_tools)
        messages = [{'role': 'user', 'content': 'Search for pool timeouts.'}]

        with patch('ollama_client.urllib.request.urlopen', return_value=response) as urlopen:
            message = generate_chat_message(
                messages,
                self.options,
                tools=tools,
                response_format={'type': 'object'},
                timeout=3,
            )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(message['content'], 'done')
        self.assertEqual(payload['messages'], messages)
        self.assertEqual(payload['tools'], tools)
        self.assertEqual(payload['format'], {'type': 'object'})
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 3)
        self.assertEqual(
            payload['tools'][0]['function']['parameters'],
            mcp_tools[0]['inputSchema'],
        )

    def test_preserves_plain_prompt_response(self):
        with patch(
            'ollama_client.generate_chat_message',
            return_value={'role': 'assistant', 'content': 'A short reply.'},
        ) as generate:
            response = generate_response('Explain this error.', self.options)

        self.assertEqual(response, 'A short reply.')
        self.assertEqual(
            generate.call_args.args[0],
            [{'role': 'user', 'content': 'Explain this error.'}],
        )

    def test_rejects_nonpositive_limits(self):
        with self.assertRaisesRegex(AgentLoopError, 'at least 1'):
            run_agent_loop(
                self.incident,
                self.options,
                max_tool_calls=0,
            )
        with self.assertRaisesRegex(AgentLoopError, 'greater than 0'):
            run_agent_loop(
                self.incident,
                self.options,
                timeout_seconds=0,
            )


if __name__ == '__main__':
    unittest.main()
