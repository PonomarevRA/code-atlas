"""Декларативная wiki модов: каталог метаданных и отдельное безопасное чтение текста."""
from hashlib import sha256

from .mod_store import relative_asset
from .source_viewer import SourceError, _read_regular_file

MAX_PAGE_BYTES = 64 * 1024


class ModWikiError(ValueError):
    """Ошибка чтения wiki содержит HTTP-статус и не влияет на активацию мода."""

    def __init__(self, message: str, status: int = 400):
        """
        Сохраняет сообщение ошибки чтения и HTTP-статус для API.

        message — безопасное сообщение без абсолютных путей; status — код ответа,
        по умолчанию 400. Возвращаемого значения нет.
        """
        super().__init__(message)
        self.status = status


def wiki_catalog(mods) -> dict:
    """
    Выдаёт декларации страниц без декодирования Markdown и исполнения кода.

    mods — локальный ModStore. Возвращает apiVersion, mods и truncated; каждый
    мод содержит текущий fingerprint и метаданные объявленных страниц.
    Обычное обнаружение пакетов внутри ModStore всё ещё хеширует байты ресурсов.
    """
    catalog = mods.catalog()
    items = []
    for item in catalog['mods']:
        manifest = item.get('manifest', {})
        wiki = manifest.get('wiki')
        if not wiki:
            continue
        record = {key: item[key] for key in ('id', 'fingerprint', 'origin', 'enabled', 'status')}
        record.update(name=manifest['name'], version=manifest['version'],
                      section=wiki['section'], pages=wiki['pages'])
        items.append(record)
    return {'apiVersion': 1, 'mods': items, 'truncated': catalog['truncated']}


def read_wiki_page(mods, identifier: str, page_id: str, fingerprint: str | None = None) -> dict:
    """
    Читает объявленный UTF-8 Markdown до 64 КиБ, включая выключенные моды.

    mods — локальный ModStore; identifier и page_id — точные ID мода и страницы.
    fingerprint — необязательная ревизия из каталога, None выбирает текущую.
    Возвращает текст Markdown, идентификаторы, заголовок, раздел и источник.
    ModWikiError означает неизвестный ресурс (404), смену ревизии (409) или
    небезопасный, слишком большой либо не-UTF-8 ресурс (400).
    Fingerprint каталога и хеш ресурса связывают ответ с одной ревизией пакета.
    Безопасный reader запрещает symlink в любом сегменте и неблокирующе отвергает FIFO.
    """
    with mods.lock:
        item = next((item for item in mods.catalog(internal=True)['mods']
                     if item['id'] == identifier and 'manifest' in item), None)
        if item is None:
            raise ModWikiError('Mod wiki not found', 404)
        if fingerprint is not None and fingerprint != item['fingerprint']:
            raise ModWikiError('Mod changed; reload wiki catalog', 409)
        wiki = item['manifest'].get('wiki')
        page = next((page for page in wiki['pages'] if page['id'] == page_id), None) if wiki else None
        if page is None:
            raise ModWikiError('Mod wiki page not found', 404)
        path = page['path']
        asset = item['assets'].get(path)
        if asset is None:
            raise ModWikiError('Declared wiki resource is missing')
        try:
            content = _read_regular_file(item['root'], relative_asset(path), MAX_PAGE_BYTES)
        except SourceError as error:
            raise ModWikiError('Wiki resource is unavailable or exceeds 64 KiB') from error
        if sha256(content).hexdigest() != asset['hash']:
            raise ModWikiError('Mod changed during read; reload wiki catalog', 409)
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError as error:
            raise ModWikiError('Wiki resource must be UTF-8 Markdown') from error
        if any(byte < 32 and byte not in {9, 10, 13} for byte in content):
            raise ModWikiError('Wiki resource contains binary control characters')
        current = next((candidate for candidate in mods.catalog(internal=True)['mods']
                        if candidate['id'] == identifier), None)
        if current is None or current.get('fingerprint') != item['fingerprint']:
            raise ModWikiError('Mod changed during read; reload wiki catalog', 409)
        return {'modId': identifier, 'pageId': page_id, 'title': page['title'],
                'section': wiki['section'], 'text': text, 'format': 'markdown',
                'fingerprint': item['fingerprint'],
                'source': {'origin': item['origin'], 'path': path}}
