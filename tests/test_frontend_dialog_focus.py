"""Контракт модального фокуса и независимого сворачивания поиска."""
from pathlib import Path
from html.parser import HTMLParser
import shutil
import subprocess

import pytest


def test_dialog_focus_and_discovery():
    """Tab, Escape, прежний inert, повторное открытие и сохранение поискового запроса."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Для проверки frontend нужен Node.js")
    result = subprocess.run([node, str(Path(__file__).with_name("test_dialog_focus.mjs"))],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "Dialog focus and discovery shell PASS" in result.stdout


def test_inline_search_markup_uses_original_controls_once():
    """Все запросы находятся в toolbar, а внешний submit принадлежит единственной semantic форме."""
    class Controls(HTMLParser):
        """Собирает controls и их ancestors без запуска браузера или JavaScript."""
        def __init__(self):
            super().__init__()
            self.stack = []
            self.controls = {}

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            identifier = values.get("id")
            if identifier:
                assert identifier not in self.controls, f"Duplicate control ID: {identifier}"
                self.controls[identifier] = (values, list(self.stack))
            if tag not in {"input", "meta", "link", "br", "hr", "img"}:
                self.stack.append((tag, identifier))

        def handle_endtag(self, tag):
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break

    parser = Controls()
    parser.feed((Path(__file__).parent.parent / "web" / "index.html").read_text())
    for identifier in ["navigation-search", "semantic-query"]:
        attrs, ancestors = parser.controls[identifier]
        assert ("div", "discovery-fields") in ancestors
        assert ("div", "workspace-discovery") not in ancestors
        assert attrs["aria-controls"] == "workspace-discovery"
    entry_attrs, entry_ancestors = parser.controls['ingress-catalog-search']
    assert ('dialog', 'ingress-catalog-dialog') in entry_ancestors
    assert ('div', 'discovery-fields') not in entry_ancestors
    assert entry_attrs['aria-controls'] == 'ingress-catalog-list'
    assert parser.controls["semantic-query"][0]["form"] == "semantic-query-form"
    assert parser.controls["semantic-search-submit"][0]["form"] == "semantic-query-form"
    assert "discovery-toggle" not in parser.controls
