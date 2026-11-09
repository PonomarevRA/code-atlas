"""Явная однократная подготовка полных локальных корпусов из официальных архивов.

Этот script не импортируется сервером. --download выполняет сеть только по команде
владельца; обычный Atlas использует готовые SQLite/LanceDB без скачивания.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from atlas.reference_store import import_archive
from scripts.reference_sources import ADDITIONAL_SOURCES
from scripts.reference_downloads import archive_for

SOURCES = [
    {"language": "csharp", "version": "multi-version", "title": ".NET API reference", "license": "CC-BY-4.0 / MIT (see upstream repository)",
     "sourceUrl": "https://github.com/dotnet/dotnet-api-docs", "url": "https://codeload.github.com/dotnet/dotnet-api-docs/zip/refs/heads/main"},
    {"language": "csharp", "version": "multi-version", "title": "C# and .NET language/library guides", "license": "CC-BY-4.0 / MIT (see upstream repository)",
     "sourceUrl": "https://github.com/dotnet/docs", "url": "https://codeload.github.com/dotnet/docs/zip/refs/heads/main"},
    {"language": "python", "version": "3.14", "title": "Python 3.14 language and standard library", "license": "Python documentation license (PSF)",
     "sourceUrl": "https://docs.python.org/3.14/", "url": "https://docs.python.org/3.14/archives/python-3.14-docs-html.zip"},
    {"language": "java", "version": "25", "title": "OpenJDK 25 standard library source and Javadoc comments", "license": "GPL-2.0 with Classpath Exception (see upstream files)",
     "sourceUrl": "https://github.com/openjdk/jdk/tree/jdk-25%2B36", "url": "https://codeload.github.com/openjdk/jdk/zip/refs/tags/jdk-25%2B36"},
    {"language": "javascript", "version": "living-standard", "title": "MDN JavaScript language and standard API", "license": "CC-BY-SA-2.5 / CC0 examples (see upstream LICENSE)",
     "sourceUrl": "https://github.com/mdn/content", "url": "https://codeload.github.com/mdn/content/zip/refs/heads/main"},
    {"language": "go", "version": "1.25.1", "title": "Go 1.25.1 specification and standard library source documentation", "license": "BSD-3-Clause (Go LICENSE)",
     "sourceUrl": "https://github.com/golang/go/tree/go1.25.1", "url": "https://codeload.github.com/golang/go/zip/refs/tags/go1.25.1"},
    {"language": "docker", "version": "multi-version", "title": "Docker official documentation", "license": "CC-BY-4.0 / Apache-2.0 (see upstream LICENSE)",
     "sourceUrl": "https://github.com/docker/docs", "url": "https://codeload.github.com/docker/docs/zip/refs/heads/main"},
    {"language": "helm", "version": "multi-version", "title": "Helm official documentation", "license": "Apache-2.0 (upstream repository)",
     "sourceUrl": "https://github.com/helm/helm-www", "url": "https://codeload.github.com/helm/helm-www/zip/refs/heads/main"},
]
SOURCES.extend(ADDITIONAL_SOURCES)


def prepare(source, archive: Path, destination: Path, edition: str):
    """Импортирует готовый архив с явными границами версии и отбора файлов."""
    metadata = {key: source[key] for key in ("language", "version", "title", "license", "sourceUrl")}
    for key in ("includes", "framework"):
        if key in source:
            metadata[key] = source[key]
    metadata.update(edition=edition, locale="en")
    print(json.dumps({"language": source["language"], "phase": "import", "bytes": archive.stat().st_size}), flush=True)
    result = import_archive(archive, metadata, destination,
                            lambda **value: print(json.dumps({"language": source["language"], **value}), flush=True))
    print(json.dumps({"language": source["language"], "phase": "ready", "id": result["id"],
                      "documents": result["documentCount"], "bytes": result["sizeBytes"]}), flush=True)
    return result


def main():
    """Требует явные download и edition, чтобы запуск не обновлял корпус случайно."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", required=True)
    parser.add_argument("--edition", required=True)
    parser.add_argument("--languages", nargs="+", default=sorted({source["language"] for source in SOURCES}))
    parser.add_argument("--versions", nargs="+", help="Import only explicitly selected versions (e.g. 12 13)")
    parser.add_argument("--cache", type=Path, default=Path("/tmp/atlas-reference-archives"))
    parser.add_argument("--destination", type=Path, default=ROOT / "resources" / "language-references")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", args.edition):
        parser.error("Edition must be a simple revision name or date, without path separators")
    unknown = set(args.languages) - {s["language"] for s in SOURCES}
    if unknown:
        parser.error("Unknown languages: " + ", ".join(sorted(unknown)))
    sources = [source for source in SOURCES if source["language"] in args.languages
               and (not args.versions or source["version"] in args.versions)]
    if not sources:
        parser.error("No source matches selected languages/versions")
    # Одна загрузка на общий archive: MDN и dotnet/docs используются несколькими корпусами.
    archives = {}
    unique = {}
    for source in sources:
        key = source.get("cacheKey", source["language"] + "-" + source["title"].split()[0].lower())
        unique[key] = source
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [(key, pool.submit(archive_for, source, args.cache, args.edition)) for key, source in unique.items()]
        for key, job in jobs:
            archives[key] = job.result()
    failures = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = []
        for source in sources:
            key = source.get("cacheKey", source["language"] + "-" + source["title"].split()[0].lower())
            jobs.append((source, pool.submit(prepare, source, archives[key], args.destination, args.edition)))
        for source, job in jobs:
            try:
                job.result()
            except Exception as error:
                failures.append(source["title"])
                print(json.dumps({"source": source["title"], "error": str(error)}), flush=True)
    if failures:
        raise SystemExit("Incomplete corpora: " + ", ".join(failures))


if __name__ == "__main__":
    main()
