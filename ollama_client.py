"""Minimal Ollama client for the incident-investigation POC.

Build-plan stage 2: send one prompt to a locally running model over HTTP and
print the response. Deliberately uses only the Python standard library so the
POC has no third-party dependencies to install.

All model access goes through ``generate_response()``. Keeping that one
function as the single integration point means a different backend (a hosted
API, or a router such as LiteLLM) can be swapped in later without touching the
Splunk, source-control, or knowledge-base code built in later stages.
"""

import argparse
import configparser
import json
import logging
import math
import sys
import time
import urllib.error
import urllib.request

from incident_analysis import (
    ANALYSIS_JSON_SCHEMA,
    AnalysisValidationError,
    build_analysis_prompt,
    parse_analysis_response,
    parse_incident_message,
)
from incident_report import render_incident_report
from local_code_tools import build_local_code_registry
from local_knowledge_tools import build_knowledge_registry
from simulated_splunk_mcp import build_simulated_splunk_mcp_client


logger = logging.getLogger(name='incidentAgent')
logger.setLevel(logging.DEBUG)
console = logging.StreamHandler()
formatter = logging.Formatter(
    '%(filename)s:%(funcName)s:[%(lineno)d]:%(levelname)s - %(message)s'
)
console.setFormatter(formatter)
logger.addHandler(console)


def set_level(level):
    """Set the level used by the incident-agent logger's console handler."""
    console.setLevel(level)


def set_log_file(path):
    """Redirect logging and printed output to a log file."""
    logger.removeHandler(console)

    file_handler = logging.FileHandler(path, mode='w')
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    log_stream = open(path, 'a')
    sys.stdout = log_stream
    sys.stderr = log_stream


set_level(logging.ERROR)


class OllamaError(RuntimeError):
    """Raised when Ollama cannot be reached or returns an unusable response."""


class AgentLoopError(OllamaError):
    """Raised when a tool-using analysis cannot complete within its limits."""


def parse_args():
    """Parse command-line options for a single prompt."""
    parser = argparse.ArgumentParser(
        description='Send one prompt to a local Ollama model and print the response.',
    )
    parser.add_argument(
        'prompt',
        nargs='*',
        help='prompt text (if omitted, the prompt is read from standard input)',
    )
    parser.add_argument(
        '-c', '--config',
        default='./IncidentAgent.ini',
        metavar='PATH',
        help='ini file holding the Ollama settings (default: ./IncidentAgent.ini)',
    )
    parser.add_argument(
        '-m', '--model',
        default=None,
        help='override the model named in the ini file',
    )
    parser.add_argument(
        '-d', '--debug-level',
        default='ERROR',
        choices=('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'),
        help='logging level (default: ERROR)',
    )
    parser.add_argument(
        '-l', '--log-file',
        nargs='?',
        const='',
        default=None,
        metavar='PATH',
        help='redirect the logger (all levels) and print() statements to a log file '
             '(default: this script\'s name with a .log extension)',
    )
    parser.add_argument(
        '--max-tool-calls',
        type=int,
        default=5,
        help='maximum local tool calls during incident analysis (default: 5)',
    )
    parser.add_argument(
        '--agent-timeout-seconds',
        type=float,
        default=300,
        help='total seconds allowed for incident analysis (default: 300)',
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        '--display-only',
        action='store_true',
        help='print the supplied message without sending it to Ollama',
    )
    mode.add_argument(
        '--analyze-incident',
        action='store_true',
        help='analyze normalized incident JSON and print validated JSON output',
    )
    return parser.parse_args()


def load_options(config_path='./IncidentAgent.ini', section='Ollama'):
    """Load and log every option from one ini section.

    Args:
        config_path: Path to the ini file to read.
        section: Section name within that file to load options from.

    Returns:
        dict: option name -> string value. Option names are lower-cased by
        ``configparser``, so lookups elsewhere use lowercase keys
        (e.g. ``opt['model']``).

    Raises:
        OllamaError: If the file or section is missing.
    """
    opt = dict()
    config = configparser.ConfigParser()

    if not config.read(config_path):
        raise OllamaError('config file not found: ' + str(config_path))

    if not config.has_section(section):
        raise OllamaError('section [' + str(section) + '] not found in ' + str(config_path))

    for option in config.options(section):
        opt[option] = config.get(section, option)
        logger.info(str(option) + ':' + str(opt[option]))

    return opt


