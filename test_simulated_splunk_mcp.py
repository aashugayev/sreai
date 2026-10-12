"""Tests for the simulated Splunk MCP request/response boundary."""

import json
import unittest

from simulated_splunk_mcp import (
    SimulatedSplunkMcpClient,
    SimulatedSplunkMcpServer,
    SplunkMcpError,
)


class SimulatedSplunkMcpTests(unittest.TestCase):
    """Verify discovery, deterministic results, and explicit failure shapes."""

    def setUp(self):
        self.server = SimulatedSplunkMcpServer()
        self.client = SimulatedSplunkMcpClient(self.server)

    def test_tools_list_returns_mcp_shaped_definition(self):
        response = self.server.handle_request(
            {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
        )

        self.assertEqual(response['jsonrpc'], '2.0')
        self.assertEqual(response['id'], 1)
        self.assertEqual(response['result']['tools'][0]['name'], 'splunk_search')
        self.assertIn('inputSchema', response['result']['tools'][0])
        self.assertEqual(self.client.list_tools(), response['result'])

    def test_tools_call_returns_deterministic_fixture_results(self):
        result = self.client.call_tool(
            'splunk_search',
            {
                'query': 'HikariPool waiting',
                'service': 'payments-api',
                'environment': 'production',
            },
        )

        self.assertFalse(result['isError'])
        self.assertEqual(result['structuredContent']['total_matches'], 3)
        self.assertEqual(result['structuredContent']['returned'], 3)
        self.assertEqual(
            json.loads(result['content'][0]['text']),
            result['structuredContent'],
        )
        self.assertEqual(
            result['structuredContent']['events'][0]['_time'],
            '2026-10-10T18:43:10.000-04:00',
        )

    def test_tools_call_uses_json_rpc_response_envelope(self):
        response = self.server.handle_request(
            {
                'jsonrpc': '2.0',
                'id': 'search-1',
                'method': 'tools/call',
                'params': {
                    'name': 'splunk_search',
                    'arguments': {
                        'query': 'HikariPool',
                        'service': 'payments-api',
                        'environment': 'production',
                    },
                },
            },
        )

        self.assertEqual(response['jsonrpc'], '2.0')
        self.assertEqual(response['id'], 'search-1')
        self.assertFalse(response['result']['isError'])
        self.assertGreater(response['result']['structuredContent']['returned'], 0)

    def test_returns_an_empty_success_result_for_no_matches(self):
        result = self.client.call_tool(
            'splunk_search',
            {'query': 'no-such-event-should-match'},
        )

        self.assertFalse(result['isError'])
        self.assertEqual(result['structuredContent']['total_matches'], 0)
        self.assertEqual(result['structuredContent']['events'], [])

    def test_returns_explicit_tool_error_for_invalid_arguments(self):
        result = self.client.call_tool(
            'splunk_search',
            {'query': 'pool', 'limit': True},
        )

        self.assertTrue(result['isError'])
        self.assertIn('limit must be an integer', result['content'][0]['text'])

    def test_raises_on_json_rpc_error_for_unknown_tool(self):
        with self.assertRaisesRegex(SplunkMcpError, 'unknown tool'):
            self.client.call_tool('missing_tool', {})


if __name__ == '__main__':
    unittest.main()
