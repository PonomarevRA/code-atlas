"""Passive HTML sketch policy shared by capture and snapshot projection."""

HTML_NAMESPACE = 'http://www.w3.org/1999/xhtml'
OMIT_SUBTREES = frozenset({
    'head', 'script', 'style', 'template', 'noscript', 'iframe', 'frame', 'frameset',
    'object', 'embed', 'base', 'link', 'meta', 'source', 'track', 'param',
})
TEXT_LIMIT = 96
DOCUMENT_TEXT_LIMIT = 8192


def previewable(tag, namespace, attrs):
    """Exclude non-HTML, active and explicitly hidden subtrees from the sketch."""
    if namespace not in ('', HTML_NAMESPACE) or tag in OMIT_SUBTREES:
        return False
    values = dict(attrs)
    if 'hidden' in values or str(values.get('aria-hidden', '')).lower() == 'true':
        return False
    return tag != 'input' or str(values.get('type', '')).lower() not in {'hidden', 'password'}


def preview_text(value):
    """Capture short literal text, never markup, URLs as attributes or form values."""
    if not isinstance(value, str):
        return ''
    return ' '.join(value[:TEXT_LIMIT * 4].split())[:TEXT_LIMIT]
