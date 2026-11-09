# Расширение языковых справочников — 2026-10-11

Добавлено 14 корпусов: 66 175 документов. Всего 22 корпуса и 233 279 документов.
Локальный preview обновлён и проверен на http://127.0.0.1:8766/language-docs.html.
Установленное native-приложение на 8765 в этой задаче не пересобиралось.

| Технология | Версия | Корпус | Документы |
| --- | --- | --- | ---: |
| bat | windows-cmd | [Windows CMD and batch command reference](https://github.com/MicrosoftDocs/windowsserverdocs/tree/main/WindowsServerDocs/administration/windows-commands) | 872 |
| csharp | 12 | [C# 12 feature specifications](https://github.com/dotnet/csharplang) | 7 |
| csharp | 12 | [C# 12 release guide](https://github.com/dotnet/docs) | 1 |
| csharp | 13 | [C# 13 feature specifications](https://github.com/dotnet/csharplang) | 9 |
| csharp | 13 | [C# 13 release guide](https://github.com/dotnet/docs) | 1 |
| css | living-standard | [MDN CSS reference and guides](https://github.com/mdn/content) | 1284 |
| dotnet | 10 | [.NET 10 API — official net-10.0 framework index](https://github.com/dotnet/dotnet-api-docs) | 61156 |
| dotnet | 10 | [.NET 10 release, libraries and SDK guides](https://github.com/dotnet/docs) | 77 |
| git | 2.51.0 | [Git 2.51.0 command reference and manuals](https://github.com/git/git/tree/v2.51.0) | 920 |
| html | living-standard | [MDN HTML reference and guides](https://github.com/mdn/content) | 256 |
| lancedb | rolling | [LanceDB open-source guides and SDK reference snapshot](https://github.com/lancedb/lancedb) | 383 |
| postgresql | 18.0 | [PostgreSQL 18.0 complete manual](https://www.postgresql.org/docs/18/) | 1143 |
| sh | POSIX.1-2024 | [POSIX shell language, execution environment and sh command](https://pubs.opengroup.org/onlinepubs/9799919799/utilities/V3_chap02.html) | 3 |
| sql | fundamentals | [SQL fundamentals — PostgreSQL examples, not a universal dialect](https://www.postgresql.org/docs/18/) | 63 |

## Проверки

- 78 тестов прошли: новые источники/framework, API/MCP/setup, документационный режим и semantic service/index.
- Настоящий MCP stdio проверил каталог и поиск/чтение всех 11 запрошенных технологических/версионных селекторов. По умолчанию ответы EN; provenance и даты сохранены.
- HTTP подтвердил PostgreSQL SELECT, Git rebase, CMD for, HTML input, CSS display, .NET ToArray<int>, C# 12 primary constructors и C# 13 params collections, а также SQL JOIN и LanceDB create_table.
- Смысловой поиск SQL/CSS/LanceDB на русском возвращает EN кандидатов из локального индекса; перевода текста не генерирует.
- Браузер подтвердил все новые редакции, точный поиск .NET 10, открытие полного оригинала и поле framework при импорте. JS syntax, Python compile и skill validator прошли.
- Карты проектов совпадают с состоянием до перезапуска; Graphify и индексы проектов не пересобирались.
- Самопроверка code-quality-gate: PASS. Закрыты несоответствие объявленной версии/framework, неверная отметка усечения после расширения лимита и отсутствие UTF-8 проверки framework XML.

## Границы

20 корпусов имеют готовый LanceDB индекс описаний. Два больших .NET API корпуса
(multi-version и net-10.0) имеют готовые точный и полнотекстовый индексы; их смысловой
индекс остаётся отдельным явным действием. Векторы описаний не заменяют полного текста.

SQL fundamentals использует PostgreSQL примеры и не заменяет текст ISO SQL.
sh — POSIX.1-2024; bat — CMD/batch. PostgreSQL 18.0 и Git 2.51.0 закреплены,
MDN/LanceDB — снимки rolling документации. C# 12/13 — спецификации возможностей
и руководства выпусков, дополняющие общий C# справочник. .NET 10 API отобраны по
официальному FrameworksIndex DocId; это не доказательство binding вызова в проекте.

Архивы и подготовленные тяжёлые базы находятся вне Git. Повторная подготовка и
обновление выполняются только явно; server/MCP не скачивают документацию на чтении.

Машиночитаемый результат: `outputs/language-reference-additions-2026-10-11.json`.
Снимок браузера: `outputs/language-reference-additions-2026-10-11.jpg`.
