"""Read-only code search and Git evidence tools for the sample repository."""

from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess

from local_tool_registry import (
    LocalToolRegistry,
    ToolExecutionError,
    ToolInputError,
)


REPOSITORY_ROOT = Path(__file__).resolve().parent
SAMPLE_CODE_PATH = 'sample_code'
SEARCHABLE_SUFFIXES = {'.py', '.md', '.toml', '.yaml', '.yml', '.json', '.ini'}
MAX_SEARCH_FILES = 500
MAX_SEARCH_FILE_BYTES = 1_000_000
MAX_SEARCH_SNIPPET_LENGTH = 500
MAX_DIFF_LENGTH = 12_000
MAX_TOOL_OUTPUT_LINES = 50


SEARCH_CODE_TOOL = {
    'name': 'search_code',
    'description': (
        'Search literal text, case-insensitively, in files under sample_code. '
        'Returns matching file paths, line numbers, and bounded snippets.'
    ),
    'inputSchema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'query': {
                'type': 'string',
                'minLength': 1,
                'maxLength': 200,
            },
            'max_results': {
                'type': 'integer',
                'minimum': 1,
                'maximum': 50,
                'default': 20,
            },
        },
        'required': ['query'],
    },
}

GIT_HISTORY_TOOL = {
    'name': 'git_history',
    'description': (
        'Read recent commit hashes, authors, timestamps, and subjects for a '
        'path under sample_code. No remote fetches are performed.'
    ),
    'inputSchema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'path': {
                'type': 'string',
                'minLength': 1,
                'description': 'Repository-relative path under sample_code.',
            },
            'limit': {
                'type': 'integer',
                'minimum': 1,
                'maximum': 25,
                'default': 10,
            },
        },
    },
}

GIT_DIFF_TOOL = {
    'name': 'git_diff',
    'description': (
        'Show a bounded, read-only diff for one path under sample_code between '
        'HEAD~1 and HEAD by default. Revisions must be HEAD, HEAD~N, or a '
        'commit hash.'
    ),
    'inputSchema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'path': {
                'type': 'string',
                'minLength': 1,
                'description': 'Repository-relative file path under sample_code.',
            },
            'base': {
                'type': 'string',
                'default': 'HEAD~1',
                'description': 'HEAD, HEAD~N, or a full/abbreviated commit hash.',
            },
            'head': {
                'type': 'string',
                'default': 'HEAD',
                'description': 'HEAD, HEAD~N, or a full/abbreviated commit hash.',
            },
        },
        'required': ['path'],
    },
}

GIT_BLAME_TOOL = {
    'name': 'git_blame',
    'description': (
        'Return bounded line-level Git author and commit evidence for a tracked '
        'file under sample_code. Blame is attribution evidence, not fault.'
    ),
    'inputSchema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'path': {
                'type': 'string',
                'minLength': 1,
                'description': 'Repository-relative file path under sample_code.',
            },
            'start_line': {
                'type': 'integer',
                'minimum': 1,
                'default': 1,
            },
            'max_lines': {
                'type': 'integer',
                'minimum': 1,
                'maximum': MAX_TOOL_OUTPUT_LINES,
                'default': 20,
            },
        },
        'required': ['path'],
    },
}


def build_local_code_registry(repository_root=REPOSITORY_ROOT):
    """Create a registry of bounded, read-only local code-investigation tools."""
    root = Path(repository_root).resolve()
    registry = LocalToolRegistry()
    registry.register(
        SEARCH_CODE_TOOL,
        lambda arguments: search_code(arguments, root),
    )
    registry.register(
        GIT_HISTORY_TOOL,
        lambda arguments: git_history(arguments, root),
    )
    registry.register(
        GIT_DIFF_TOOL,
        lambda arguments: git_diff(arguments, root),
    )
    registry.register(
        GIT_BLAME_TOOL,
        lambda arguments: git_blame(arguments, root),
    )
    return registry