def generate_chat_message(
    messages,
    opt,
    model=None,
    response_format=None,
    temperature=None,
    tools=None,
    timeout=None,
):
    """Send a chat history to Ollama and return its assistant message.

    Streaming is disabled so the complete assistant message, including any
    requested tool calls, arrives as one JSON document.

    Args:
        messages: Ollama chat messages.
        opt: Options loaded by ``load_options`` (``host``, ``model``,
            ``timeoutseconds``).
        model: Optional model name overriding ``opt['model']``.
        response_format: Optional Ollama ``format`` value, such as a JSON
            Schema.
        temperature: Optional model temperature.
        tools: Optional tool definitions exposed to the model.
        timeout: Optional request timeout overriding the configured timeout.

    Returns:
        dict: The assistant message returned by Ollama.

    Raises:
        OllamaError: If the server is unreachable, times out, or returns a
            response that does not contain an assistant message.
    """
    host = opt.get('host', 'http://localhost:11434').rstrip('/')
    model = model or opt['model']
    timeout = float(timeout if timeout is not None else opt.get('timeoutseconds', 120))
    url = host + '/api/chat'

    payload = {
        'model': model,
        'messages': messages,
        'stream': False,
    }
    if response_format is not None:
        payload['format'] = response_format
    if temperature is not None:
        payload['options'] = {'temperature': temperature}
    if tools is not None:
        payload['tools'] = tools
    logger.info('POST ' + url + ' model=' + str(model))
    logger.debug('messages:' + str(messages))

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode('utf-8'))
    except json.JSONDecodeError as error:
        raise OllamaError(
            'Ollama returned invalid JSON at character ' + str(error.pos)
        ) from error
    except urllib.error.HTTPError as error:
        detail = error.read().decode('utf-8', errors='replace').strip()
        raise OllamaError(
            'Ollama returned HTTP ' + str(error.code) + ': ' + detail
        ) from error
    except urllib.error.URLError as error:
        raise OllamaError(
            'cannot reach Ollama at ' + host + ' (' + str(error.reason) + '). '
            'Confirm the server is running with: ollama list'
        ) from error
    except TimeoutError as error:
        raise OllamaError(
            'Ollama did not respond within ' + str(timeout) + 's. A cold model '
            'load can be slow; raise TimeoutSeconds in the ini file.'
        ) from error

    logger.debug('response:' + str(body))

    if not isinstance(body, dict) or not isinstance(body.get('message'), dict):
        raise OllamaError('no assistant message in Ollama response: ' + str(body))

    return body['message']


def generate_response(prompt, opt, model=None, response_format=None, temperature=None):
    """Send one prompt to Ollama's chat endpoint and return the reply text."""
    message = generate_chat_message(
        [{'role': 'user', 'content': prompt}],
        opt,
        model=model,
        response_format=response_format,
        temperature=temperature,
    )
    content = message.get('content')
    if not isinstance(content, str):
        raise OllamaError('no text content in Ollama response: ' + str(message))

    return content


def build_agent_prompt(incident):
    """Add tool-use rules to the validated structured-analysis prompt."""
    return (
        build_analysis_prompt(incident)
        + '\n\nYou may call the supplied read-only tools to gather evidence. '
        'Before finalizing, consider whether related events from the incident '
        'service and environment, relevant sample code or recent changes, or '
        'related runbooks and prior incidents would add evidence, and inspect '
        'them when useful. Use a tool only '
        'when it can help answer the incident. Treat tool output as evidence '
        'returned by that tool, not as a verified root cause. Git authorship '
        'is routing context, not evidence of fault. Only describe a search or '
        'inspection as completed if you actually called the tool. If no tool '
        'is needed, return the final JSON now. Cite the incident as source_id '
        'INCIDENT. Each successful tool result is labeled TOOL-N; cite only '
        'source IDs actually present in the conversation.'
    )


