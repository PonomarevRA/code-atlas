"""Проверки клиентского выбора связанных репозиториев без npm/browser зависимостей."""
import shutil
import subprocess
from pathlib import Path
import pytest


def test_related_repositories_frontend():
    """Завершение фоновой индексации прикрепляет к исходному primary после смены экрана."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для frontend проверки нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_related_repositories.mjs'))], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'primary identity PASS' in result.stdout


def test_federation_projection_frontend():
    """Проверяет namespace, отдельную область primary-фильтров и устаревший состав федерации."""
    node = shutil.which('node')
    if node is None:
        pytest.skip('Для frontend проверки нужен Node.js')
    result = subprocess.run([node, str(Path(__file__).with_name('test_federation_projection.mjs'))], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'explicit drilldown PASS' in result.stdout


def test_related_management_is_collapsed_without_hiding_status():
    """Дополнительная форма закрыта по умолчанию, поиск и сообщение доступны без раскрытия."""
    from html.parser import HTMLParser
    class Structure(HTMLParser):
        """Отмечает положение нужных элементов относительно related details."""
        def __init__(self):
            super().__init__()
            self.inside=False
            self.closed_default=False
            self.status_outside=False
            self.search_before=False
            self.seen_related=False
        def handle_starttag(self,tag,attributes):
            attrs=dict(attributes)
            if tag=='details' and attrs.get('class')=='related-repositories':
                self.inside=True
                self.seen_related=True
                self.closed_default='open' not in attrs
            if attrs.get('id')=='system-search':
                self.search_before=not self.seen_related
            if attrs.get('id')=='related-status':
                self.status_outside=not self.inside
        def handle_endtag(self,tag):
            if tag=='details':
                self.inside=False
    parser=Structure()
    parser.feed((Path(__file__).parent.parent/'web/index.html').read_text())
    assert parser.closed_default
    assert parser.status_outside
    assert parser.search_before
