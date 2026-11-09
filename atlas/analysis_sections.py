"""Lossless bounded navigation of selected description fields as a JSON tree."""

from copy import deepcopy
import json

from .continuation import decode_reference, encode_reference
from .analysis_contexts import AnalysisContextError

MAX_PAGE_BYTES = 24_000


def section_page(value, scope: dict, reference=None, limit=40):
    """
    Project direct children and child selectors without dumping nested detail.

    Small values remain exact; larger containers/strings expose detail references.
    References bind context, node, field and source revision. A page never skips
    an item; unrepresentable identity metadata raises 413 instead of stalling.
    """
    if type(limit) is not int or not 1 <= limit <= 64:
        raise AnalysisContextError('Section limit must be 1..64', 400)
    path, offset = [], 0
    if reference is not None:
        try:
            selector = decode_reference(reference)
            if set(selector) != {'scope', 'path', 'offset'}:
                raise ValueError('Invalid selector shape')
            path, offset = selector['path'], selector['offset']
            if not isinstance(path, list) or len(path) > 64 or type(offset) is not int or offset < 0:
                raise ValueError('Invalid selector position')
            if any(type(part) not in (str, int) for part in path):
                raise ValueError('Invalid selector path')
        except ValueError as error:
            raise AnalysisContextError('Invalid section reference', 400) from error
        if selector['scope'] != scope:
            raise AnalysisContextError('Section source or scope changed; restart the section', 409)
    current = value
    try:
        for part in path:
            if isinstance(current, dict) and isinstance(part, str):
                current = current[part]
            elif isinstance(current, list) and type(part) is int and 0 <= part < len(current):
                current = current[part]
            else:
                raise KeyError(part)
    except (KeyError, IndexError, TypeError) as error:
        raise AnalysisContextError('Section path is unavailable', 409) from error
    if isinstance(current, dict):
        entries = list(current.items())
    elif isinstance(current, list):
        entries = list(enumerate(current))
    elif isinstance(current, str):
        entries = [(index, current[index:index + 1000]) for index in range(0, len(current), 1000)]
    else:
        entries = [(None, current)]
    if offset > len(entries):
        raise AnalysisContextError('Section offset is unavailable', 409)
    result = {'section': scope['section'], 'sourceHash': scope['sourceHash'], 'items': [],
              'snapshotId': scope['snapshotId'], 'nodeId': scope['nodeId'],
              'contextId': scope['contextId'], 'containerKind': type(current).__name__,
              'total': len(entries), 'pageOffset': offset, 'pageHasMore': False, 'nextRef': None,
              'analysisKind': 'description-section', 'runtimeStatus': 'unknown'}
    for key, item in entries[offset:offset + limit]:
        item_path = path + [key] if isinstance(current, (dict, list)) else path
        expandable = isinstance(item, (dict, list)) or isinstance(item, str) and len(item) > 1000
        descriptor = {'key': key, 'kind': type(item).__name__, 'hasChildren': expandable}
        if expandable:
            descriptor['ref'] = encode_reference({'scope': scope, 'path': item_path, 'offset': 0})
            descriptor['size'] = len(item)
            if isinstance(item, dict):
                descriptor['summary'] = {name: item[name] for name in ('kind', 'label', 'line', 'status', 'truncated')
                                         if name in item and isinstance(item[name], (str, int, bool, type(None)))}
                for name, text in list(descriptor['summary'].items()):
                    if isinstance(text, str):
                        descriptor['summary'][name] = text[:200]
        else:
            descriptor['value'] = deepcopy(item)
        following = offset + len(result['items']) + 1
        following_ref = encode_reference({'scope': scope, 'path': path, 'offset': following}) if following < len(entries) else None
        candidate = {**result, 'items': result['items'] + [descriptor],
                     'pageHasMore': following_ref is not None, 'nextRef': following_ref}
        if len(json.dumps(candidate, ensure_ascii=False).encode()) > MAX_PAGE_BYTES:
            break
        result = candidate
    if offset < len(entries) and not result['items']:
        raise AnalysisContextError('Section metadata exceeds the response budget', 413)
    return result
