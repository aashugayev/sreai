"""Read-only fake Splunk search backed by deterministic sample events."""

from datetime import datetime
import json
from pathlib import Path

from local_tool_registry import ToolInputError


DEFAULT_EVENTS_PATH = Path(__file__).parent / 'sample_data' / 'splunk_events.json'
ALLOWED_ARGUMENTS = {
    'query',
    'service',
    'environment',
    'host',
    'earliest',
    'latest',
    'limit',
}

SPLUNK_SEARCH_TOOL = {
    'name': 'splunk_search',
    'title': 'Search Splunk events',
    'description': (
        'Read-only search over local sample Splunk events. All whitespace-'
        'separated query terms must occur somewhere in an event. Optional '
        'service, environment, and host filters are exact matches. Results '
        'are returned newest first. This tool does not accept or execute SPL.'
    ),
    'inputSchema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'query': {
                'type': 'string',
                'minLength': 1,
                'maxLength': 200,
                'description': 'Case-insensitive terms that must all match.',
            },
            'service': {'type': 'string', 'minLength': 1},
            'environment': {'type': 'string', 'minLength': 1},
            'host': {'type': 'string', 'minLength': 1},
            'earliest': {
                'type': 'string',
                'format': 'date-time',
                'description': 'Inclusive lower timestamp bound.',
            },
            'latest': {
                'type': 'string',
                'format': 'date-time',
                'description': 'Inclusive upper timestamp bound.',
            },
            'limit': {
                'type': 'integer',
                'minimum': 1,
                'maximum': 100,
                'default': 20,
            },
        },
        'required': ['query'],
    },
    'outputSchema': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'query': {'type': 'string'},
            'total_matches': {'type': 'integer'},
            'returned': {'type': 'integer'},
            'events': {
                'type': 'array',
                'items': {'type': 'object'},
            },
        },
        'required': ['query', 'total_matches', 'returned', 'events'],
    },
}


def require_string(arguments, name, required=False):
    """Validate and normalize one optional string argument."""
    value = arguments.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolInputError(name + ' must be a non-empty string')
    value = value.strip()
    if len(value) > 200:
        raise ToolInputError(name + ' must not exceed 200 characters')
    return value


def parse_timestamp(value, name):
    """Parse a timezone-aware ISO 8601 timestamp."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ToolInputError(name + ' must be an ISO 8601 timestamp') from error
    if parsed.tzinfo is None:
        raise ToolInputError(name + ' must include a timezone')
    return parsed


def load_events(path=DEFAULT_EVENTS_PATH):
    """Load and validate the local event fixture."""
    try:
        with path.open(encoding='utf-8') as event_file:
            events = json.load(event_file)
    except FileNotFoundError as error:
        raise RuntimeError('Splunk event fixture not found: ' + str(path)) from error
    except json.JSONDecodeError as error:
        raise RuntimeError(
            'Splunk event fixture is invalid JSON at character ' + str(error.pos)
        ) from error

    if not isinstance(events, list):
        raise RuntimeError('Splunk event fixture must be a JSON array')
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise RuntimeError('Splunk event fixture item ' + str(index) + ' is not an object')
        timestamp = event.get('_time')
        if not isinstance(timestamp, str):
            raise RuntimeError('Splunk event fixture item ' + str(index) + ' has no _time')
        try:
            parse_timestamp(timestamp, 'event._time')
        except ToolInputError as error:
            raise RuntimeError(
                'Splunk event fixture item ' + str(index) + ': ' + str(error)
            ) from error
    return events


def search_events(arguments, events_path=DEFAULT_EVENTS_PATH):
    """Search the sample event fixture using bounded, read-only semantics."""
    unexpected = sorted(set(arguments) - ALLOWED_ARGUMENTS)
    if unexpected:
        raise ToolInputError(
            'unexpected arguments: ' + ', '.join(unexpected)
        )

    query = require_string(arguments, 'query', required=True)
    query_terms = query.casefold().split()
    service = require_string(arguments, 'service')
    environment = require_string(arguments, 'environment')
    host = require_string(arguments, 'host')
    earliest_text = require_string(arguments, 'earliest')
    latest_text = require_string(arguments, 'latest')
    earliest = parse_timestamp(earliest_text, 'earliest')
    latest = parse_timestamp(latest_text, 'latest')
    if earliest is not None and latest is not None and earliest > latest:
        raise ToolInputError('earliest must not be after latest')

    limit = arguments.get('limit', 20)
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ToolInputError('limit must be an integer')
    if limit < 1 or limit > 100:
        raise ToolInputError('limit must be between 1 and 100')

    matches = []
    for event in load_events(events_path):
        timestamp = parse_timestamp(event['_time'], 'event._time')
        searchable = json.dumps(event, sort_keys=True).casefold()
        if not all(term in searchable for term in query_terms):
            continue
        if service is not None and event.get('service') != service:
            continue
        if environment is not None and event.get('environment') != environment:
            continue
        if host is not None and event.get('host') != host:
            continue
        if earliest is not None and timestamp < earliest:
            continue
        if latest is not None and timestamp > latest:
            continue
        matches.append(event)

    matches.sort(
        key=lambda event: parse_timestamp(event['_time'], 'event._time'),
        reverse=True,
    )
    returned_events = matches[:limit]
    return {
        'query': query,
        'total_matches': len(matches),
        'returned': len(returned_events),
        'events': returned_events,
    }
