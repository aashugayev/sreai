"""Tests for the MCP-shaped local tool registry and fake Splunk search."""

import json
from pathlib import Path
import tempfile
import unittest

from local_tool_registry import LocalToolRegistry, build_default_registry
from splunk_search_tool import SPLUNK_SEARCH_TOOL, search_events


class LocalToolTests(unittest.TestCase):
    """Verify tool discovery, execution, validation, and search bounds."""

    def test_lists_mcp_shaped_splunk_tool(self):
        result = build_default_registry().list_tools()

        self.assertEqual(len(result['tools']), 1)
        self.assertEqual(result['tools'][0]['name'], 'splunk_search')
        self.assertIn('inputSchema', result['tools'][0])

    def test_calls_tool_and_returns_structured_content(self):
        result = build_default_registry().call_tool(
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
            result['structuredContent']['events'][0]['_time'],
            '2026-10-10T18:43:10.000-04:00',
        )
        self.assertEqual(
            json.loads(result['content'][0]['text']),
            result['structuredContent'],
        )

    def test_filters_by_inclusive_time_range_and_limit(self):
        result = search_events(
            {
                'query': 'HikariPool',
                'service': 'payments-api',
                'environment': 'production',
                'earliest': '2026-10-10T18:39:12-04:00',
                'latest': '2026-10-10T18:42:31-04:00',
                'limit': 2,
            },
        )

        self.assertEqual(result['total_matches'], 3)
        self.assertEqual(result['returned'], 2)
        self.assertGreater(
            result['events'][0]['_time'],
            result['events'][1]['_time'],
        )

    def test_returns_explicit_errors_for_invalid_calls(self):
        registry = build_default_registry()

        unknown = registry.call_tool('missing', {})
        invalid_limit = registry.call_tool(
            'splunk_search',
            {'query': 'pool', 'limit': True},
        )
        unexpected = registry.call_tool(
            'splunk_search',
            {'query': 'pool', 'spl': 'index=* | delete'},
        )

        self.assertTrue(unknown['isError'])
        self.assertIn('unknown tool', unknown['content'][0]['text'])
        self.assertTrue(invalid_limit['isError'])
        self.assertIn('limit must be an integer', invalid_limit['content'][0]['text'])
        self.assertTrue(unexpected['isError'])
        self.assertIn('unexpected arguments: spl', unexpected['content'][0]['text'])

    def test_rejects_inverted_time_range(self):
        result = build_default_registry().call_tool(
            'splunk_search',
            {
                'query': 'pool',
                'earliest': '2026-10-10T19:00:00-04:00',
                'latest': '2026-10-10T18:00:00-04:00',
            },
        )

        self.assertTrue(result['isError'])
        self.assertIn('earliest must not be after latest', result['content'][0]['text'])

    def test_fixture_failure_is_not_returned_as_tool_success(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing = Path(temporary_directory) / 'missing.json'
            with self.assertRaisesRegex(RuntimeError, 'fixture not found'):
                search_events({'query': 'pool'}, events_path=missing)

    def test_registry_rejects_duplicate_tool(self):
        registry = LocalToolRegistry()
        registry.register(SPLUNK_SEARCH_TOOL, search_events)

        with self.assertRaisesRegex(ValueError, 'already registered'):
            registry.register(SPLUNK_SEARCH_TOOL, search_events)


if __name__ == '__main__':
    unittest.main()
