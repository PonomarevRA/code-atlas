"""One native-messaging request forwards a bounded capture to the fixed loopback Atlas."""
import argparse
from http.client import HTTPException
import math
import json
import re
import struct
import sys
from urllib.error import URLError, HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_MESSAGE_BYTES = 8 * 1024 * 1024
FRAME = struct.Struct('=I')
HOST_NAME = 'com.code_atlas.browser_capture'


class NoRedirect(HTTPRedirectHandler):
    """A loopback bridge must never forward a captured page to a redirect destination."""
    def redirect_request(self, request, file, code, message, headers, new_url):
        """Decline every redirect, including another loopback port."""
        return None


def read_exact(stream, count):
    """Read one bounded frame segment, rejecting truncated pipes without logging bytes."""
    chunks, remaining = [], count
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise ValueError('truncated-native-frame')
        chunks.append(chunk)
        remaining -= len(chunk)
    return b''.join(chunks)


def read_frame(stream):
    """Decode native-endian length plus UTF-8 JSON with an independent byte ceiling."""
    length = FRAME.unpack(read_exact(stream, FRAME.size))[0]
    if not 0 < length <= MAX_MESSAGE_BYTES:
        raise ValueError('native-frame-limit')
    result = json.loads(read_exact(stream, length))
    if not isinstance(result, dict):
        raise ValueError('invalid-native-message')
    return result


def write_frame(stream, value):
    """Only a small metadata reply or stable error enters Chrome's standard-output channel."""
    encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(encoded) > 64_000:
        raise ValueError('native-reply-limit')
    stream.write(FRAME.pack(len(encoded)) + encoded)
    stream.flush()


def forward_capture(payload, port):
    """Preserve the original capture-only bridge entry point for existing clients."""
    return forward_message(payload, port)


def forward_message(payload, port):
    """Allow only capture or exact-ID status/finish operations on the fixed local service."""
    operation = payload.get('operation', 'capture')
    headers = {'Content-Type': 'application/json'}
    if operation in {'privacy-status', 'privacy-configure'}:
        endpoint = '/api/browser-privacy'
        body, method = None, 'GET'
        if operation == 'privacy-status':
            if set(payload) != {'operation'}:
                raise ValueError('invalid-native-message')
        else:
            if (set(payload) != {'operation', 'mode', 'confirmed'} or payload['confirmed'] is not True
                    or payload['mode'] not in {'strict', 'credentials-only', 'off'}):
                raise ValueError('invalid-native-message')
            from .browser_human_auth import confirm_privacy_change, HumanConfirmationRequired
            if payload['mode'] != 'strict' and not confirm_privacy_change():
                raise HumanConfirmationRequired('human-confirmation-required')
            from .runtime_paths import default_data_dir
            from .browser_privacy_files import private_read
            token = private_read(default_data_dir() / 'browser-privacy/control.key', 32)
            if len(token) != 32:
                raise ValueError('native-control-unavailable')
            headers['X-Atlas-Native-Control'] = token.hex()
            body, method = {'mode': payload['mode'], 'confirmed': True}, 'POST'
    elif operation == 'capture':
        if set(payload) - {'operation', 'capture', 'retention', 'ttlSeconds'} or 'capture' not in payload:
            raise ValueError('invalid-native-message')
        body = {key: value for key, value in payload.items() if key != 'operation'}
        endpoint, method = '/api/browser-sessions', 'POST'
    elif operation == 'health':
        if set(payload) != {'operation'}:
            raise ValueError('invalid-native-message')
        body, method, endpoint = None, 'GET', '/api/browser-sessions'
    elif operation in {'status', 'finish'}:
        if set(payload) != {'operation', 'sessionId'} or re.fullmatch('[a-f0-9]{32}', str(payload['sessionId'])) is None:
            raise ValueError('invalid-native-message')
        endpoint = '/api/browser-sessions/' + payload['sessionId']
        body, method = None, 'GET'
        if operation == 'finish':
            endpoint += '/finish'
            body, method = {'reason': 'completed'}, 'POST'
    else:
        raise ValueError('invalid-native-message')
    encoded = None if body is None else json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if encoded is not None and len(encoded) > MAX_MESSAGE_BYTES:
        raise ValueError('native-frame-limit')
    request = Request(f'http://127.0.0.1:{port}' + endpoint, data=encoded,
                      headers=headers, method=method)
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=20) as response:
        content = response.read(64_001)
    if len(content) > 64_000:
        raise ValueError('native-reply-limit')
    result = json.loads(content)
    if operation in {'privacy-status', 'privacy-configure'}:
        if (not isinstance(result, dict) or result.get('mode') not in {'strict', 'credentials-only', 'off', 'blocked'}
                or type(result.get('available')) is not bool):
            raise ValueError('invalid-atlas-reply')
        return {'mode': result['mode'], 'available': result['available']}
    if operation == 'health':
        if not isinstance(result, dict) or not isinstance(result.get('sessions'), list):
            raise ValueError('invalid-atlas-reply')
        return {'available': True, 'protocol': 'atlas-browser-agent-v1'}
    if not isinstance(result, dict) or re.fullmatch('[a-f0-9]{32}', str(result.get('sessionId', ''))) is None:
        raise ValueError('invalid-atlas-reply')
    if operation != 'capture' and result['sessionId'] != payload['sessionId']:
        raise ValueError('invalid-atlas-reply')
    if operation == 'finish':
        if result.get('deleted') is not True:
            raise ValueError('invalid-atlas-reply')
        return {'sessionId': result['sessionId'], 'deleted': True}
    if (result.get('retention') not in {'task', 'hour'}
            or any(type(result.get(key)) is not int or result[key] < 0 for key in ('nodeCount', 'edgeCount'))
            or type(result.get('expiresAt')) not in {int, float} or not math.isfinite(result['expiresAt'])):
        raise ValueError('invalid-atlas-reply')
    # Do not echo page titles, URLs, DOM labels or source into extension state.
    metadata = {key: result[key] for key in ('sessionId', 'expiresAt', 'retention', 'nodeCount', 'edgeCount')}
    privacy = result.get('privacy')
    if isinstance(privacy, dict) and privacy.get('mode') in {'strict', 'credentials-only', 'off'}:
        metadata['privacy'] = {'mode': privacy['mode'], 'masked': privacy.get('masked') is True,
                               'mustDiscloseMasking': privacy.get('mustDiscloseMasking') is True}
    return metadata


