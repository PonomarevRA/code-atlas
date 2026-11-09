"""Явный импорт найденных RU-переводов; непереведённые документы остаются английскими."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import sys
from threading import Event
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from atlas.reference_documents import documents, ReferenceError
from atlas.reference_store import ReferenceStore, connect, import_archive
from scripts.reference_downloads import archive_for
from scripts.reference_learn import fetch_page, LearnRateLimit

API_TYPES = ("Microsoft.EntityFrameworkCore.EntityFrameworkQueryableExtensions.",
             "Microsoft.EntityFrameworkCore.RelationalQueryableExtensions.",
             "Microsoft.EntityFrameworkCore.RelationalDatabaseFacadeExtensions.",
             "Microsoft.EntityFrameworkCore.DbContext.", "Microsoft.EntityFrameworkCore.DbSet.",
             "System.Data.Entity.QueryableExtensions.", "System.Data.Entity.DbContext.",
             "System.Linq.Enumerable.", "System.Linq.Queryable.")


def prepared_archive(source, args):
    """Режим cache-only запрещает сеть также для архивов MDN/Python/Helm."""
    if args.offline_cache_only:
        key = source.get("cacheKey", source["language"] + "-" + source["title"].split()[0].lower())
        path = args.cache / (key + "-" + args.edition + ".zip")
        if not path.is_file():
            raise ReferenceError("Archive not captured; offline mode never downloads: " + key)
        return path
    return archive_for(source, args.cache, args.edition)


def write_captured_archive(path, rows, license_text):
    """Фиксирует timestamps/порядок ZIP: повтор тех же страниц не создаёт дубль корпуса."""
    temporary = path.with_suffix(".part")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as output:
            for number, row in enumerate(rows):
                row = {**row, "aliases": sorted(row["aliases"])}
                entry = zipfile.ZipInfo(f"pages/{number}.reference.json", date_time=(1980, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                output.writestr(entry, json.dumps([row], ensure_ascii=False, sort_keys=True))
            entry = zipfile.ZipInfo("LICENSE.txt", date_time=(1980, 1, 1, 0, 0, 0))
            output.writestr(entry, license_text)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def page_for(pack, row):
    """Привязывает страницу к исходному корпусу; API moniker никогда не берётся из TFM."""
    path = row["path"]
    language = pack["language"]
    if row["signature"] and row["symbol"].startswith(API_TYPES):
        if language == "dotnet":
            view = "net-10.0"
        elif language == "efcore":
            view = "efcore-" + pack["version"].rsplit(".", 1)[0]
        elif language == "ef6":
            view = "entity-framework-6.2.0"
            # Learn EF6 6.2 is not evidence for package 6.5.1; leave its API English.
            return None
        else:
            return None
        identifier = row["symbol"].lower()
        type_identity = row["signature"][2:].split("(", 1)[0]
        type_name = type_identity.rsplit(".", 1)[0]
        if "`" in type_name:
            type_name = re.sub(r"`(\d+)", r"-\1", type_name).lower()
            identifier = type_name + "." + row["symbol"].rsplit(".", 1)[-1].lower()
        return ("https://learn.microsoft.com/ru-ru/dotnet/api/" + identifier + "?view=" + view, view, row["symbol"])
    if language in {"efcore", "ef6"} and "/entity-framework/" in path:
        relative = path.split("/entity-framework/", 1)[1].removesuffix(".md")
        relative = relative.removesuffix("/index")
        return ("https://learn.microsoft.com/ru-ru/ef/" + relative, "", "")
    if language == "csharp" and pack["version"] in {"12", "13"} and "release guide" in pack["title"]:
        return ("https://learn.microsoft.com/ru-ru/dotnet/csharp/whats-new/csharp-" + pack["version"], "", "")
    if language == "csharp" and not row["signature"] and "/docs/" in path:
        relative = path.split("/docs/", 1)[1]
        if relative.startswith(("csharp/language-reference/", "standard/linq/")):
            return ("https://learn.microsoft.com/ru-ru/dotnet/" + relative.removesuffix(".md"), "", "")
    if language == "dotnet" and "/docs/" in path and not row["signature"]:
        return ("https://learn.microsoft.com/ru-ru/dotnet/" + path.split("/docs/", 1)[1].removesuffix(".md"), "", "")
    return None


def source_rows(pack):
    """Читает исходные идентификаторы и aliases, не загружая API-корпус целиком."""
    with connect(Path(pack["_directory"]) / "documents.sqlite") as db:
        query = """SELECT d.id,d.path,d.title,d.symbol,d.signature,json_group_array(a.alias) AS aliases
                   FROM documents d LEFT JOIN aliases a ON a.document_id=d.id
                   GROUP BY d.id ORDER BY d.path,d.signature"""
        for row in db.execute(query):
            result = dict(row)
            result["aliases"] = [alias for alias in json.loads(row["aliases"]) if alias is not None]
            yield result


def translated_archive(pack, archive):
    """Сопоставляет MDN/Python/Helm по пути страницы; отсутствующий перевод пропускается."""
    with zipfile.ZipFile(archive) as source:
        names = set(source.namelist())
        for row in source_rows(pack):
            path = row["path"]
            if pack["language"] in {"javascript", "html", "css"}:
                relative = path.split("/files/en-us/", 1)[-1]
                translated = "translated-content-main/files/ru/" + relative
                url = "https://developer.mozilla.org/ru/docs/" + relative.removesuffix("/index.md")
            elif pack["language"] == "python":
                relative = path.split("/", 1)[-1]
                translated = "python-3.14-docs-html/" + relative
                url = "https://docs.python.org/ru/3.14/" + relative
            else:
                relative = path.split("/docs/", 1)[-1]
                translated = "helm-www-main/i18n/ru/docusaurus-plugin-content-docs/current/" + relative
                if translated not in names:
                    translated = "helm-www-main/i18n/ru/docusaurus-plugin-content-docs/version-3/" + relative
                    # v3 pages do not describe the matching current v4 English page.
                    continue
                url = "https://helm.sh/ru/docs/" + relative.removesuffix(".md").removesuffix(".mdx")
            if translated not in names:
                continue
            if source.getinfo(translated).file_size > 8 * 1024 * 1024:
                raise ReferenceError("Translation file exceeds 8 MiB")
            body = source.read(translated).decode("utf-8-sig")
            if len(re.findall(r"[А-Яа-яЁё]", body)) < 80:
                continue
            parsed = list(documents(translated, body.encode(), pack["language"]))[0]
            yield {**row, "title": parsed["title"], "text": parsed["text"], "sourceUrl": url}


def prepare(pack, args, archives):
    """Публикует отдельный неполный RU-корпус и inventory доступных/пропущенных страниц."""
    rows, skipped = [], []
    language = pack["language"]
    if language in {"javascript", "html", "css", "helm", "python"}:
        key = language if language in {"helm", "python"} else "mdn"
        rows = list(translated_archive(pack, archives[key]))
        source_url = {"helm": "https://github.com/helm/helm-www", "python": "https://docs.python.org/ru/3.14/",
                      "mdn": "https://github.com/mdn/translated-content"}[key]
    else:
        grouped = {}
        for row in source_rows(pack):
            page = page_for(pack, row)
            if page:
                grouped.setdefault(page, []).append(row)
        def capture(item):
            """Одна загрузка на страницу всех перегрузок, с проверкой UID и версии."""
            (url, view, uid), originals = item
            try:
                page = fetch_page(url, args.cache / "learn-ru", view, uid,
                                  args.offline_cache_only or args.rate_limited.is_set())
            except LearnRateLimit as error:
                args.rate_limited.set()
                return [], {"url": url, "reason": str(error)[:250]}
            except Exception as error:
                return [], {"url": url, "reason": str(error)[:250]}
            prefix = "Страница Microsoft Learn: описание метода и его перегрузок. Сверьте нужную сигнатуру.\n\n" if uid else ""
            text = prefix + page["text"] + "\n\nПеревод источника: " + page["translationType"]
            return [{**row, "text": text, "sourceUrl": url} for row in originals], None
        with ThreadPoolExecutor(max_workers=4) as pool:
            for captured, missing in pool.map(capture, grouped.items()):
                rows.extend(captured)
                if missing:
                    skipped.append(missing)
        source_url = "https://learn.microsoft.com/ru-ru/"
    if not rows:
        return {"englishPackId": pack["id"], "language": language, "translated": 0, "skipped": skipped}
    archive = args.cache / (pack["id"] + "-ru-" + args.edition + ".zip")
    license_text = pack["license"]
    if source_url.startswith("https://learn.microsoft.com/"):
        # Перевод Learn — документация, а не DLL/XML NuGet с лицензией пакета.
        license_text = "CC-BY-4.0 documentation / MIT examples (Microsoft Learn upstream); https://learn.microsoft.com/legal/termsofuse"
    write_captured_archive(archive, rows, license_text + "\n" + source_url)
    metadata = {key: pack[key] for key in ("language", "version", "edition", "title", "license")}
    metadata.update(locale="ru", sourceUrl=source_url, license=license_text, includes=["pages/*.reference.json"])
    imported = import_archive(archive, metadata, args.destination)
    return {"englishPackId": pack["id"], "language": language, "translated": len(rows), "totalOriginal": pack["documentCount"],
            "packId": imported["id"], "skipped": skipped}


def main():
    """Сеть разрешается только явным --download; inventory сохраняет границы найденных переводов."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", required=True)
    parser.add_argument("--edition", required=True)
    parser.add_argument("--languages", nargs="+", choices=["efcore", "ef6", "dotnet", "csharp", "javascript", "html", "css", "helm", "python"],
                        default=["efcore", "ef6", "dotnet", "csharp", "javascript", "html", "css", "helm", "python"])
    parser.add_argument("--cache", type=Path, default=Path("/tmp/atlas-reference-archives"))
    parser.add_argument("--destination", type=Path, default=ROOT / "resources/language-references")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--offline-cache-only", action="store_true", help="Re-import already captured pages without network")
    args = parser.parse_args()
    args.rate_limited = Event()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", args.edition):
        parser.error("Invalid edition")
    args.cache.mkdir(parents=True, exist_ok=True)
    (args.cache / "learn-ru").mkdir(exist_ok=True)
    archives = {}
    if set(args.languages) & {"javascript", "html", "css"}:
        archives["mdn"] = prepared_archive({"language": "javascript", "title": "MDN translations", "cacheKey": "mdn-translated",
                                      "url": "https://codeload.github.com/mdn/translated-content/zip/refs/heads/main"}, args)
    if "helm" in args.languages:
        archives["helm"] = prepared_archive({"language": "helm", "title": "Helm", "url": "https://codeload.github.com/helm/helm-www/zip/refs/heads/main"}, args)
    if "python" in args.languages:
        archives["python"] = prepared_archive({"language": "python", "title": "Python RU", "cacheKey": "python-3.14-ru",
                                         "url": "https://docs.python.org/ru/3.14/archives/python-3.14-docs-html.zip"}, args)
    store = ReferenceStore(Path("/tmp/unused-atlas-translation-state"), args.destination)
    outcomes = []
    for pack in store.catalog():
        if pack["locale"] != "en" or pack["language"] not in args.languages:
            continue
        print(json.dumps({"phase": "capture", "title": pack["title"]}), flush=True)
        outcome = prepare(pack, args, archives)
        outcomes.append(outcome)
        args.report.write_text(json.dumps(outcomes, ensure_ascii=False, indent=2))
        print(json.dumps({k: v for k, v in outcome.items() if k != "skipped"}), flush=True)


if __name__ == "__main__":
    main()
