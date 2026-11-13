"""Validate and render the user-facing section for one stable native release."""
from pathlib import Path
import re

_MAX_BULLET_CHARS = 240
_SECTIONS = ('Русский', 'English')


def release_changelog(path: Path, version: str) -> str:
    """Return one bilingual changelog section with bounded, plain-text bullets."""
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Expected a stable numeric application version')
    with path.open(encoding='utf-8') as source:
        text = source.read(128_001)
    if len(text) > 128_000:
        raise ValueError('Changelog exceeds the size limit')
    sections = re.split(r'^## \[([^\]\n]+)\][ \t]*$', text, flags=re.MULTILINE)
    matches = [body.strip() for tag, body in zip(sections[1::2], sections[2::2]) if tag == version]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one changelog section for {version}')
    content = re.split(r'^### (.+)$', matches[0], flags=re.MULTILINE)
    if content[0].strip() or tuple(content[1::2]) != _SECTIONS:
        raise ValueError('Changelog requires Russian and English headings in order')
    for label, body in zip(content[1::2], content[2::2]):
        lines = [line for line in body.splitlines() if line.strip()]
        if not 1 <= len(lines) <= 5:
            raise ValueError(f'Expected one to five user-facing changes in {label}')
        for line in lines:
            if (not line.startswith('- ') or not 1 <= len(line[2:].strip()) <= _MAX_BULLET_CHARS
                    or any(marker in line for marker in ('<', '>', '`', '://', ']('))
                    or not line.isprintable()):
                raise ValueError(f'Invalid changelog bullet in {label}')
    return f'## [{version}]\n\n{matches[0]}\n'