def search_code(arguments, repository_root=REPOSITORY_ROOT):
    """Search bounded text files in sample_code for one literal query."""
    _reject_unexpected_arguments(arguments, {'query', 'max_results'})
    query = _require_string(arguments, 'query', required=True)
    if len(query) > 200:
        raise ToolInputError('query must not exceed 200 characters')
    max_results = _require_integer(arguments, 'max_results', 20, 1, 50)
    sample_root = _sample_root(repository_root)

    files = []
    for path in sample_root.rglob('*'):
        if path.is_file() and path.suffix.casefold() in SEARCHABLE_SUFFIXES:
            try:
                path.resolve().relative_to(sample_root)
            except ValueError:
                continue
            files.append(path)
    files.sort()
    if len(files) > MAX_SEARCH_FILES:
        raise ToolExecutionError(
            'sample_code contains more than '
            + str(MAX_SEARCH_FILES)
            + ' searchable files'
        )

    matches = []
    folded_query = query.casefold()
    for path in files:
        try:
            if path.stat().st_size > MAX_SEARCH_FILE_BYTES:
                raise ToolExecutionError(
                    'searchable file exceeds the 1 MB limit: '
                    + _relative_path(repository_root, path)
                )
            lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        except OSError as error:
            raise ToolExecutionError(
                'could not read ' + _relative_path(repository_root, path)
                + ': ' + str(error)
            ) from error

        for line_number, line in enumerate(lines, start=1):
            if folded_query not in line.casefold():
                continue
            matches.append(
                {
                    'path': _relative_path(repository_root, path),
                    'line': line_number,
                    'snippet': line[:MAX_SEARCH_SNIPPET_LENGTH],
                },
            )

    return {
        'query': query,
        'total_matches': len(matches),
        'returned': min(len(matches), max_results),
        'matches': matches[:max_results],
    }


def git_history(arguments, repository_root=REPOSITORY_ROOT):
    """Return recent committed history scoped to sample_code."""
    _reject_unexpected_arguments(arguments, {'path', 'limit'})
    limit = _require_integer(arguments, 'limit', 10, 1, 25)
    path = _validated_sample_path(
        repository_root,
        arguments.get('path', SAMPLE_CODE_PATH),
        require_file=False,
    )
    output = _run_git(
        repository_root,
        [
            'log',
            '-n',
            str(limit),
            '--format=%H%x1f%an%x1f%aI%x1f%s',
            '--',
            path,
        ],
    )

    commits = []
    for line in output.splitlines():
        fields = line.split('\x1f', 3)
        if len(fields) != 4:
            raise ToolExecutionError('git log returned a malformed commit record')
        commits.append(
            {
                'commit': fields[0],
                'author': fields[1],
                'authored_at': fields[2],
                'subject': fields[3],
            },
        )
    return {'path': path, 'returned': len(commits), 'commits': commits}


def git_diff(arguments, repository_root=REPOSITORY_ROOT):
    """Return a bounded diff for one sample-code file and safe revisions."""
    _reject_unexpected_arguments(arguments, {'path', 'base', 'head'})
    path = _validated_sample_path(
        repository_root,
        arguments.get('path'),
        require_file=True,
    )
    base = _require_revision(arguments.get('base', 'HEAD~1'), 'base')
    head = _require_revision(arguments.get('head', 'HEAD'), 'head')
    _require_tracked_file(repository_root, path)
    diff = _run_git(
        repository_root,
        [
            'diff',
            '--no-ext-diff',
            '--no-textconv',
            '--no-color',
            '--unified=2',
            base,
            head,
            '--',
            path,
        ],
    )
    truncated = len(diff) > MAX_DIFF_LENGTH
    if truncated:
        diff = diff[:MAX_DIFF_LENGTH]
    return {
        'path': path,
        'base': base,
        'head': head,
        'diff': diff,
        'truncated': truncated,
    }


def git_blame(arguments, repository_root=REPOSITORY_ROOT):
    """Return bounded line attribution for one tracked sample-code file."""
    _reject_unexpected_arguments(arguments, {'path', 'start_line', 'max_lines'})
    path = _validated_sample_path(
        repository_root,
        arguments.get('path'),
        require_file=True,
    )
    start_line = _require_integer(arguments, 'start_line', 1, 1, 1_000_000)
    max_lines = _require_integer(
        arguments,
        'max_lines',
        20,
        1,
        MAX_TOOL_OUTPUT_LINES,
    )
    end_line = start_line + max_lines - 1
    output = _run_git(
        repository_root,
        [
            'blame',
            '--line-porcelain',
            '-L',
            str(start_line) + ',' + str(end_line),
            'HEAD',
            '--',
            path,
        ],
    )

    lines = _parse_blame(output)
    return {
        'path': path,
        'start_line': start_line,
        'returned': len(lines),
        'lines': lines,
    }


