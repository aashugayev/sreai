"""Send a mock Splunk webhook alert to the local receiver.

Stands in for Splunk's webhook alert action during development. The default
run sends the unmodified sample payload. The ``--break`` option corrupts a copy
of that payload in memory, so each receiver rejection path can be exercised
without editing the sample file on disk.
"""

import argparse
import json
from pathlib import Path
import urllib.error
import urllib.request


DEFAULT_URL = 'http://127.0.0.1:8080/webhooks/splunk'
DEFAULT_PAYLOAD = Path(__file__).parent / 'sample_data' / 'splunk_alert.json'

BREAKAGES = {
    'missing-service': 'drop result.service, a field the saved search must emit',
    'missing-result': 'drop the whole result object',
    'result-not-object': 'send result as a list instead of an object',
    'empty-severity': 'blank out result.severity',
    'malformed-json': 'send text that is not valid JSON',
    'wrong-content-type': 'send valid JSON as text/plain',
    'empty-body': 'send a zero-length body',
}


def parse_args():
    """Parse sender command-line options."""
    parser = argparse.ArgumentParser(
        description='Send a mock Splunk alert to the incident receiver.',
        epilog='breakages: ' + '; '.join(
            name + ' - ' + reason for name, reason in BREAKAGES.items()
        ),
    )
    parser.add_argument(
        '-u', '--url',
        default=DEFAULT_URL,
        help='receiver webhook URL (default: ' + DEFAULT_URL + ')',
    )
    parser.add_argument(
        '-f', '--payload',
        default=str(DEFAULT_PAYLOAD),
        metavar='PATH',
        help='alert payload to send (default: sample_data/splunk_alert.json)',
    )
    parser.add_argument(
        '-b', '--break', dest='breakage',
        default=None,
        choices=sorted(BREAKAGES),
        help='corrupt the payload in memory to test a rejection path',
    )
    parser.add_argument(
        '-t', '--timeout',
        default=10.0,
        type=float,
        help='seconds to wait for a response (default: 10)',
    )
    return parser.parse_args()


def build_request_body(payload_path, breakage):
    """Return the body bytes and content type for the chosen scenario.

    The sample file is never modified. Corruption is applied to an in-memory
    copy so the on-disk payload stays a trustworthy reference.
    """
    content_type = 'application/json'

    if breakage == 'malformed-json':
        return b'{"sid": "broken", "result": {', content_type
    if breakage == 'empty-body':
        return b'', content_type

    payload = json.loads(Path(payload_path).read_text(encoding='utf-8'))

    if breakage == 'missing-service':
        payload['result'].pop('service', None)
    elif breakage == 'missing-result':
        payload.pop('result', None)
    elif breakage == 'result-not-object':
        payload['result'] = []
    elif breakage == 'empty-severity':
        payload['result']['severity'] = '   '
    elif breakage == 'wrong-content-type':
        content_type = 'text/plain'

    return json.dumps(payload).encode('utf-8'), content_type


def send_alert(url, body, content_type, timeout):
    """POST one alert and return the status code and decoded response body."""
    request = urllib.request.Request(
        url,
        data=body,
        headers={'Content-Type': content_type},
        method='POST',
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode('utf-8')
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode('utf-8', errors='replace')


def main():
    """Send one mock alert and report how the receiver responded."""
    args = parse_args()

    try:
        body, content_type = build_request_body(args.payload, args.breakage)
    except FileNotFoundError:
        raise SystemExit('error: payload file not found: ' + str(args.payload))
    except json.JSONDecodeError as error:
        raise SystemExit('error: payload file is not valid JSON: ' + str(error))

    scenario = args.breakage or 'valid alert'
    print('POST ' + args.url)
    print('scenario: ' + scenario + ' (' + str(len(body)) + ' bytes, ' + content_type + ')')

    try:
        status, response_body = send_alert(args.url, body, content_type, args.timeout)
    except urllib.error.URLError as error:
        raise SystemExit(
            'error: cannot reach the receiver at ' + args.url + ' ('
            + str(error.reason) + '). Start it with: python splunk_receiver.py'
        )
    except TimeoutError:
        raise SystemExit('error: receiver did not respond within '
                         + str(args.timeout) + 's')

    print('HTTP ' + str(status))
    print(response_body)

    # A broken payload that is accepted means the receiver's validation
    # regressed, so make that visible to scripts and CI.
    if args.breakage and status < 400:
        raise SystemExit('error: receiver accepted a payload it should reject')
    if not args.breakage and status != 202:
        raise SystemExit('error: receiver did not accept a valid alert')


if __name__ == '__main__':
    main()
