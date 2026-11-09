"""Build source-attributed offline wiki pages from the exact installed records."""

import argparse
from pathlib import Path

from .catalog import PatternCatalog


def _bullets(items: list[str]) -> str:
    """
    Render short explanatory lists as plain Markdown, never executable markup.
    """
    return "\n".join("- " + item for item in items)


def render_pattern(record: dict, catalog_version: str) -> str:
    """
    Project one full card, including its exact identity, examples and limitations.
    """
    parts = [f"# {record['title']}", record["summary"],
             f"ID: `{record['id']}` · версия: `{record['version']}` · статус карточки: **{record['status']}**.",
             f"Content hash: `{record['contentHash']}`.", f"Catalog version: `{catalog_version}`.",
             "Квалификация применения в проекте: **not-assessed**. Чтение карточки не подтверждает внедрение."]
    sections = [("Задача", record["problem"]), ("Когда применять", _bullets(record["whenToUse"])),
                ("Когда не применять", _bullets(record["whenNotToUse"])),
                ("Граница класса или папки", record["moduleBoundary"]),
                ("Ответственность", _bullets(record["responsibilities"])),
                ("Публичный контракт", record["publicContract"]),
                ("Зависимости", _bullets(record["dependencies"])),
                ("Состояние и время жизни", record["stateLifetime"]),
                ("Ошибки", _bullets(record["errors"]))]
    for title, body in sections:
        parts.extend(["## " + title, body])
    for title, key in (("Пример", "example"), ("Контрпример", "counterexample")):
        example = record[key]
        parts.extend(["## " + title, "```" + example["language"] + "\n" + example["code"].rstrip() + "\n```",
                      example["explanation"]])
    for title, key in (("Как внедрять", "migrationSteps"), ("Ограничения", "limits"),
                       ("Что проверять", "checks"), ("Альтернативы", "alternatives"),
                       ("Как писать код", "codingGuidance")):
        parts.extend(["## " + title, _bullets(record[key])])
    applicability = record["applicability"]
    parts.extend(["## Применимость и версии", _bullets(applicability["languages"] + applicability["versions"]),
                  applicability["scope"], "Примеры проверяются синтаксически при сборке каталога. "
                  "Они не исполняются и не доказывают поведение реального backend или MCP-клиента.",
                  "## Источники", f"Ссылки проверены: {record['reviewedAt']}. Рецепт Atlas отделён от гарантий источников."])
    for source in record["sources"]:
        parts.append(f"[{source['title']}]({source['url']}) — {source['supports']}")
    links = [f"[{identifier}](Pattern-{identifier}.md)" for identifier in record["related"]]
    parts.extend(["## Продолжить", " · ".join(links + ["[Каталог паттернов](Code-Patterns.md)"])])
    return "\n\n".join(parts) + "\n"


def render_pages(catalog: PatternCatalog) -> dict[str, str]:
    """
    Return the index and per-card pages from one immutable catalogue edition.
    """
    pages = {}
    index = ["# Паттерны проектирования и написания кода",
             "Найдите подход к задаче, прочитайте точную карточку и проверьте её применимость. "
             "Модуль может быть самостоятельным классом или связной папкой; абстракция не обязательна сама по себе.",
             "Это единый локальный справочник для wiki и агентов. "
             "Draft/reviewed карточки не подтверждают квалификацию конкретного проекта.",
             f"Catalog version: `{catalog.catalog_version}`.",
             "## Выбрать паттерн", "[Как выбирать](Pattern-Selection.md) · [Модульный код](Modular-Code.md) · "
             "[Совместимое обновление MCP](MCP-Compatible-Updates.md)"]
    offset = 0
    while True:
        page = catalog.search(offset=offset, limit=20, catalog_version=catalog.catalog_version)
        for summary in page["items"]:
            record = catalog.get(summary["id"], version=summary["version"],
                                 content_hash=summary["contentHash"], catalog_version=catalog.catalog_version)["pattern"]
            pages[record["wikiPage"] + ".md"] = render_pattern(record, catalog.catalog_version)
            index.append(f"- [{record['title']}]({record['wikiPage']}.md) — {record['summary']} "
                         f"Версия `{record['version']}`, `{record['status']}`, hash `{record['contentHash']}`.")
        if page["nextOffset"] is None:
            break
        offset = page["nextOffset"]
    index.extend(["## Границы доказательств", "Каталог предлагает варианты, а не автоматический рефакторинг. "
                  "Наличие паттерна, высокий score графа или совпадение схемы не доказывают архитектурную целостность. "
                  "Все примеры — текст; загрузка wiki ничего не исполняет.",
                  "[Как менять Atlas](Development-Guide.md) · [Агенты и MCP](Agents-and-MCP.md)"])
    pages["Code-Patterns.md"] = "\n\n".join(index) + "\n"
    return pages


def _preflight(root: Path, pages: dict[str, str]) -> Path:
    """
    Validate every source directory and target before generating any page.

    Build output must stay in the ordinary project wiki tree. A redirected later
    target rejects the entire batch before an earlier regular file is changed.
    """
    directory = root / "docs" / "wiki"
    for path in (root, root / "docs", directory):
        if path.is_symlink() or not path.is_dir():
            raise RuntimeError("Pattern wiki source must be an ordinary project directory")
    for name, content in pages.items():
        target = directory / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise RuntimeError(f"Pattern wiki target must be an ordinary file: {name}")
        if len(content.encode("utf-8")) > 100000:
            raise RuntimeError(f"Pattern wiki page exceeds byte limit: {name}")
    expected = {name for name in pages if name.startswith("Pattern-")}
    stale = {path.name for path in directory.glob("Pattern-*.md")} - expected - {"Pattern-Selection.md"}
    if stale:
        # Do not remove author content implicitly when a catalogue edition changes.
        raise RuntimeError("Unexpected pattern wiki pages: " + ", ".join(sorted(stale)))
    return directory


def build(root: Path, *, check: bool) -> int:
    """
    Write or verify generated pages; never publish externally or execute examples.
    """
    catalog = PatternCatalog(root / "resources" / "code-patterns")
    pages = render_pages(catalog)
    directory = _preflight(root, pages)
    for name, content in pages.items():
        target = directory / name
        if check:
            if not target.is_file() or target.read_text(encoding="utf-8") != content:
                raise RuntimeError(f"Pattern wiki page is stale: {name}")
        else:
            target.write_text(content, encoding="utf-8")
    return len(pages)


def main() -> None:
    """
    Expose the build/check operation to the existing Node wiki packager.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()
    count = build(Path(__file__).resolve().parents[2], check=options.check)
    print(f"Pattern wiki: {count} pages {'verified' if options.check else 'generated'}")


if __name__ == "__main__":
    main()
