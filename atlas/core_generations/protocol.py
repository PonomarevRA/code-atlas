"""Finite JSON frames for one local compute pipe; no network or source paths."""

import json
import math
import struct

from .artifacts import GenerationError, canonical

MAX_FRAME_BYTES = 8 * 1024 * 1024


def encode_frame(value):
    """Reject non-finite/oversized messages before dispatch or serialization reuse."""
    try:
        data = canonical(value)
    except (ValueError, TypeError, RecursionError):
        raise GenerationError('worker_protocol', 'Expected finite JSON data', 400) from None
    if len(data) > MAX_FRAME_BYTES:
        raise GenerationError('worker_budget', 'Worker message exceeds byte budget', 413)
    return struct.pack('!I', len(data)) + data


def decode_payload(data):
    """Require an object response and reject non-finite extensions to JSON."""
    def invalid_constant(value):
        """Reject JSON extensions such as NaN/Infinity before retaining them."""
        raise ValueError('Nonfinite constant')
    def finite_float(value):
        """Finite syntax may overflow binary64; 1e309 must also be rejected."""
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('Nonfinite number')
        return number
    try:
        value = json.loads(data, parse_constant=invalid_constant, parse_float=finite_float)
    except (ValueError, UnicodeError, RecursionError):
        raise GenerationError('worker_protocol', 'Worker returned invalid JSON') from None
    if not isinstance(value, dict):
        raise GenerationError('worker_protocol', 'Worker message must be an object')
    return value


def read_frame(stream):
    """Read one child-side frame, bounding its allocation before payload read."""
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise GenerationError('worker_protocol', 'Incomplete frame header')
    size = struct.unpack('!I', header)[0]
    if size > MAX_FRAME_BYTES:
        raise GenerationError('worker_budget', 'Worker message exceeds byte budget', 413)
    data = stream.read(size)
    if len(data) != size:
        raise GenerationError('worker_protocol', 'Incomplete frame payload')
    return decode_payload(data)
