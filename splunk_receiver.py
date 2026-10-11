"""Receive and normalize a Splunk webhook alert.

Splunk's webhook alert action POSTs a JSON envelope after a saved search
triggers. This receiver validates the fields produced by our planned saved
search and converts the vendor-specific envelope into an internal incident
signal. It deliberately does not call the language model in the request path.
"""

import argparse
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from typing import Any
from urllib.parse import urlsplit


WEBHOOK_PATH = '/webhooks/splunk'
MAX_REQUEST_BYTES = 1024 * 1024


class WebhookValidationError(ValueError):
    """Raised when a webhook body does not match the expected alert contract."""


@dataclass(frozen=True)
class IncidentSignal:
    """Provider-neutral incident signal created from a Splunk alert."""

    incident_id: str
    source: str
    title: str
    occurred_at: str
    service: str
    environment: str
    severity: str
    summary: str
    host: str
    splunk_app: str
    splunk_owner: str
    splunk_results_url: str
    evidence: dict[str, Any]


class SplunkWebhookServer(ThreadingHTTPServer):
    """Webhook server that publishes each accepted incident signal."""

    def __init__(self, server_address, incident_handler=None):
        super().__init__(server_address, SplunkWebhookHandler)
        self.incident_handler = incident_handler

    def publish_incident(self, signal):
        """Deliver a signal to the orchestrator or print it in standalone mode."""
        if self.incident_handler is None:
            print(json.dumps(asdict(signal), indent=2), flush=True)
            return
        self.incident_handler(signal)


def require_object(value, field_name):
    """Return a JSON object or raise a field-specific validation error."""
    if not isinstance(value, dict):
        raise WebhookValidationError(field_name + ' must be a JSON object')
    return value


def require_string(values, field_name, location='webhook'):
    """Return a required, non-empty string field."""
    value = values.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise WebhookValidationError(
            location + '.' + field_name + ' must be a non-empty string'
        )
    return value.strip()


def normalize_splunk_alert(payload):
    """Validate a Splunk webhook envelope and create an incident signal.

    The saved search must emit the result fields used here. Splunk itself adds
    the outer envelope fields such as ``sid`` and ``results_link``.
    """
    payload = require_object(payload, 'webhook')
    result = require_object(payload.get('result'), 'webhook.result')

    return IncidentSignal(
        incident_id=require_string(payload, 'sid'),
        source='splunk',
        title=require_string(payload, 'search_name'),
        occurred_at=require_string(result, '_time', 'webhook.result'),
        service=require_string(result, 'service', 'webhook.result'),
        environment=require_string(result, 'environment', 'webhook.result'),
        severity=require_string(result, 'severity', 'webhook.result'),
        summary=require_string(result, 'message', 'webhook.result'),
        host=require_string(result, 'host', 'webhook.result'),
        splunk_app=require_string(payload, 'app'),
        splunk_owner=require_string(payload, 'owner'),
        splunk_results_url=require_string(payload, 'results_link'),
        evidence=result.copy(),
    )


class SplunkWebhookHandler(BaseHTTPRequestHandler):
    """HTTP handler for the Splunk webhook and a health endpoint."""

    server_version = 'SreAiSplunkReceiver/0.1'

    def do_GET(self):
        """Return receiver health without exposing incident state."""
        if urlsplit(self.path).path != '/health':
            self.send_error(404)
            return
        self.send_json(200, {'status': 'ok'})

    def do_POST(self):
        """Validate, normalize, and acknowledge one Splunk alert."""
        if urlsplit(self.path).path != WEBHOOK_PATH:
            self.send_error(404)
            return

        if self.headers.get_content_type() != 'application/json':
            self.send_json(415, {'error': 'Content-Type must be application/json'})
            return

        content_length = self.headers.get('Content-Length')
        if content_length is None:
            self.send_json(411, {'error': 'Content-Length is required'})
            return

        try:
            body_length = int(content_length)
        except ValueError:
            self.send_json(400, {'error': 'Content-Length must be an integer'})
            return

        if body_length < 1:
            self.send_json(400, {'error': 'request body must not be empty'})
            return
        if body_length > MAX_REQUEST_BYTES:
            self.send_json(413, {'error': 'request body exceeds 1 MiB'})
            return

        try:
            payload = json.loads(self.rfile.read(body_length).decode('utf-8'))
        except UnicodeDecodeError:
            self.send_json(400, {'error': 'request body must be UTF-8'})
            return
        except json.JSONDecodeError as error:
            self.send_json(
                400,
                {'error': 'invalid JSON at character ' + str(error.pos)},
            )
            return

        try:
            signal = normalize_splunk_alert(payload)
        except WebhookValidationError as error:
            self.send_json(400, {'error': str(error)})
            return

        self.server.publish_incident(signal)
        self.send_json(
            202,
            {
                'status': 'accepted',
                'incident_id': signal.incident_id,
            },
        )

    def send_json(self, status, payload):
        """Send one JSON response."""
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, message_format, *args):
        """Use a concise access-log format."""
        print(
            self.address_string() + ' - ' + (message_format % args),
            flush=True,
        )


def parse_args():
    """Parse receiver command-line options."""
    parser = argparse.ArgumentParser(
        description='Receive and normalize Splunk webhook alerts.',
    )
    parser.add_argument(
        '--host',
        default='127.0.0.1',
        help='interface to bind (default: 127.0.0.1)',
    )
    parser.add_argument(
        '--port',
        default=8080,
        type=int,
        help='port to bind (default: 8080)',
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='handle one HTTP request and exit (useful for local testing)',
    )
    return parser.parse_args()


def main():
    """Run the webhook receiver."""
    args = parse_args()
    server = SplunkWebhookServer((args.host, args.port))
    print(
        'Listening on http://'
        + args.host
        + ':'
        + str(args.port)
        + WEBHOOK_PATH,
        flush=True,
    )

    try:
        if args.once:
            server.handle_request()
        else:
            server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopping receiver.', flush=True)
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
