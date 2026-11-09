# Локальные языковые справочники

`/language-docs.html` открывается через меню «Страницы Atlas» рядом с картами и
технической страницей. Корпусы доступны всем проектам, не становятся узлами
Graphify и не требуют пересборки карт.

## Хранение и поиск

- Полные тексты и перегрузки API хранятся в неизменяемых версиях SQLite с FTS5.
- LanceDB хранит по одному вектору краткого описания страницы/API. Это locator,
  а не полная векторизация всех абзацев: `coverage=document-summaries`.
  Для .NET XML используется авторский `summary`; полные параметры, remarks и
  примеры остаются в исходном документе. Одинаковые summary кодируются один раз.
- Точное имя, сигнатура и выбранная версия имеют приоритет. Полнотекстовые и
  смысловые результаты — кандидаты; они не доказывают binding вызова или runtime.
- Каждый ответ сохраняет версию, edition, исходный путь, source URL, archiveHash,
  license и importedAt. Полный документ читается страницами через nextOffset.
- EN оригиналы отделены от RU/других переводов (`locale` в manifest). При отсутствии
  перевода показывается английский оригинал с явным уведомлением. Перевод по сети
  или генеративной моделью не выполняется. Интерфейс RU/EN — `web/locales/*`.
  Для новой локализации добавьте её в `web/locales/catalog.json` и отдельные
  `references.json`/`common.json`; отсутствующие ключи используют EN fallback.
- Встроенные корпуса лежат в `resources/language-references`, добавленные —
  `<data-dir>/language-references`; векторные поколения отдельно в `reference-search`.
  Большие подготовленные ресурсы не хранятся в Git; PyInstaller включает их в bundle.

## Однократная подготовка встроенного корпуса

Из checkout Atlas явно выполните:

```sh
.venv/bin/python scripts/prepare_language_references.py --download --edition 2026-10-11
```

Script скачивает полные официальные архивы; server не вызывает его и не содержит
сетевого downloader. Параметры `--languages`, `--cache`, `--destination` позволяют
выбрать корпуса и переиспользовать локальные архивы. Повторная команда с той же
edition использует существующий архив; для новой редакции задайте другую edition.
Archive SHA-256 фиксирует реально захваченное содержимое даже для upstream main.

Начальные источники: .NET API XML и .NET guides, Python HTML (включая standard
library), MDN JavaScript, Go specification и стандартные source/doc comments,
OpenJDK standard-library source/Javadoc comments, Docker и Helm Markdown.
Go/Java source corpora содержат полный исходный текст, не сгенерированный HTML
godoc/Javadoc. .NET/Docker/Helm multi-version corps не считаются конкретной версией
runtime: проверяйте applicability и upstream version внутри документа. Отбор файлов
и границы корпуса видны в `reference_documents.eligible`, source recipes — в script.
Часть .NET API имеет upstream placeholder «To be added.»; такой элемент доступен
по имени, но описание не придумывается. Внешние ссылки и ссылки на snippets
сохраняются как текст и не загружаются автоматически.

При запуске Atlas автоматически строит недостающие смысловые индексы установленных
справочников в фоне. Готовые встроенные или пользовательские поколения переиспользуются.
Каждый подготовленный корпус сразу доступен общему поиску, включая переходы из схем
и MCP; ждать всей очереди не требуется. Прогресс виден на странице документации.
После прерывания следующий запуск продолжает корпуса без опубликованного индекса.
Ошибка одного корпуса не останавливает остальные и доступна в результате задачи.
Ручная пересборка и offline script также доступны:

```sh
.venv/bin/python scripts/index_language_references.py --model-cache "/path/to/models"
```

Модель должна быть заранее установлена по `docs/semantic-search.md`; команды не
скачивают модель. Без неё точный и полнотекстовый поиск остаются доступны.

## Добавление более свежей версии

Скачайте ZIP отдельно, откройте «Добавить или обновить документацию», задайте его
локальный путь, язык, version, edition, locale, source URL и лицензию. Импорт идёт
в фоне. Новая редакция публикуется атомарно; старые остаются доступны. Идентичный
повторный импорт идемпотентен. На чтении нет implicit import/rebuild/download.
Для нестандартной структуры ZIP API manifest может задать до 20 glob `includes`.

Ограничения: ZIP до 1 GiB, expanded до 3 GiB, до 300000 entries, каждый выбранный
файл до 8 MiB, документ до 4 MiB символов. ZIP не распаковывается на диск, выбранные
symlink, path traversal и XML DTD/entities запрещены. Примеры/команды не исполняются.
Сбой импорта не публикует частичный корпус. Один writer ограничивает очередь;
после перезапуска каталог сохранён, статус незавершённой job нужно перечитать.

## API и MCP