def public_error(code, retryable=False, action='check-setup'):
    """Return allowlisted recovery facts without forwarding exception or remote response text."""
    return {'error': {'code': code, 'retryable': retryable, 'recoveryAction': action}}


def main(argv=None):
    """Chrome supplies its origin; the installed launcher pins the only allowed extension ID."""
    parser = argparse.ArgumentParser(description='Code Atlas browser native bridge')
    parser.add_argument('--extension-id', required=True)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('origin')
    args = parser.parse_args(argv)
    from .browser_human_auth import HumanConfirmationRequired
    result = public_error('native-request-rejected')
    try:
        if re.fullmatch('[a-p]{32}', args.extension_id) is None or not 1024 <= args.port <= 65535:
            raise ValueError('invalid-host-configuration')
        if args.origin != 'chrome-extension://' + args.extension_id + '/':
            raise ValueError('extension-origin-rejected')
        result = forward_message(read_frame(sys.stdin.buffer), args.port)
    except HumanConfirmationRequired:
        result = public_error('human-confirmation-required', False, 'confirm-in-system-dialog')
    except HTTPError as error:
        code, retryable, action = {
            404: ('browser-item-unavailable', False, 'capture-again'),
            410: ('browser-item-unavailable', False, 'capture-again'),
            429: ('browser-capacity', True, 'finish-owned-session'),
            503: ('browser-busy', True, 'retry-later'),
            424: ('browser-privacy-unavailable', False, 'check-privacy-settings'),
            403: ('native-control-required', False, 'check-privacy-settings'),
            422: ('invalid-capture', False, 'capture-again'),
            413: ('capture-too-large', False, 'use-smaller-page'),
        }.get(error.code, ('atlas-request-rejected', False, 'check-setup'))
        result = public_error(code, retryable, action)
    except (URLError, TimeoutError, ConnectionError):
        result = public_error('atlas-unavailable', True, 'start-atlas')
    except (ValueError, TypeError, KeyError, OSError, HTTPException, RecursionError):
        # Exceptions can contain input fragments or HTTP payloads. Publish a fixed code only.
        pass
    write_frame(sys.stdout.buffer, result)


if __name__ == '__main__':
    main()