def to_ollama_tool_definitions(tool_definitions):
    """Convert MCP-shaped local definitions to Ollama function-tool format."""
    ollama_tools = []
    for definition in tool_definitions:
        name = definition.get('name')
        description = definition.get('description')
        input_schema = definition.get('inputSchema')
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(description, str)
            or not isinstance(input_schema, dict)
        ):
            raise AgentLoopError('registered tool has an invalid definition')
        ollama_tools.append(
            {
                'type': 'function',
                'function': {
                    'name': name,
                    'description': description,
                    'parameters': input_schema,
                },
            },
        )
    return ollama_tools


def run_agent_loop(
    incident,
    opt,
    model=None,
    max_tool_calls=5,
    timeout_seconds=300,
    evidence_sources=None,
):
    """Let Ollama request bounded calls to registered local tools."""
    if isinstance(max_tool_calls, bool) or not isinstance(max_tool_calls, int):
        raise AgentLoopError('max tool calls must be an integer')
    if max_tool_calls < 1:
        raise AgentLoopError('max tool calls must be at least 1')
    if isinstance(timeout_seconds, bool) or not isinstance(
        timeout_seconds,
        (int, float),
    ):
        raise AgentLoopError('agent timeout must be a number of seconds')
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise AgentLoopError('agent timeout must be greater than 0 seconds')

    try:
        configured_timeout = float(opt.get('timeoutseconds', 120))
    except ValueError as error:
        raise AgentLoopError('TimeoutSeconds must be a positive number') from error
    if not math.isfinite(configured_timeout) or configured_timeout <= 0:
        raise AgentLoopError('TimeoutSeconds must be greater than 0')

    mcp_client = build_simulated_splunk_mcp_client()
    code_registry = build_local_code_registry()
    knowledge_registry = build_knowledge_registry()
    clients = (mcp_client, code_registry, knowledge_registry)
    tool_definitions = []
    tool_clients = {}
    for client in clients:
        definitions = client.list_tools()['tools']
        tool_definitions.extend(definitions)
        for definition in definitions:
            tool_clients[definition['name']] = client
    tools = to_ollama_tool_definitions(tool_definitions)
    messages = [
        {
            'role': 'user',
            'content': build_agent_prompt(incident),
        },
    ]
    deadline = time.monotonic() + timeout_seconds
    tool_calls_made = 0

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AgentLoopError(
                'incident analysis exceeded its '
                + str(timeout_seconds)
                + ' second timeout'
            )

        message = generate_chat_message(
            messages,
            opt,
            model=model,
            temperature=0,
            tools=tools,
            timeout=min(configured_timeout, remaining),
        )
        if time.monotonic() >= deadline:
            raise AgentLoopError(
                'incident analysis exceeded its '
                + str(timeout_seconds)
                + ' second timeout'
            )
        tool_calls = message.get('tool_calls', [])
        if not isinstance(tool_calls, list):
            raise AgentLoopError('Ollama returned malformed tool calls')

        if not tool_calls:
            content = message.get('content')
            if not isinstance(content, str) or not content.strip():
                raise OllamaError(
                    'Ollama returned neither tool calls nor final text'
                )
            messages.extend(
                [
                    message,
                    {
                        'role': 'user',
                        'content': (
                            'Now return the final incident analysis as JSON '
                            'conforming exactly to the JSON Schema in the '
                            'original request. Use only the incident and '
                            'tool results in this conversation as evidence.'
                        ),
                    },
                ],
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AgentLoopError(
                    'incident analysis exceeded its '
                    + str(timeout_seconds)
                    + ' second timeout'
                )
            final_message = generate_chat_message(
                messages,
                opt,
                model=model,
                response_format=ANALYSIS_JSON_SCHEMA,
                temperature=0,
                timeout=min(configured_timeout, remaining),
            )
            if time.monotonic() >= deadline:
                raise AgentLoopError(
                    'incident analysis exceeded its '
                    + str(timeout_seconds)
                    + ' second timeout'
                )
            if final_message.get('tool_calls'):
                raise AgentLoopError(
                    'Ollama requested a tool during final structured output'
                )
            final_content = final_message.get('content')
            if not isinstance(final_content, str) or not final_content.strip():
                raise OllamaError('Ollama returned no final analysis text')
            return final_content

        if tool_calls_made + len(tool_calls) > max_tool_calls:
            raise AgentLoopError(
                'Ollama requested more than the configured maximum of '
                + str(max_tool_calls)
                + ' tool calls'
            )

        messages.append(message)
        for tool_call in tool_calls:
            function = tool_call.get('function') if isinstance(tool_call, dict) else None
            if not isinstance(function, dict):
                raise AgentLoopError('Ollama returned a malformed tool call')
            name = function.get('name')
            arguments = function.get('arguments')
            if not isinstance(name, str) or not name:
                raise AgentLoopError('Ollama returned a tool call without a name')
            if not isinstance(arguments, dict):
                raise AgentLoopError(
                    'Ollama returned non-object arguments for tool ' + name
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AgentLoopError(
                    'incident analysis exceeded its '
                    + str(timeout_seconds)
                    + ' second timeout'
                )
            client = tool_clients.get(name)
            if client is None:
                raise AgentLoopError('Ollama requested an unavailable tool: ' + name)
            logger.info('Calling investigation tool ' + name)
            result = client.call_tool(name, arguments)
            source_id = 'TOOL-' + str(tool_calls_made + 1)
            source = {
                'source_id': source_id,
                'tool_name': name,
                'arguments': arguments,
                'result': result,
                'is_error': result.get('isError', True),
            }
            if evidence_sources is not None:
                evidence_sources.append(source)
            tool_calls_made += 1
            if time.monotonic() >= deadline:
                raise AgentLoopError(
                    'incident analysis exceeded its '
                    + str(timeout_seconds)
                    + ' second timeout'
                )
            messages.append(
                {
                    'role': 'tool',
                    'content': json.dumps(
                        dict(result, source_id=source_id),
                        sort_keys=True,
                    ),
                },
            )


def main(debug_level=logging.ERROR, log_file=None, prompt=None, config_path='./IncidentAgent.ini',
         model=None, display_only=False, analyze_incident=False,
         max_tool_calls=5, agent_timeout_seconds=300):
    """Display a message, analyze an incident, or send a free-form prompt."""
    set_level(debug_level)
    if log_file is not None:
        set_log_file(log_file)

    if not prompt:
        if sys.stdin.isatty():
            prompt = input('Prompt: ')
        else:
            prompt = sys.stdin.read()
    if not prompt.strip():
        raise OllamaError('no prompt supplied')

    if display_only:
        print('--- message received by ollama_client.py ---')
        print(prompt)
        return

    opt = load_options(config_path)

    if analyze_incident:
        incident = parse_incident_message(prompt)
        evidence_sources = []
        response = run_agent_loop(
            incident,
            opt,
            model=model,
            max_tool_calls=max_tool_calls,
            timeout_seconds=agent_timeout_seconds,
            evidence_sources=evidence_sources,
        )
        available_source_ids = [
            source['source_id']
            for source in evidence_sources
            if not source['is_error']
        ]
        analysis = parse_analysis_response(
            response,
            incident,
            available_source_ids=available_source_ids,
        )
        print(render_incident_report(analysis, incident, evidence_sources))
        return

    print(generate_response(prompt, opt, model))


if __name__ == '__main__':
    args = parse_args()

    log_file = args.log_file
    if log_file == '':
        log_file = __file__.rsplit('.', 1)[0] + '.log'

    try:
        main(
            debug_level=getattr(logging, args.debug_level),
            log_file=log_file,
            prompt=' '.join(args.prompt),
            config_path=args.config,
            model=args.model,
            display_only=args.display_only,
            analyze_incident=args.analyze_incident,
            max_tool_calls=args.max_tool_calls,
            agent_timeout_seconds=args.agent_timeout_seconds,
        )
    except (OllamaError, AnalysisValidationError) as error:
        raise SystemExit('error: ' + str(error))