- GET `/api/language-references` — каталог, даты, размеры, vector coverage.
- POST `/api/language-references/search` — query, language/version/packId, locale,
  mode exact/fulltext/semantic/hybrid и limit.
- GET `/api/language-references/{packId}/documents/{documentId}` — offset/limit.
- POST `/api/language-references/import` — archivePath, manifest.
- POST `/api/language-references/{packId}/index` — явный rebuild vectors.
- GET `/api/language-references/jobs/{id}` — progress и итог.

MCP read-only tools: `list_language_references`, `search_language_reference`,
`get_language_reference`. Каталог имеет offset/limit/nextOffset; MCP ответы
ограничены 64000 UTF-8 байт, nextOffset сохраняет продолжение документа.
Новые tool descriptions и исходные документы по
умолчанию английские. Нельзя приписывать найденное описание собственному методу
с тем же именем: проверьте импорт, тип receiver и перегрузку. В документационном
режиме несопоставленного вызова есть переход к поиску справочника.

## Дополнительные языки и технологии

Каталог поддерживает также `sql`, `postgresql`, `lancedb`, `html`, `css`, `git`,
`sh`, `bat` и `dotnet`. Источники и границы отбора заданы в
`scripts/reference_sources.py`:

| Корпус | Версия / границы |
| --- | --- |
| SQL | Основы языка на примерах PostgreSQL: tutorial, syntax, queries, DDL/DML. Это не полный текст ISO SQL и не обещание переносимости расширений между СУБД. |
| PostgreSQL | Полный официальный HTML manual 18.0; версия закреплена, текущая версия сервера может отличаться. |
| LanceDB | Снимок open-source guides и опубликованных SDK страниц из `lancedb/lancedb`, `rolling`. Enterprise/private pages не входят. |
| HTML / CSS | Полные соответствующие разделы MDN EN; living standard, edition фиксирует захваченный архив. |
| Git | Командный справочник и руководства 2.51.0 в исходном AsciiDoc; это закреплённая версия, не автоматический latest. |
| sh | POSIX.1-2024: shell language, execution environment и команда sh. Расширения Bash не подразумеваются. |
| bat | Windows CMD и batch: официальный каталог windows-commands; PowerShell не входит. |
| .NET 10 | API, входящие в официальный `FrameworksIndex/net-10.0.xml`, плюс release/library/SDK guides и compatibility notes 10.0. |
| C# 12 / 13 | Отдельные feature specifications/proposals и release guides. Общий многоверсионный C# справочник остаётся доступен; это не полностью замороженная спецификация каждой версии. |

.NET framework и версия C# различаются: для API задавайте `language=dotnet`,
`version=10`; для возможностей языка — `language=csharp`, `version=12` или `13`.
Номер сборки не используется как доказательство наличия API. Импорт с
`framework=net-10.0` читает whitelist официальных DocId и предпочитает подходящие
FrameworkAlternate сигнатуры. Полные исходные описания и упоминания иных версий
сохраняются; это не проверка binding/перегрузки в проекте. На странице импорта поле
framework доступно для `dotnet`; руководства можно импортировать без фильтра.

Подготовить только добавленные корпуса:

```sh
.venv/bin/python scripts/prepare_language_references.py --download --edition 2026-10-11 --languages sql postgresql lancedb html css git sh bat dotnet
.venv/bin/python scripts/prepare_language_references.py --download --edition 2026-10-11 --languages csharp --versions 12 13
```

Общий архив MDN/dotnet/docs скачивается один раз на edition и используется несколькими
корпусами. TAR и отдельные POSIX HTML страницы преобразуются в локальный ZIP без
extract и без выполнения содержимого. ZIP/FTS сохраняют весь выбранный текст.
В Git AsciiDoc и LanceDB MDX ссылки/include директивы остаются исходным текстом,
не исполняются и не разрешаются автоматически.

Для выборочного построения локальных векторов script индекса принимает
`--packs <packId> ...`. Точный и полнотекстовый поиск доступны немедленно после
импорта, независимо от необязательного смыслового индекса. Поиск по всему каталогу
ограничен 32 редакциями и сообщает об усечении; для большого набора выбирайте
packId/версию явно.

## Entity Framework

Отдельные технологии `efcore` и `ef6` не смешиваются с LINQ-to-Objects или общим
.NET API. Добавлены официальные руководства `dotnet/EntityFramework.Docs`, включая
LINQ-запросы, tracking, загрузку связей, миграции, транзакции, concurrency и
производительность. Общие руководства EF Core имеют `version=multi-version`,
EF6 — `6.x`; применимость конкретной главы нужно проверять по её тексту.

