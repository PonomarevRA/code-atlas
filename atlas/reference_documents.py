"""Нормализация локальных справочников; HTML, XML и исходники остаются данными."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
from pathlib import PurePosixPath
import re
import xml.etree.ElementTree as ET

from .reference_xml import normalize_xrefs, xml_sections, xml_summary

LANGUAGES = ("csharp", "dotnet", "python", "java", "javascript", "go", "docker", "helm",
             "sql", "postgresql", "lancedb", "html", "css", "git", "sh", "bat", "efcore", "ef6")
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_EF_XML_BYTES = 32 * 1024 * 1024
MAX_DOCUMENT_CHARS = 4 * 1024 * 1024


def without_generics(symbol: str) -> str:
    """Нормализует nested generic аргументы, сохраняя namespace и имя метода."""
    result, depth = [], 0
    for char in symbol:
        if char == "<":
            depth += 1
        elif char == ">" and depth:
            depth -= 1
        elif not depth:
            result.append(char)
    return "".join(result)


class ReferenceError(ValueError):
    """Ошибка входного справочника с явным HTTP-статусом."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class _HTMLText(HTMLParser):
    """Извлекает текст и идентификаторы API без исполнения скриптов и загрузки ссылок."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.aliases, self.title_parts = [], [], []
        self.ignored = 0
        self.in_title = False
        self.signature_parts = None

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "nav"}:
            self.ignored += 1
        if tag == "title":
            self.in_title = True
        if not self.ignored:
            attrs = dict(attrs)
            if tag == "dt" and "sig" in attrs.get("class", "").split():
                self.signature_parts = []
            if tag in {"dt", "section", "h2", "h3", "a"}:
                alias = attrs.get("id", attrs.get("name", ""))
                if alias and len(alias) <= 500:
                    self.aliases.append(alias)
            if tag in {"p", "div", "li", "dt", "dd", "pre", "h1", "h2", "h3", "br", "tr"}:
                self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "dt" and self.signature_parts is not None:
            # Python tutorial signatures may omit IDs (e.g. list.append).
            signature = re.sub(r"\s+", "", "".join(self.signature_parts))
            match = re.match(r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\(", signature)
            if match:
                self.aliases.append(match.group(1))
            self.signature_parts = None
        if tag in {"script", "style", "nav"}:
            self.ignored = max(0, self.ignored - 1)
        if tag == "title":
            self.in_title = False
        if tag in {"p", "pre", "li", "dt", "dd", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.signature_parts is not None and not self.ignored:
            self.signature_parts.append(data)
        if self.in_title:
            self.title_parts.append(data)
        elif not self.ignored:
            self.parts.append(data)


def _record(path, title, symbol, text, aliases=(), signature="", summary="") -> dict:
    """Детерминированный ID различает перегрузки и сохраняет исходный путь."""
    if len(text) > MAX_DOCUMENT_CHARS:
        raise ReferenceError(f"Document exceeds text limit: {path}")
    key = path + "\0" + symbol + "\0" + signature
    return {"id": hashlib.sha256(key.encode()).hexdigest()[:32], "path": path,
            "title": title[:500], "symbol": symbol[:500], "signature": signature[:2000],
            "text": text, "summary": summary, "aliases": sorted(set([symbol, without_generics(symbol), *aliases]))}


def _signature(element, language: str, framework: str) -> str:
    """Предпочитает вариант сигнатуры выбранного framework; чужие варианты исключены."""
    fallback = ""
    for signature in element.findall("MemberSignature") + element.findall("TypeSignature"):
        if signature.get("Language") != language:
            continue
        alternate = signature.get("FrameworkAlternate", "")
        if framework and framework in alternate.split(";"):
            return signature.get("Value", "")
        if not alternate or not framework:
            fallback = fallback or signature.get("Value", "")
    return fallback


def file_limit(path: str, language: str) -> int:
    """Монолитные XML библиотек допускают 64 MiB, EF — 32 MiB; страницы остаются ограниченными."""
    if language == "dotnet" and path.startswith("library-xml/") and PurePosixPath(path).suffix.lower() == ".xml":
        return 64 * 1024 * 1024
    if language in {"efcore", "ef6"} and PurePosixPath(path).suffix.lower() == ".xml":
        return MAX_EF_XML_BYTES
    return MAX_FILE_BYTES


def _compiled_members(root, path: str):
    """Сохраняет полные NuGet doc-comments; DocId различает overloads без выдуманной C# сигнатуры."""
    assembly = root.findtext("assembly/name", "")
    for member in root.findall("members/member"):
        identity = member.get("name", "")
        if not re.match(r"^[TMPFE]:", identity):
            continue
        qualified = identity[2:].split("(", 1)[0]
        symbol = re.sub(r"`{1,2}\d+", "", qualified)
        name = symbol.rsplit(".", 1)[-1]
        summary = member.find("summary")
        body = xml_sections(member) + "\n\nAssembly: " + assembly
        yield _record(path, symbol, symbol, body, [identity, qualified, name], identity,
                      xml_summary(summary))


