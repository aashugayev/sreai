"""Tests for bounded local runbook and prior-incident retrieval."""

import json
from pathlib import Path
import tempfile
import unittest

from local_knowledge_tools import build_knowledge_registry


class LocalKnowledgeToolTests(unittest.TestCase):
    """Verify ranked knowledge evidence and explicit tool errors."""

    def test_retrieves_relevant_runbook_and_prior_incident(self):
        result = build_knowledge_registry().call_tool(
            'knowledge_search',
            {'query': 'HikariPool database connection timeout'},
        )

        self.assertFalse(result['isError'], result)
        content = result['structuredContent']
        self.assertEqual(content['total_matches'], 2)
        self.assertEqual(content['returned'], 2)
        self.assertEqual(
            content['documents'][0]['id'],
            'runbook-payments-db-pool',
        )
        self.assertEqual(content['documents'][0]['type'], 'runbook')
        self.assertIn('connection', content['documents'][0]['matched_terms'])
        self.assertLessEqual(len(content['documents'][0]['excerpt']), 500)
        self.assertEqual(
            content['documents'][1]['id'],
            'incident-payments-pool-timeouts-2026-08',
        )

    def test_returns_empty_results_for_unmatched_query(self):
        result = build_knowledge_registry().call_tool(
            'knowledge_search',
            {'query': 'unrelated astronomy topic'},
        )

        self.assertFalse(result['isError'], result)
        self.assertEqual(result['structuredContent']['total_matches'], 0)
        self.assertEqual(result['structuredContent']['documents'], [])

    def test_returns_excerpt_when_match_is_only_in_document_tags(self):
        result = build_knowledge_registry().call_tool(
            'knowledge_search',
            {'query': 'HikariPool'},
        )

        self.assertFalse(result['isError'], result)
        document = result['structuredContent']['documents'][0]
        self.assertEqual(document['id'], 'runbook-payments-db-pool')
        self.assertIn('For connection acquisition timeouts', document['excerpt'])

    def test_rejects_invalid_arguments_and_reports_missing_knowledge_base(self):
        registry = build_knowledge_registry()
        invalid_query = registry.call_tool(
            'knowledge_search',
            {'query': '!!!'},
        )
        invalid_limit = registry.call_tool(
            'knowledge_search',
            {'query': 'payments', 'max_results': 11},
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_base = build_knowledge_registry(Path(temporary_directory)).call_tool(
                'knowledge_search',
                {'query': 'payments'},
            )

        self.assertTrue(invalid_query['isError'])
        self.assertIn('at least 2 characters', invalid_query['content'][0]['text'])
        self.assertTrue(invalid_limit['isError'])
        self.assertIn('between 1 and 10', invalid_limit['content'][0]['text'])
        self.assertTrue(missing_base['isError'])
        self.assertIn('knowledge base not found', missing_base['content'][0]['text'])

    def test_rejects_malformed_knowledge_data(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            data_path = root / 'sample_data' / 'knowledge_base.json'
            data_path.parent.mkdir()
            data_path.write_text(json.dumps([{'id': 'incomplete'}]), encoding='utf-8')

            result = build_knowledge_registry(root).call_tool(
                'knowledge_search',
                {'query': 'payments'},
            )

        self.assertTrue(result['isError'])
        self.assertIn('invalid or duplicate document', result['content'][0]['text'])


if __name__ == '__main__':
    unittest.main()
