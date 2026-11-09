"""Контрактные проверки activity workspace запускаются обычным pytest."""

from pathlib import Path
from html.parser import HTMLParser
import json
import shutil
import subprocess

import pytest


class _MarkupTree(HTMLParser):
    """Retain current HTML topology for identity wiring; browser layout stays a separate UX check."""

    _VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__()
        self.root = {'tag': 'root', 'attrs': [], 'children': []}
        self.stack = [self.root]
        self.body = None

    def handle_starttag(self, tag, attrs):
        """Keep actual attributes and nested controls without evaluating markup or scripts."""
        node = {'tag': tag, 'attrs': attrs, 'children': []}
        self.stack[-1]['children'].append(node)
        if tag == 'body':
            self.body = node
        if tag not in self._VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        """Ignore unmatched closing tags and return to their nearest matching ancestor."""
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1]['children'].append(data)


def test_activity_workspace_contracts(tmp_path):
    """Проверяет независимость карты, поиск по retained карточкам и границы Ctrl/Cmd+F."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для проверки frontend нужен Node.js')
    script = Path(__file__).with_suffix('.mjs')
    parser = _MarkupTree()
    parser.feed((script.parent.parent / 'web/index.html').read_text())
    assert parser.body is not None
    topology = tmp_path / 'workspace-markup.json'
    topology.write_text(json.dumps(parser.body))
    result = subprocess.run([node, str(script), str(topology)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'Activity workspace local Map Find contracts PASS' in result.stdout