def documents(path: str, content: bytes, language: str, framework: str = ""):
    """Читает весь выбранный файл и выдаёт страницы или .NET API по перегрузкам."""
    maximum = file_limit(path, language)
    if len(content) > maximum:
        raise ReferenceError(f"File exceeds {maximum // 1024 // 1024} MiB: {path}")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ReferenceError(f"Expected UTF-8 documentation: {path}") from None
    suffix = PurePosixPath(path).suffix.lower()
    symbol = PurePosixPath(path).stem
    if path.endswith(".reference.json"):
        # Локальный импорт избегает циклической зависимости проверки/ошибок.
        from .reference_localization import captured_records
        for row in captured_records(text):
            record = _record(row["path"], row["title"], row["symbol"], row["text"],
                             row.get("aliases", []), row["signature"])
            yield {**record, "sourceUrl": row["sourceUrl"]}
    elif suffix in {".html", ".htm"}:
        parser = _HTMLText()
        parser.feed(text)
        body = re.sub(r"\n[ \t]*\n+", "\n\n", "".join(parser.parts)).strip()
        if language in {"sql", "postgresql"} and symbol.startswith("sql-"):
            parser.aliases.append(symbol[4:].replace("-", " ").upper())
        yield _record(path, "".join(parser.title_parts).strip() or symbol, symbol, body, parser.aliases)
    elif suffix == ".xml" and language in {"csharp", "dotnet", "efcore", "ef6"}:
        markup = re.sub(r"<!\[CDATA\[.*?\]\]>|<!--.*?-->", "", text, flags=re.S)
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", markup, re.I):
            raise ReferenceError(f"XML entities are not supported: {path}")
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            raise ReferenceError(f"Invalid XML documentation: {path}") from None
        if root.tag == "doc":
            yield from _compiled_members(root, path)
            return
        if root.tag != "Type":
            return
        symbol = root.get("FullName", symbol)
        versions = sorted({n.text for n in root.findall("AssemblyInfo/AssemblyVersion") if n.text})
        applicability = "Assembly versions: " + ", ".join(versions) + "\n" if versions else ""
        docs = root.find("Docs")
        if docs is not None:
            summary = docs.find("summary")
            yield _record(path, symbol, symbol, xml_sections(docs) + "\n" + applicability,
                          [_signature(root, "DocId", framework)],
                          summary=xml_summary(summary))
        for member in root.findall("Members/Member"):
            name = member.get("MemberName", "")
            docs = member.find("Docs")
            if not name or docs is None:
                continue
            signature = _signature(member, "C#", framework)
            identity = _signature(member, "DocId", framework) or signature
            api = symbol + "." + name
            member_versions = sorted({n.text for n in member.findall("AssemblyInfo/AssemblyVersion") if n.text})
            applies = "Assembly versions: " + ", ".join(member_versions or versions)
            summary = docs.find("summary")
            yield _record(path, api, api, xml_sections(docs) + "\n" + applies, [name, name.split("<")[0], identity], signature or identity,
                          summary=xml_summary(summary))
    elif suffix == ".go":
        # Стандартный Go source archive содержит авторские doc-comments и полный код.
        # Это справочник исходников: не выдаём lexical имя за разрешённую сигнатуру API.
        package = re.search(r"^package\s+(\w+)", text, re.M)
        package_name = package.group(1) if package else symbol
        aliases = []
        for receiver, name in re.findall(r"^func\s+(?:\(([^\n)]+)\)\s+)?([A-Z]\w*)\s*[\[(]", text, re.M):
            owner = package_name
            if receiver:
                # Метод receiver не является свободной функцией package.Method.
                receiver_type = receiver.split()[-1].lstrip("*").split("[", 1)[0]
                owner += "." + receiver_type
            aliases.append(owner + "." + name)
        yield _record(path, path, symbol, text, aliases)
    elif suffix == ".java":
        package = re.search(r"^package\s+([\w.]+)\s*;", text, re.M)
        api = (package.group(1) + "." if package else "") + symbol
        aliases = [api + "." + name for name in re.findall(r"\bpublic\s+(?:(?:static|final|default|synchronized|abstract)\s+)*(?:[\w<>\[\],.?]+\s+)+(\w+)\s*\(", text)]
        yield _record(path, api, api, text, aliases)
    else:
        heading = re.search(r"^#\s+(.+)$", text, re.M)
        front_title = re.search(r"^title:\s*[\"']?([^\n]+)", text, re.M)
        title = heading.group(1) if heading else front_title.group(1).strip("\"'") if front_title else symbol
        aliases = []
        if language in {"javascript", "html", "css"} and "/reference/" in path.lower():
            aliases.append(title.removesuffix("()"))
            parts = path.lower().split("/reference/", 1)[1].split("/")
            if parts[-1] == "index.md":
                parts.pop()
            aliases.append(".".join(parts[1:] if parts and parts[0] == "global_objects" else parts))
            if language in {"html", "css"} and len(parts) >= 2:
                # Свойство display и элемент input ищутся без служебного пути MDN.
                aliases.append(parts[-1])
        elif language == "git" and symbol.startswith("git-"):
            aliases.extend([symbol.replace("-", " ", 1), symbol[4:]])
        elif language == "bat":
            aliases.extend([symbol.removesuffix("_1"), "cmd " + symbol])
        elif language == "lancedb":
            # Явные квалифицированные имена из примеров — кандидаты документации,
            # а не доказательство разрешения вызова в анализируемом проекте.
            aliases.extend(re.findall(r"\b(?:lancedb|LanceDB)\.[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", text))
        if suffix in {".adoc", ".txt"} and language == "git":
            first = text.splitlines()[0].strip() if text.splitlines() else symbol
            title = first or symbol
        if language in {"csharp", "dotnet", "efcore", "ef6"} and suffix == ".md":
            text = normalize_xrefs(text)
        yield _record(path, title, symbol, text, aliases)


