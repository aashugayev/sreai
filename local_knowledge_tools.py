"""Bounded, read-only keyword retrieval over the local incident knowledge base."""

import json
from pathlib import Path
import re

from local_tool_registry import (
    LocalToolRegistry,
    ToolExecutionError,
    ToolInputError,
)


REPOSITORY_ROOT = Path(__file__).resolve().parent
KNOWLEDGE_BASE_PATH = Path('sample_data') / 'knowledge_base.json'
MAX_KNOWLEDGE_BASE_BYTES = 1_000_000
MAX_KNOWLEDGE_DOCUMENTS = 100
MAX_EXCERPT_LENGTH = 500


KNOWLEDGE_SEARCH_TOOL = {
    'name': 'knowledge_search',
    'description': (
        'Search local runbooks and prior incident records by keyword. Returns '
        'ranked document titles, source IDs, matched terms, and bounded '
        'excerpts. This is local fixture data, not a live knowledge service.'
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
                'maximum': 10,
                'default': 5,
            },
        },
        'required': ['query'],
    },
}


def build_knowledge_registry(repository_root=REPOSITORY_ROOT):
    """Create a registry containing the local knowledge search tool."""
    root = Path(repository_root).resolve()
    registry = LocalToolRegistry()
    registry.register(
        KNOWLEDGE_SEARCH_TOOL,
        lambda arguments: search_knowledge(arguments, root),
    )
    return registry


def search_knowledge(arguments, repository_root=REPOSITORY_ROOT):
    """Return bounded knowledge-base documents ranked by keyword overlap."""
    unexpected = set(arguments) - {'query', 'max_results'}
    if unexpected:
        raise ToolInputError(
            'unexpected argument: ' + sorted(unexpected)[0],
        )

    query = arguments.get('query')
    if not isinstance(query, str):
        raise ToolInputError('query must be a string')
    if not query.strip() or len(query) > 200:
        raise ToolInputError('query must contain 1 to 200 characters')

    max_results = arguments.get('max_results', 5)
    if isinstance(max_results, bool) or not isinstance(max_results, int):
        raise ToolInputError('max_results must be an integer')
    if not 1 <= max_results <= 10:
        raise ToolInputError('max_results must be between 1 and 10')

    query_terms = _terms(query)
    if not query_terms:
        raise ToolInputError('query must contain a word with at least 2 characters')

    knowledge_path = Path(repository_root).resolve() / KNOWLEDGE_BASE_PATH
    try:
        if knowledge_path.stat().st_size > MAX_KNOWLEDGE_BASE_BYTES:
            raise ToolExecutionError(
                'knowledge base exceeds the 1 MB size limit',
            )
        records = json.loads(knowledge_path.read_text(encoding='utf-8'))
    except FileNotFoundError as error:
        raise ToolExecutionError(
            'knowledge base not found: ' + str(KNOWLEDGE_BASE_PATH),
        ) from error
    except OSError as error:
        raise ToolExecutionError(
            'could not read knowledge base: ' + str(error),
        ) from error
    except json.JSONDecodeError as error:
        raise ToolExecutionError(
            'knowledge base is not valid JSON at character ' + str(error.pos),
        ) from error

    if not isinstance(records, list):
        raise ToolExecutionError('knowledge base must contain a JSON array')
    if len(records) > MAX_KNOWLEDGE_DOCUMENTS:
        raise ToolExecutionError(
            'knowledge base contains more than '
            + str(MAX_KNOWLEDGE_DOCUMENTS)
            + ' documents',
        )

    matches = []
    document_ids = set()
    for record in records:
        if not isinstance(record, dict):
            raise ToolExecutionError('knowledge base contains an invalid document')
        document_id = record.get('id')
        document_type = record.get('type')
        title = record.get('title')
        tags = record.get('tags')
        content = record.get('content')
        if (
            not isinstance(document_id, str)
            or not document_id
            or document_id in document_ids
            or not isinstance(document_type, str)
            or not isinstance(title, str)
            or not isinstance(tags, list)
            or not all(isinstance(tag, str) for tag in tags)
            or not isinstance(content, str)
        ):
            raise ToolExecutionError(
                'knowledge base contains an invalid or duplicate document',
            )
        document_ids.add(document_id)

        title_terms = _terms(title)
        tag_terms = _terms(' '.join(tags))
        content_terms = _terms(content)
        matched_terms = query_terms & (title_terms | tag_terms | content_terms)
        if not matched_terms:
            continue

        match_score = (
            3 * len(query_terms & title_terms)
            + 2 * len(query_terms & tag_terms)
            + len(query_terms & content_terms)
        )
        matches.append(
            {
                'id': document_id,
                'type': document_type,
                'title': title,
                'match_score': match_score,
                'matched_terms': sorted(matched_terms),
                'excerpt': _excerpt(content, matched_terms),
            },
        )

    matches.sort(key=lambda item: (-item['match_score'], item['id']))
    return {
        'query': query,
        'total_matches': len(matches),
        'returned': min(len(matches), max_results),
        'documents': matches[:max_results],
    }


def _terms(value):
    return set(re.findall(r'[a-z0-9]{2,}', value.casefold()))


def _excerpt(content, matched_terms):
    if len(content) <= MAX_EXCERPT_LENGTH:
        return content

    folded_content = content.casefold()
    positions = [
        folded_content.find(term)
        for term in matched_terms
        if folded_content.find(term) >= 0
    ]
    if not positions:
        return content[:MAX_EXCERPT_LENGTH]

    start = max(min(positions) - MAX_EXCERPT_LENGTH // 4, 0)
    prefix = '...' if start else ''
    end = min(start + MAX_EXCERPT_LENGTH - len(prefix), len(content))
    suffix = '...' if end < len(content) else ''
    if suffix:
        end -= len(suffix)
    return prefix + content[start:end] + suffix
