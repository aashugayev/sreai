"""Small MCP-shaped registry for local, in-process tools."""

import argparse
from copy import deepcopy
import json
import sys
from typing import Any, Callable


class ToolInputError(ValueError):
    """Raised when a tool call contains invalid arguments."""


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


class LocalToolRegistry:
    """List and call tools using the data shapes used by MCP."""

    def __init__(self):
        self._tools = {}

    def register(self, definition, handler):
        """Register one tool definition and its in-process handler."""
        name = definition.get('name')
        if not isinstance(name, str) or not name:
            raise ValueError('tool definition requires a non-empty name')
        if name in self._tools:
            raise ValueError('tool already registered: ' + name)
        if not callable(handler):
            raise TypeError('tool handler must be callable')

        self._tools[name] = (deepcopy(definition), handler)

    def list_tools(self):
        """Return an MCP-compatible tools/list result."""
        return {
            'tools': [
                deepcopy(definition)
                for definition, _handler in self._tools.values()
            ],
        }

    def call_tool(self, name, arguments):
        """Return an MCP-compatible tools/call result."""
        registered = self._tools.get(name)
        if registered is None:
            return error_result('unknown tool: ' + str(name))
        if not isinstance(arguments, dict):
            return error_result('tool arguments must be a JSON object')

        _definition, handler = registered
        try:
            structured_content = handler(arguments)
        except ToolInputError as error:
            return error_result(str(error))

        return {
            'content': [
                {
                    'type': 'text',
                    'text': json.dumps(structured_content, sort_keys=True),
                },
            ],
            'structuredContent': structured_content,
            'isError': False,
        }


def error_result(message):
    """Build a tool-level error result rather than disguising it as success."""
    return {
        'content': [{'type': 'text', 'text': message}],
        'isError': True,
    }


def build_default_registry():
    """Return the local registry with all currently available tools."""
    from splunk_search_tool import SPLUNK_SEARCH_TOOL, search_events

    registry = LocalToolRegistry()
    registry.register(SPLUNK_SEARCH_TOOL, search_events)
    return registry


def parse_args():
    """Parse the manual tool-registry demonstration commands."""
    parser = argparse.ArgumentParser(
        description='List or call the incident agent local tools.',
    )
    subparsers = parser.add_subparsers(dest='command', required=True)
    subparsers.add_parser('list', help='print available tool definitions')

    call_parser = subparsers.add_parser('call', help='call one local tool')
    call_parser.add_argument('name', help='registered tool name')
    call_parser.add_argument(
        'arguments',
        help='tool arguments as one JSON object, or - to read JSON from stdin',
    )
    return parser.parse_args()


def main():
    """Run a manual tools/list or tools/call demonstration."""
    args = parse_args()
    registry = build_default_registry()

    if args.command == 'list':
        result = registry.list_tools()
    else:
        arguments_text = args.arguments
        if arguments_text == '-':
            arguments_text = sys.stdin.read()
        try:
            arguments = json.loads(arguments_text)
        except json.JSONDecodeError as error:
            raise SystemExit(
                'error: arguments are not valid JSON at character '
                + str(error.pos)
            ) from error
        result = registry.call_tool(args.name, arguments)

    print(json.dumps(result, indent=2))
    if result.get('isError'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
