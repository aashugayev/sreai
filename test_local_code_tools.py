"""Tests for read-only source search and Git investigation tools."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from local_code_tools import build_local_code_registry


class LocalCodeToolTests(unittest.TestCase):
    """Verify repository scoping, results, and Git evidence."""

    def test_searches_only_sample_code_and_returns_line_evidence(self):
        result = build_local_code_registry().call_tool(
            'search_code',
            {'query': 'connection pool exhausted'},
        )

        self.assertFalse(result['isError'])
        content = result['structuredContent']
        self.assertGreater(content['total_matches'], 0)
        self.assertEqual(content['matches'][0]['path'], 'sample_code/payments_api/database.py')
        self.assertEqual(content['matches'][0]['line'], 15)
        self.assertIn('connection pool exhausted', content['matches'][0]['snippet'])

    def test_rejects_paths_outside_sample_code(self):
        registry = build_local_code_registry()

        history = registry.call_tool(
            'git_history',
            {'path': '../README.md'},
        )
        blame = registry.call_tool(
            'git_blame',
            {'path': 'sample_code/../../README.md'},
        )

        self.assertTrue(history['isError'])
        self.assertIn('inside sample_code', history['content'][0]['text'])
        self.assertTrue(blame['isError'])
        self.assertIn('inside sample_code', blame['content'][0]['text'])

    def test_reads_history_diff_and_blame_from_a_temporary_repository(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            code_path = root / 'sample_code' / 'payments.py'
            code_path.parent.mkdir()
            code_path.write_text(
                'def status():\n    return "ok"\n',
                encoding='utf-8',
            )
            self._git(root, 'init', '--quiet')
            registry = build_local_code_registry(root)
            untracked_diff = registry.call_tool(
                'git_diff',
                {'path': 'sample_code/payments.py'},
            )
            self.assertTrue(untracked_diff['isError'])
            self.assertIn(
                "Did you forget to 'git add'?",
                untracked_diff['content'][0]['text'],
            )

            self._git(root, 'add', 'sample_code/payments.py')
            self._git(
                root,
                '-c',
                'user.name=Sample Author',
                '-c',
                'user.email=sample@example.test',
                'commit',
                '--quiet',
                '-m',
                'Add payment status',
            )
            first_commit = self._git(root, 'rev-parse', 'HEAD').strip()

            code_path.write_text(
                'def status():\n    return "degraded"\n',
                encoding='utf-8',
            )
            self._git(root, 'add', 'sample_code/payments.py')
            self._git(
                root,
                '-c',
                'user.name=Sample Author',
                '-c',
                'user.email=sample@example.test',
                'commit',
                '--quiet',
                '-m',
                'Report degraded status',
            )
            second_commit = self._git(root, 'rev-parse', 'HEAD').strip()

            history = registry.call_tool(
                'git_history',
                {'path': 'sample_code/payments.py'},
            )
            diff = registry.call_tool(
                'git_diff',
                {
                    'path': 'sample_code/payments.py',
                    'base': first_commit,
                    'head': second_commit,
                },
            )
            blame = registry.call_tool(
                'git_blame',
                {'path': 'sample_code/payments.py', 'start_line': 2},
            )

        self.assertFalse(history['isError'], history)
        self.assertEqual(history['structuredContent']['returned'], 2)
        self.assertEqual(
            history['structuredContent']['commits'][0]['subject'],
            'Report degraded status',
        )
        self.assertFalse(diff['isError'], diff)
        self.assertIn('-    return "ok"', diff['structuredContent']['diff'])
        self.assertIn('+    return "degraded"', diff['structuredContent']['diff'])
        self.assertFalse(blame['isError'], blame)
        self.assertEqual(blame['structuredContent']['returned'], 1)
        self.assertEqual(blame['structuredContent']['lines'][0]['author'], 'Sample Author')
        self.assertEqual(blame['structuredContent']['lines'][0]['line'], 2)

    def test_rejects_unbounded_or_unsafe_arguments(self):
        registry = build_local_code_registry()
        excessive_blame = registry.call_tool(
            'git_blame',
            {
                'path': 'sample_code/payments_api/database.py',
                'max_lines': 51,
            },
        )
        unsafe_revision = registry.call_tool(
            'git_diff',
            {
                'path': 'sample_code/payments_api/database.py',
                'base': '--output=outside.txt',
            },
        )

        self.assertTrue(excessive_blame['isError'])
        self.assertIn('max_lines must be between', excessive_blame['content'][0]['text'])
        self.assertTrue(unsafe_revision['isError'])
        self.assertIn('base must be', unsafe_revision['content'][0]['text'])

    @staticmethod
    def _git(repository_root, *arguments):
        completed = subprocess.run(
            ['git', '-C', str(repository_root)] + list(arguments),
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return completed.stdout


if __name__ == '__main__':
    unittest.main()