def eligible(path: str, language: str) -> bool:
    """Выбирает документационные файлы официальных архивов; код выполняться не будет."""
    lowered = path.lower()
    suffix = PurePosixPath(path).suffix.lower()
    if language == "go":
        return (("/src/" in lowered and suffix == ".go" and not lowered.endswith("_test.go") and "/testdata/" not in lowered)
                or "/doc/" in lowered and suffix in {".html", ".md"})
    if language in {"javascript", "html", "css"}:
        return f"/en-us/web/{language}/" in lowered and suffix == ".md"
    if language in {"csharp", "dotnet"}:
        return (suffix == ".xml" and re.match(r"^[^/]+/xml/", lowered) is not None
                or suffix == ".md" and re.match(r"^[^/]+/(?:docs|proposals)/", lowered) is not None)
    if language in {"efcore", "ef6"}:
        section = "core" if language == "efcore" else "ef6"
        return (f"/entity-framework/{section}/" in lowered and suffix == ".md"
                or lowered.startswith("lib/") and suffix == ".xml")
    if language == "git":
        return "/documentation/" in lowered and suffix in {".adoc", ".txt"}
    if language == "bat":
        return "/windows-commands/" in lowered and suffix == ".md"
    if language == "lancedb":
        return "/docs/" in lowered and suffix in {".md", ".mdx", ".rst", ".html"}
    if language == "docker":
        return (("/content/" in lowered and suffix == ".md")
                or "/_vendor/" in lowered and suffix in {".md", ".yaml", ".yml"})
    if language == "helm":
        return (("/content/en/docs/" in lowered or re.match(r"^[^/]+/(?:docs|versioned_docs)/", lowered))
                and suffix in {".md", ".mdx"})
    if language == "java" and suffix == ".java":
        return "/src/java." in lowered and "/share/classes/" in lowered
    return suffix in {".html", ".htm", ".md", ".rst", ".txt"} and not PurePosixPath(path).name.lower().startswith(("license", "copying"))