API-документация взята из XML doc-comments официальных NuGet-пакетов:
EF Core/Relational `8.0.0`, `9.0.0`, `10.0.0` и EntityFramework `6.5.1`.
Это закреплённые пакеты, не заявление о последних patch releases. Для MCP задавайте
`language=efcore`, `version=9.0.0` (или другую установленную версию), либо
`language=ef6`, `version=6.5.1`. `IQueryable`, provider и версия EF важны для
описания поведения; найденная документация не доказывает, какой SQL выполнился.

Архив `.nupkg` — ZIP: Atlas читает выбранный XML, не загружает DLL, не исполняет
примеры, запросы или миграции. Каждый member/overload становится отдельным
документом. `signature` содержит исходный XML DocId, а не восстановленную C#
сигнатуру; короткие/FQN имена — кандидаты для поиска. Файлы монолитного EF XML
могут занимать до 32 MiB; прочие выбранные файлы остаются ограничены 8 MiB.
Полный отдельный документ по-прежнему ограничен 4 MiB символов.

Подготовка и обновление остаются явными:

```sh
.venv/bin/python scripts/prepare_language_references.py --download --edition 2026-10-11 --languages efcore ef6
```

Можно отобрать `--versions 9.0.0` для API; руководства импортируются отдельно
с `--versions multi-version 6.x`. Общая NuGet версия и TFM пакета различаются:
пакет EF Core 9.0.0 содержит XML в `lib/net8.0`.

## Переводы и английский оригинал

Русские страницы хранятся отдельными корпусами с `locale=ru`. Поиск `locale=ru`
проверяет перевод каждого документа, сохраняя английские страницы и перегрузки,
для которых перевод не найден. Версии, редакции и технологии не смешиваются.
Выбор английского корпуса в UI допускает его русский перевод; выбор русского
дополняется английскими документами той же редакции. MCP по умолчанию использует
английский. Результаты содержат `requestedLocale` и `localeFallback`, а чтение
документа — фактический `sourceUrl` страницы.

Подготовка переводов выполняется только явно:

```sh
.venv/bin/python scripts/prepare_reference_translations.py --download --edition 2026-10-11 --report /tmp/atlas-ru-reference-import.json
```

Script использует опубликованные страницы Microsoft Learn, русский архив Python
и `mdn/translated-content`. Переводы неполные. Microsoft Learn может помечать
страницу `MT` (машинный перевод источника); Atlas сохраняет эту метку и не
создаёт собственного перевода. Проверяются русский prose, UID API и moniker
версии. Одно описание метода может включать несколько перегрузок: исходный XML
DocId сохраняется для проверки, текст явно обозначен как страница метода.

Подготовленные `.reference.json` содержат только текст, aliases, исходный путь
для стабильного ID и HTTPS provenance. Для MDN/Python сопоставляется конкретная
страница; для API — однозначные symbol/signature. English остаётся при отсутствии
перевода, несовпадении версии или недоступности страницы. Наличие перевода в иной
версии (например, EF6 6.2 вместо пакета 6.5.1, Helm 3 вместо 4) не заменяет
документ установленной версии. Полный inventory пропусков сохраняется в отчёте.

## Структура .NET XML и DocFX

Обработчик `atlas/reference_xml.py` сохраняет XML разделы `remarks`, `example`,
`param`, `returns`, абзацы, списки и таблицы. Markdown внутри `format/CDATA`
остаётся структурированным; fenced code сохраняет отступы и буквальные xref.
XML `see cref` и DocFX `<xref:UID>` нормализуются в `[подпись](xref:UID)`.
UID хранится отдельно от отображаемого имени; `displayProperty` задаёт подпись.
`include`/`inheritdoc` остаются явно неразрешёнными.

На `/language-docs.html` и `/library-docs.html` разделы Remarks/Examples
отображаются как «Примечания»/«Примеры» при русском интерфейсе. Это перевод
заголовков, не содержимого авторской документации. Ссылки запускают локальный
точный поиск с текущими фильтрами; ссылка не доказывает выбор перегрузки в коде.
CLR arity (`List`1`) не принимается за inline code. Исходный текст страницы
доступен в отдельном раскрываемом блоке; HTTP/MCP пагинация не меняется.

`:::code source=...` указывает на внешний snippet: отображается путь с пометкой
«файл не загружен». Atlas не скачивает и не исполняет его. Неподдержанная
разметка остаётся текстом. HTML никогда не вставляется через `innerHTML`.

Extractor v8 применяется при следующем явном импорте/capture и создаёт новую
неизменяемую редакцию. Существующие SQLite корпуса не переписываются при
чтении: их xref, Markdown и прежние метки Remarks/Example форматируются
в интерфейсе. Уже потерянные при старом импорте границы XML разделов/отступы
восстанавливаются только повторным явным импортом исходного архива.
