"""Demonstrate the local Splunk-to-client workflow.

This script starts the webhook receiver, sends one mock Splunk alert, waits
for the receiver's normalized incident signal, and pipes that signal into a
separate ``ollama_client.py`` process. Display-only remains the default;
``--analyze`` requests a validated structured analysis from Ollama.
"""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import Thread
import urllib.error

from send_test_alert import build_request_body, send_alert
from splunk_receiver import SplunkWebhookServer, WEBHOOK_PATH


ROOT = Path(__file__).resolve().parent
CLIENT = ROOT / 'ollama_client.py'
CONFIG = ROOT / 'IncidentAgent.ini'
SAMPLE_PAYLOAD = ROOT / 'sample_data' / 'splunk_alert.json'


class OrchestrationError(RuntimeError):
    """Raised when a local handoff stage does not complete correctly."""


def parse_args():
    """Parse local orchestration settings."""
    parser = argparse.ArgumentParser(
        description=(
            'Start the Splunk receiver, send one alert, and pass the normalized '
            'signal to ollama_client.py.'
        ),
    )
    parser.add_argument(
        '--host',
        default='127.0.0.1',
        help='receiver bind address (default: 127.0.0.1)',
    )
    parser.add_argument(
        '--port',
        default=8080,
        type=int,
        help='receiver port (default: 8080)',
    )
    parser.add_argument(
        '--payload',
        default=str(SAMPLE_PAYLOAD),
        metavar='PATH',
        help='JSON alert payload to send (default: sample_data/splunk_alert.json)',
    )
    parser.add_argument(
        '--timeout',
        default=10.0,
        type=float,
        help='seconds to wait for the webhook handoff (default: 10)',
    )
    parser.add_argument(
        '--analyze',
        action='store_true',
        help='request tool-assisted, validated structured analysis from Ollama',
    )
    parser.add_argument(
        '--max-tool-calls',
        type=int,
        default=5,
        help='maximum local tool calls during analysis (default: 5)',
    )
    parser.add_argument(
        '--agent-timeout-seconds',
        type=float,
        default=300,
        help='total seconds allowed for analysis (default: 300)',
    )
    return parser.parse_args()


def run_client(signal, analyze, max_tool_calls=5, agent_timeout_seconds=300):
    """Start the client and pipe it one normalized incident signal."""
    message = json.dumps(asdict(signal), indent=2)
    mode = '--analyze-incident' if analyze else '--display-only'
    completed = subprocess.run(
        [
            sys.executable,
            str(CLIENT),
            '--config',
            str(CONFIG),
            mode,
            '--max-tool-calls',
            str(max_tool_calls),
            '--agent-timeout-seconds',
            str(agent_timeout_seconds),
        ],
        input=message,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise OrchestrationError(
            'ollama_client.py exited with code ' + str(completed.returncode)
        )


def main():
    """Run one complete transport-only handoff."""
    args = parse_args()
    incidents = Queue(maxsize=1)
    server = SplunkWebhookServer(
        (args.host, args.port),
        incident_handler=incidents.put_nowait,
    )
    actual_host, actual_port = server.server_address
    receiver_thread = Thread(
        target=server.serve_forever,
        name='splunk-webhook-receiver',
    )

    print(
        '1. Starting receiver at http://'
        + str(actual_host)
        + ':'
        + str(actual_port)
        + WEBHOOK_PATH,
        flush=True,
    )
    receiver_thread.start()

    try:
        body, content_type = build_request_body(args.payload, breakage=None)
        webhook_url = (
            'http://'
            + str(actual_host)
            + ':'
            + str(actual_port)
            + WEBHOOK_PATH
        )

        print('2. Sending mock Splunk alert', flush=True)
        status, response_body = send_alert(
            webhook_url,
            body,
            content_type,
            args.timeout,
        )
        print(
            '   receiver HTTP ' + str(status) + ': ' + response_body,
            flush=True,
        )
        if status != 202:
            raise OrchestrationError(
                'receiver rejected the valid sample alert with HTTP '
                + str(status)
            )

        try:
            signal = incidents.get(timeout=args.timeout)
        except Empty as error:
            raise OrchestrationError(
                'receiver acknowledged the alert but did not publish an incident'
            ) from error

        mode_name = 'structured-analysis' if args.analyze else 'display-only'
        print('3. Starting ollama_client.py in ' + mode_name + ' mode', flush=True)
        run_client(
            signal,
            args.analyze,
            max_tool_calls=args.max_tool_calls,
            agent_timeout_seconds=args.agent_timeout_seconds,
        )
    finally:
        server.shutdown()
        server.server_close()
        receiver_thread.join(timeout=args.timeout)
        if receiver_thread.is_alive():
            raise OrchestrationError('receiver thread did not stop')


if __name__ == '__main__':
    try:
        main()
    except FileNotFoundError as error:
        raise SystemExit('error: file not found: ' + str(error.filename))
    except json.JSONDecodeError as error:
        raise SystemExit('error: payload file is not valid JSON: ' + str(error))
    except urllib.error.URLError as error:
        raise SystemExit('error: webhook request failed: ' + str(error.reason))
    except OSError as error:
        raise SystemExit('error: could not start local handoff: ' + str(error))
    except OrchestrationError as error:
        raise SystemExit('error: ' + str(error))
