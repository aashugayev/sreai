"""In-process MCP-shaped simulator for a Splunk search server."""

from copy import deepcopy
import json

from local_tool_registry import ToolInputError
from splunk_search_tool import SPLUNK_SEARCH_TOOL, search_events


class SplunkMcpError(RuntimeError):
    """Raised when the simulated MCP exchange returns a protocol error."""


class SimulatedSplunkMcpServer:
    """Return deterministic MCP-shaped responses backed by sample events."""

    def __init__(self):
        self._tool_definitions = {
            SPLUNK_SEARCH_TOOL['name']: deepcopy(SPLUNK_SEARCH_TOOL),
        }
        self._tool_handlers = {
            SPLUNK_SEARCH_TOOL['name']: search_events,
        }

    def handle_request(self, request):
        """Handle one JSON-RPC request using the MCP tools methods."""
        if not isinstance(request, dict) or request.get('jsonrpc') != '2.0':
            return self._error_response(None, -32600, 'invalid JSON-RPC request')

        request_id = request.get('id')
        if isinstance(request_id, bool) or not isinstance(request_id, (int, str)):
            return self._error_response(None, -32600, 'request id must be a string or integer')

        method = request.get('method')
        params = request.get('params', {})
        if not isinstance(method, str) or not isinstance(params, dict):
            return self._error_response(request_id, -32600, 'invalid request method or params')

        if method == 'tools/list':
            result = {
                'tools': [
                    deepcopy(definition)
                    for definition in self._tool_definitions.values()
                ],
            }
        elif method == 'tools/call':
            result_or_error = self._call_tool(params)
            if 'error' in result_or_error:
                return {
                    'jsonrpc': '2.0',
                    'id': request_id,
                    'error': result_or_error['error'],
                }
            result = result_or_error
        else:
            return self._error_response(
                request_id,
                -32601,
                'method not found: ' + method,
            )

        return {
            'jsonrpc': '2.0',
            'id': request_id,
            'result': result,
        }

    def _call_tool(self, params):
        name = params.get('name')
        arguments = params.get('arguments', {})
        if not isinstance(name, str) or not name:
            return {'error': {'code': -32602, 'message': 'tool name is required'}}
        if not isinstance(arguments, dict):
            return {'error': {'code': -32602, 'message': 'tool arguments must be an object'}}

        handler = self._tool_handlers.get(name)
        if handler is None:
            return {'error': {'code': -32602, 'message': 'unknown tool: ' + name}}
        try:
            structured_content = handler(arguments)
        except ToolInputError as error:
            return {
                'content': [{'type': 'text', 'text': str(error)}],
                'isError': True,
            }

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

    @staticmethod
    def _error_response(request_id, code, message):
        return {
            'jsonrpc': '2.0',
            'id': request_id,
            'error': {'code': code, 'message': message},
        }


class SimulatedSplunkMcpClient:
    """Call the in-process server through MCP-shaped JSON-RPC messages."""

    def __init__(self, server=None):
        self._server = server or SimulatedSplunkMcpServer()
        self._next_request_id = 1

    def list_tools(self):
        """Discover tools through a simulated ``tools/list`` exchange."""
        result = self._request('tools/list')
        tools = result.get('tools')
        if not isinstance(tools, list):
            raise SplunkMcpError('tools/list response did not contain a tools array')
        return {'tools': tools}

    def call_tool(self, name, arguments):
        """Execute a tool through a simulated ``tools/call`` exchange."""
        result = self._request(
            'tools/call',
            {'name': name, 'arguments': arguments},
        )
        if (
            not isinstance(result.get('content'), list)
            or not isinstance(result.get('isError'), bool)
        ):
            raise SplunkMcpError('tools/call response has an invalid result shape')
        return result

    def _request(self, method, params=None):
        request_id = self._next_request_id
        self._next_request_id += 1
        request = {
            'jsonrpc': '2.0',
            'id': request_id,
            'method': method,
        }
        if params is not None:
            request['params'] = params

        response = self._server.handle_request(request)
        if (
            not isinstance(response, dict)
            or response.get('jsonrpc') != '2.0'
            or response.get('id') != request_id
        ):
            raise SplunkMcpError('simulated MCP server returned an invalid response')
        if 'error' in response:
            error = response['error']
            if not isinstance(error, dict) or not isinstance(error.get('message'), str):
                raise SplunkMcpError('simulated MCP server returned a malformed error')
            raise SplunkMcpError(error['message'])
        result = response.get('result')
        if not isinstance(result, dict):
            raise SplunkMcpError('simulated MCP server returned no result object')
        return result


def build_simulated_splunk_mcp_client():
    """Create the deterministic MCP client used by incident analysis."""
    return SimulatedSplunkMcpClient()
