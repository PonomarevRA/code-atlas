"""Снимок опубликованного перевода Microsoft Learn, без сетевых вызовов из Atlas."""
from html.parser import HTMLParser
from html import unescape
import re
from urllib.parse import urlsplit
import urllib.request
from urllib.error import HTTPError

from atlas.reference_documents import MAX_FILE_BYTES, ReferenceError


class LearnRateLimit(ReferenceError):
    """Сервис просит прекратить загрузку; текущая ручная сессия не повторяет запросы."""


class LearnPage(HTMLParser):
    """Читает только основной content и выбранные moniker-блоки; навигация исключена."""

    def __init__(self, moniker=""):
        super().__init__(convert_charrefs=True)
        self.moniker = moniker
        self.meta = {}
        self.stack = []
        self.blocks = []
        self.active = None
        self.summary = []

    def handle_starttag(self, tag, attrs):
        """Учитывает закрытие void-тегов и исключает чужие версии API целиком."""
        attrs = dict(attrs)
        if tag == "meta":
            self.meta.setdefault(attrs.get("name", ""), []).append(attrs.get("content", ""))
        if tag in {"meta", "link", "img", "br", "hr", "input", "wbr", "source"}:
            if tag in {"br", "hr"} and self.active is not None:
                self.active.append("\n")
            return
        blocked = bool(self.stack and self.stack[-1][1])
        if self.moniker and attrs.get("data-moniker"):
            blocked |= self.moniker not in attrs["data-moniker"].split()
        blocked |= tag in {"script", "style", "nav"}
        starts = tag == "div" and "content" in attrs.get("class", "").split() and self.active is None
        if starts:
            self.active = []
        self.stack.append((tag, blocked, starts))
        if self.active is not None and not blocked and tag in {"p", "div", "li", "pre", "h1", "h2", "h3", "h4", "tr", "dt", "dd"}:
            self.active.append("\n")

    def handle_endtag(self, tag):
        """Закрывает подходящий контейнер; незакрытые служебные теги не смешивают страницы."""
        position = next((i for i in range(len(self.stack)-1, -1, -1) if self.stack[i][0] == tag), None)
        if position is None:
            return
        closed = self.stack[position:]
        self.stack = self.stack[:position]
        if self.active is not None and tag in {"p", "div", "li", "pre", "h1", "h2", "h3", "h4", "tr", "dd"}:
            self.active.append("\n")
        if any(item[2] for item in closed):
            self.blocks.append("".join(self.active))
            self.active = None

    def handle_data(self, data):
        """Сохраняет текст и примеры, не исполняя HTML или JavaScript."""
        if self.active is not None and not (self.stack and self.stack[-1][1]):
            self.active.append(data)

    def russian(self, expected_uid="") -> dict:
        """RU-метка недостаточна: проверяет русский prose, UID и наличие выбранной версии."""
        if not any(value.lower() == "ru-ru" for value in self.meta.get("locale", [])):
            raise ReferenceError("Source did not provide a Russian locale")
        if self.moniker and self.moniker not in self.meta.get("monikers", []):
            raise ReferenceError("Source does not cover requested API version")
        uid = self.meta.get("uid", [""])[0].rstrip("*")
        normalized_uid = re.sub(r"`\d+", "", uid).casefold()
        if expected_uid and normalized_uid != expected_uid.casefold():
            raise ReferenceError("Source API UID does not match")
        body = max(self.blocks, key=len, default="").strip()
        if not body:
            raise ReferenceError("Source article body unavailable; retain English")
        # Learn metadata может повторно кодировать <T>; это текст, не HTML-render.
        description = unescape(self.meta.get("description", [""])[0])
        prose = description if expected_uid else body
        if len(re.findall(r"[А-Яа-яЁё]", prose)) < (8 if expected_uid else 80):
            raise ReferenceError("Russian description unavailable; retain English")
        body = re.sub(r"\n(?:[ \t]*\n)+", "\n\n", body)
        if expected_uid:
            body = description + "\n\n" + body
        return {"text": body, "translationType": self.meta.get("ms.translationtype", ["unspecified"])[0]}


def fetch_page(url: str, cache, moniker="", expected_uid="", offline=False) -> dict:
    """Явно загружает ограниченную Learn-страницу; повтор использует локальный HTML."""
    import hashlib
    key = hashlib.sha256(url.encode()).hexdigest()
    path = cache / (key + ".html")
    if not path.exists():
        if offline:
            raise ReferenceError("Page not captured; retaining English without network")
        try:
            with urllib.request.urlopen(url, timeout=25) as response:
                if urlsplit(response.url).hostname != "learn.microsoft.com":
                    raise ReferenceError("Unexpected documentation redirect")
                content = response.read(MAX_FILE_BYTES + 1)
        except HTTPError as error:
            if error.code == 429:
                raise LearnRateLimit("HTTP 429: defer remaining network until a later manual request") from error
            raise
        if len(content) > MAX_FILE_BYTES:
            raise ReferenceError("Learn page exceeds 8 MiB")
        temporary = path.with_suffix(".part")
        temporary.write_bytes(content)
        temporary.replace(path)
    parser = LearnPage(moniker)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ReferenceError("Cached Learn page exceeds 8 MiB")
    parser.feed(path.read_text(encoding="utf-8-sig"))
    return parser.russian(expected_uid)
