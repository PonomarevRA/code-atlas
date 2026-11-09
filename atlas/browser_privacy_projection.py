"""Bounded product schema projection; arbitrary page values cross the native privacy engine."""
from datetime import datetime, timezone
import hashlib
import json
import time
from .browser_models import BrowserCapture
from .browser_privacy_files import PrivacyFailure

# Only fixed technical vocabulary is exempt from strict replacement, never user-supplied public values.
HTML_TAGS = frozenset('html head body main section article aside nav header footer div span p a button input label form select option textarea table thead tbody tfoot tr th td ul ol li h1 h2 h3 h4 h5 h6 img svg path canvas script style link meta title br hr strong em small pre code dl dt dd details summary dialog iframe'.split())


def protect_capture(capture, module):
    """Mask whole values without chunking secrets; generate graph IDs independently of captured IDs."""
    began = time.monotonic()
    result = capture.model_dump()
    if module.mode == 'off':
        return capture.model_copy(deep=True), False
    tasks = []
    # Batch values only. Positional association is transient and never an original-to-mask map on disk.
    def add(value, assign):
        """Keep one indivisible value; strict oversized values become keyed aliases of a digest."""
        encoded = json.dumps(value, ensure_ascii=False).encode('utf-8')
        if len(encoded) > 220_000:
            if module.mode != 'strict':
                raise PrivacyFailure('browser-privacy-unavailable')
            value = 'oversized:' + hashlib.sha256(encoded).hexdigest()
        tasks.append((value, assign))
    page = result['page']
    add(page['title'], lambda value: page.__setitem__('title', value))
    if module.mode == 'strict':
        add(page['url'], lambda value: page.__setitem__('url', 'https://redacted.invalid/' + value))
        page['capturedAt'] = datetime.now(timezone.utc).isoformat()
    else:
        add(page['url'], lambda value: page.__setitem__('url', value))
    identifiers = {node['id']: 'n' + str(i) for i, node in enumerate(result['nodes'])}
    for node in result['nodes']:
        node['id'] = identifiers[node['id']]
        node['parentId'] = identifiers.get(node['parentId'])
        if node['tag'].lower() not in HTML_TAGS:
            node['tag'] = 'span'
        for field in ('text', 'attributes', 'styles'):
            add(node[field], lambda value, node=node, field=field: node.__setitem__(field, value))
    for i, script in enumerate(result['scripts']):
        script['id'] = 's' + str(i)
        add(script['url'], lambda value, script=script: script.__setitem__('url', value))
        if script['source'] is not None:
            if module.mode != 'strict' and len(script['source'].encode('utf-8')) > 96_000:
                script.update(source=None, status='oversized')
            else:
                add(script['source'], lambda value, script=script: script.__setitem__('source', '/* ' + value.replace('*/', '* /') + ' */' if module.mode == 'strict' else value))
    # Untrusted warnings can contain page values; publish generic coverage only.
    result['warnings'] = ['browser-capture-incomplete'] if result['warnings'] else []
    if module.mode == 'strict':
        result['warnings'].append('privacy-protected-context-source-analysis-limited')
    elif any(script['status'] == 'oversized' for script in result['scripts']):
        result['warnings'].append('privacy-whole-source-budget')
    masked = False
    batch, assignments, size = [], [], 2
    def flush():
        """Validate full native batches; exceeding the whole-capture deadline releases no partial graph."""
        nonlocal masked, batch, assignments, size
        if not batch:
            return
        if time.monotonic() - began > 6:
            raise PrivacyFailure('browser-privacy-unavailable')
        cleaned = module.sanitize_json(json.dumps(batch, ensure_ascii=False, separators=(',', ':')))
        if time.monotonic() - began > 6:
            raise PrivacyFailure('browser-privacy-unavailable')
        values = cleaned.get('Data')
        if not isinstance(values, list) or len(values) != len(assignments):
            raise PrivacyFailure('browser-privacy-unavailable')
        masked = masked or cleaned.get('OriginalReplaced') is True
        for value, assign in zip(values, assignments):
            assign(value)
        batch, assignments, size = [], [], 2
    for value, assign in tasks:
        encoded_size = len(json.dumps(value, ensure_ascii=False).encode('utf-8')) + 1
        if size + encoded_size > 240_000 or len(batch) >= 128:
            flush()
        batch.append(value)
        assignments.append(assign)
        size += encoded_size
    flush()
    return BrowserCapture.model_validate(result), masked