def _parse_blame(output):
    lines = []
    record = None
    for line in output.splitlines():
        if line.startswith('\t') and record is not None:
            authored_at = record.get('author_time')
            lines.append(
                {
                    'line': record['line'],
                    'commit': record['commit'],
                    'author': record.get('author', 'unknown'),
                    'authored_at': (
                        datetime.fromtimestamp(
                            int(authored_at),
                            tz=timezone.utc,
                        ).isoformat()
                        if authored_at is not None
                        else None
                    ),
                    'content': line[1:][:MAX_SEARCH_SNIPPET_LENGTH],
                },
            )
            record = None
            continue
        if record is None:
            header = re.match(
                r'^([0-9a-f]{40}) \d+ (\d+)(?: \d+)?$',
                line,
            )
            if header:
                record = {
                    'commit': header.group(1),
                    'line': int(header.group(2)),
                }
            continue
        if line.startswith('author '):
            record['author'] = line[len('author '):]
        elif line.startswith('author-time '):
            record['author_time'] = line[len('author-time '):]
    return lines


def _run_git(repository_root, arguments):
    try:
        completed = subprocess.run(
            ['git', '-C', str(Path(repository_root).resolve())] + arguments,
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ToolExecutionError('could not run git: ' + str(error)) from error

    if completed.returncode != 0:
        message = completed.stderr.strip() or 'git command failed'
        raise ToolExecutionError(message[:1000])
    return completed.stdout


def _require_tracked_file(repository_root, path):
    _run_git(
        repository_root,
        ['ls-files', '--error-unmatch', '--', path],
    )


def _sample_root(repository_root):
    root = Path(repository_root).resolve()
    sample_root = (root / SAMPLE_CODE_PATH).resolve()
    try:
        sample_root.relative_to(root)
    except ValueError as error:
        raise ToolExecutionError('sample_code must be inside the repository') from error
    if not sample_root.is_dir():
        raise ToolExecutionError('sample_code directory not found: ' + str(sample_root))
    return sample_root


def _validated_sample_path(repository_root, value, require_file):
    if not isinstance(value, str) or not value.strip():
        raise ToolInputError('path must be a non-empty sample_code path')
    normalized = value.strip().replace('\\', '/')
    parts = normalized.split('/')
    if (
        normalized.startswith('/')
        or any(part in ('', '.', '..') for part in parts)
        or parts[0] != SAMPLE_CODE_PATH
    ):
        raise ToolInputError('path must stay inside sample_code')

    root = Path(repository_root).resolve()
    sample_root = _sample_root(root)
    target = (root.joinpath(*parts)).resolve()
    try:
        target.relative_to(sample_root)
    except ValueError as error:
        raise ToolInputError('path must stay inside sample_code') from error
    if not target.exists():
        raise ToolInputError('sample_code path does not exist: ' + normalized)
    if require_file and not target.is_file():
        raise ToolInputError('path must identify a file')
    return Path(*parts).as_posix()


def _relative_path(repository_root, path):
    return Path(path).resolve().relative_to(Path(repository_root).resolve()).as_posix()


def _require_string(arguments, name, required=False):
    value = arguments.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolInputError(name + ' must be a non-empty string')
    return value.strip()


def _require_integer(arguments, name, default, minimum, maximum):
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolInputError(name + ' must be an integer')
    if value < minimum or value > maximum:
        raise ToolInputError(
            name + ' must be between ' + str(minimum) + ' and ' + str(maximum)
        )
    return value


def _require_revision(value, name):
    if not isinstance(value, str) or not re.fullmatch(
        r'(?:HEAD(?:~[0-9]+)?|[0-9a-fA-F]{7,40})',
        value,
    ):
        raise ToolInputError(
            name + ' must be HEAD, HEAD~N, or a commit hash'
        )
    return value


def _reject_unexpected_arguments(arguments, allowed):
    unexpected = sorted(set(arguments) - allowed)
    if unexpected:
        raise ToolInputError('unexpected arguments: ' + ', '.join(unexpected))
