"""Bounded opaque selectors with integrity checks; references never grant access."""

import base64
import hashlib
import json

MAX_REFERENCE_CHARS = 8192


def encode_reference(payload: dict) -> str:
    """
    Encode a JSON selector deterministically with a corruption checksum.

    Callers must independently validate scope and permission. Oversized selectors
    raise ValueError instead of returning an unusable continuation.
    """
    data = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
    token = base64.urlsafe_b64encode(data).decode().rstrip('=')
    token += '.' + hashlib.sha256(data).hexdigest()[:24]
    if len(token) > MAX_REFERENCE_CHARS:
        raise ValueError('Continuation metadata exceeds the reference budget')
    return token


def decode_reference(token: str) -> dict:
    """
    Decode one bounded JSON object or raise ValueError for malformed input.

    The checksum detects corruption, not forgery; callers validate every field.
    """
    if not isinstance(token, str) or not 1 <= len(token) <= MAX_REFERENCE_CHARS:
        raise ValueError('Invalid continuation')
    try:
        encoded, checksum = token.split('.')
        data = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
        if hashlib.sha256(data).hexdigest()[:24] != checksum:
            raise ValueError('Invalid continuation checksum')
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError('Continuation must select an object')
        return payload
    except (TypeError, UnicodeError, RecursionError) as error:
        raise ValueError('Invalid continuation') from error
