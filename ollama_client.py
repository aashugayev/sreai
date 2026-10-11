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
import sys
import urllib.error
import urllib.request

from incident_analysis import (
    ANALYSIS_JSON_SCHEMA,
    AnalysisValidationError,
    analysis_to_dict,
    build_analysis_prompt,
    parse_analysis_response,
    parse_incident_message,
)


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


def generate_response(prompt, opt, model=None, response_format=None, temperature=None):
    """Send one prompt to Ollama's chat endpoint and return the reply text.

    This is the single model-integration point for the POC. Streaming is
    disabled so the whole reply arrives as one JSON document, which keeps this
    first stage easy to read.

    Args:
        prompt: The user prompt to send.
        opt: Options loaded by ``load_options`` (``host``, ``model``,
            ``timeoutseconds``).
        model: Optional model name overriding ``opt['model']``.
        response_format: Optional Ollama ``format`` value, such as a JSON
            Schema.
        temperature: Optional model temperature.

    Returns:
        str: The model's reply text.

    Raises:
        OllamaError: If the server is unreachable, times out, or returns a
            response that does not contain reply text.
    """
    host = opt.get('host', 'http://localhost:11434').rstrip('/')
    model = model or opt['model']
    timeout = float(opt.get('timeoutseconds', 120))
    url = host + '/api/chat'

    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'stream': False,
    }
    if response_format is not None:
        payload['format'] = response_format
    if temperature is not None:
        payload['options'] = {'temperature': temperature}
    logger.info('POST ' + url + ' model=' + str(model))
    logger.debug('prompt:' + str(prompt))

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode('utf-8'))
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

    content = body.get('message', {}).get('content')
    if content is None:
        raise OllamaError('no message content in Ollama response: ' + str(body))

    return content


def main(debug_level=logging.ERROR, log_file=None, prompt=None, config_path='./IncidentAgent.ini',
         model=None, display_only=False, analyze_incident=False):
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
        analysis_prompt = build_analysis_prompt(incident)
        response = generate_response(
            analysis_prompt,
            opt,
            model,
            response_format=ANALYSIS_JSON_SCHEMA,
            temperature=0,
        )
        analysis = parse_analysis_response(response, incident)
        print(json.dumps(analysis_to_dict(analysis), indent=2))
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
        )
    except (OllamaError, AnalysisValidationError) as error:
        raise SystemExit('error: ' + str(error))
