# XML-документация подключённых библиотек

Страница `/library-docs.html` доступна в меню Atlas отдельно от языковых
справочников. Выберите репозиторий, нажмите **«Найти XML»**, при необходимости
выберите `.csproj`, затем **«Импортировать / обновить»**. Само открытие страницы,
поиск и MCP-чтение ничего не импортируют и не запускают restore/build.

Atlas читает авторские XML doc-comments сборок: `summary`, `param`, `typeparam`,
`returns`, `remarks`, `exception`, `value`, ссылки `cref`. Перегрузки сохраняют
исходный DocId. XML include и inheritdoc не разрешаются: вместо выдуманного
описания выводится `Unresolved`. Файлы UTF-8 и UTF-16 поддержаны; DTD/entities
запрещены. DLL не загружаются, примеры не исполняются.

Источники:

- NuGet compile assets из `obj/project.assets.json`: только выбранные версии и
  пути `ref/lib`, включая транзитивные пакеты. Прочие установленные версии не
  подставляются. Кэш берётся из `~/.nuget/packages` или `NUGET_PACKAGES`.
- Reference packs из `downloadDependencies` assets: только точная установленная
  версия и TFM; более свежий SDK не заменяет отсутствующий старый pack.
- Локальный `Reference/HintPath` внутри зарегистрированного корня репозитория:
  XML рядом со сборкой. Версия без разрешённых assets обозначается `unknown`.
  Условный Reference — статический кандидат, а не доказанное MSBuild binding.

Рядом с каждой XML-сборкой проверяются каталоги `ru`, `en` и других локалей.
`xml:lang`, если задан, уточняет язык документа; без него используется имя
каталога, а соседний XML считается английским. Atlas не переводит текст сам.
Русский поиск использует имеющийся перевод **каждого описания**, оставляя
английский оригинал для непереведённых методов той же версии/asset.

## Хранение и актуальность

Каталог проекта: `<data-dir>/library-documentation/<repository-id>/`.
`catalog.json` указывает текущую явную выборку; SQLite/FTS корпуса лежат в
`language-references/reference-*`. Старые редакции сохраняются на диске,
но обычный поиск использует только текущий каталог. Повторный импорт
неизменённых XML повторно использует те же корпуса.

В ответах есть `capturedAt`, `inputFingerprint`, версия пакета, assembly/asset,
TFM и список использующих `.csproj`, фактический путь XML, hash, дата и размер.
Источник — локальный `file:` URL, а ссылка на Microsoft описывает только
формат XML. Отсутствующие assets/пакеты/XML перечисляются как предупреждения;
если `.csproj` новее assets, версия разрешения может быть устаревшей. Это не
компилятор и не вычисление MSBuild conditions. Custom obj/output paths,
packages.config и внешние HintPath пока не поддержаны.

Импорт атомарно меняет только `catalog.json`: невалидный XML, изменение
csproj/assets или источников во время capture сохраняет прежний каталог.
Карты кода и языковые справочники не меняются. Поиск точный и полнотекстовый,
без обязательного embedding-моделя; векторный индекс библиотек пока не строится.

Лимиты: 128 проектов, 40 000 файлов обхода, 20 000 зависимостей,
2 048 XML, 64 MiB на входной файл и 1 GiB XML на capture.
Усечение обозначается явно. На странице каталог показан порциями по 50 строк.

## API и MCP

Namespace: `/api/projects/{repository-id}/library-documentation`.

- GET `/` — опубликованный каталог; `/sources?manifest=...` — явное обнаружение.
- POST `/capture` с `{ "manifest": "relative/App.csproj" }` или `{}` — фоновой импорт.
- GET `/jobs/{id}` — прогресс и результат.
- POST `/search` — query, packId, locale (`en` по умолчанию), mode (`exact`/`fulltext`), limit.
- GET `/{packId}/documents/{documentId}?offset=...&limit=...` — полный текст страницами.

MCP: `list_library_documentation`, `discover_library_sources`,
`capture_library_documentation`, `get_library_documentation_job`,
`search_library_documentation`, `get_library_documentation`.
Все инструменты проверяют привязанный repository ID до HTTP-запроса.
Каталоги и результаты имеют UTF-8 budget 64 KB и continuation offsets.
Правила для агентов поставляются в `skills/code-atlas/SKILL.md`.

Формат авторских комментариев описан в
[официальной документации C#](https://learn.microsoft.com/dotnet/csharp/language-reference/xmldoc/);
NuGet выбирает compile assets отдельно от runtime:
[официальный контракт выбора сборок](https://learn.microsoft.com/nuget/create-packages/select-assemblies-referenced-by-projects).

## Форматирование XML

Новые capture используют общий структурный обработчик .NET XML из
`atlas/reference_xml.py`: Markdown разделы Remarks/Examples, абзацы, списки,
таблицы и literal code fences попадают также в HTTP/MCP ответы. UID xref
служит для локального поиска API в текущем каталоге проекта. Чтение старого
каталога его не перестраивает; страницу форматирует `web/reference-content.js`.
См. раздел «Структура .NET XML и DocFX» в `docs/language-references.md`.
