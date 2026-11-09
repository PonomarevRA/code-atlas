# Карты документов и логов

Atlas сохраняет дерево выбранных файлов отдельно от карты кода. Повторные запросы
читают SQLite и захваченные фрагменты выбранного снимка. Обновление — явное действие;
удаление или изменение оригинала не уничтожает сохранённое дерево.

## Для человека

В меню «Страницы Atlas» откройте **Документы** (`/documents.html`). Раскройте
«Выбрать документы», укажите ID зарегистрированного проекта либо абсолютный
каталог, найдите файлы и выберите до 100. Нажмите «Построить выбранные файлы».
Поиск файлов показывает только имена и размеры; он не запускает парсеры.

Слева находятся карты и выбор файлов, в центре — дерево/граф/окно таблицы,
справа — выбранный узел, текст, координаты, атрибуты и ограничения. Большие списки
загружаются страницами. Ctrl/Cmd+F переводит фокус на поиск в сохранённом тексте.
Ссылка содержит карту, снимок и узел; Back/Forward восстанавливают чтение.
«Сохранить вид» закрепляет конкретные nodes/edges и исходный снимок.

## Форматы

| Мод / профиль | Что сохраняется | Существенные ограничения |
| --- | --- | --- |
| document-markdown | Заголовки, разделы, списки, таблицы, code fences, ссылки | HTML и код не выполняются; front matter непрозрачен |
| document-yaml | Поток документов, occurrence paths, значения, теги, anchors/aliases | Constructors не вызываются; aliases не разворачиваются |
| document-helm | Статический chart/templates/values и ссылки | Без helm render, установки chart или remote dependencies; unresolved остаётся unresolved |
| document-docx | Блоки, стили, вложенные таблицы, объединения | Координаты структуры OOXML; номера страниц Word не выдумываются |
| document-xlsx | Листы, таблицы, sparse cells, ranges, формулы и cache | Без вычисления формул; наличие cache не доказывает актуальность |
| document-pptx | Slides, groups/shapes, текст, таблицы, notes | Порядок shapes не гарантирует смысловой порядок чтения |
| document-pdf-basic | Страницы, текст, outline, bbox и кандидаты таблиц | PDF layout приблизителен; image-only получает needs_ocr, OCR отсутствует |
| logs-structured | JSONL, multiline envelopes, events и source spans | События не считаются восстановленной трассой вызовов |
| logs-drain3 | Группы шаблонов сообщений с новым miner на build | Включается вручную; пользовательские checkpoints не загружаются |
| logs-lnav | События распознанных форматов через локальный parser-only runtime | Нужен сертифицированный пакет для ОС и архитектуры процесса; при отсутствии профиль явно недоступен |

Моды выбирают доверенные host providers из allowlist. Python из анализируемого
каталога не загружается как parser. Ручное выключение мода блокирует новые imports,
но не историческое чтение. Document fingerprint отделён от code-analysis fingerprint.
Настройки Helm и обязательный контекст участвуют в идентичности артефакта.

## Для агента: MCP без повторного парсинга

1. `discover_document_files(root="/absolute/documents", limit=50)` или `projectId`.
   При `nextCursor` повторить с тем же scope/фильтром. При смене каталога начните сначала.
2. `ensure_document_map(files=["guide.md", "values.yaml"], root="/absolute/documents")`.
3. `get_map_job(jobId=...)` до completed/failed/cancelled. Сохранить `mapId` и `snapshotId`.
4. `query_document_tree(mapId=..., snapshotId=...)`: корни. Для потомков передать
   возвращённый `parentId`; для поиска `operation="search", queryText="ClientId"`.
5. Для таблицы использовать возвращённые opaque `nodeId`/`documentId`:
   `read_document_table(..., rowStart=0, rowCount=20, columnStart=0, columnCount=10)`.
6. `read_document_content(..., offset=0, limit=4000)` возвращает `nextOffset`.
   Offset измеряется Unicode codepoints, не UTF-8 байтами и не UTF-16 единицами.
7. `create_document_view(...)` сохраняет ограниченный подграф.

Не вычисляйте ID из пути: одинаковые bytes могут использовать один artifact,
но имеют разные document bindings. Передавайте исходный snapshot во все запросы;
`freshness` относится к текущему оригиналу, `coverage` — к захваченному содержимому.
Табличное окно относительно выбранной таблицы с нуля; sourceLocator сохраняет
реальные A1/OOXML/page/line координаты. Пустое, отсутствующее и null различаются.

Основные HTTP endpoints: POST `/api/document-files`, GET `/api/document-maps`,
POST `/api/document-maps/ensure`, GET `/api/map-jobs/{id}`, POST
`/api/document-maps/{map}/query/{tree|search|node|table|graph|content}`.
Query body содержит `snapshotId` и соответствующие операции поля.
Неизвестные поля отклоняются. Общий job gateway поддерживает и прежние data maps.

## Связь с локальным поиском по коду

Captured Markdown адаптируется к существующему corpus/index без повторного parse.
Ссылки на файлы сопоставляются с raw code IDs только в file scope. Это связь
документа с файлом, а не доказанный вызов метода.

Для зарегистрированного проекта доступны `build_document_search`,
`get_document_search_index`, `search_captured_documents`. Требуются **оба** ID:
`snapshotId` документа и `codeSnapshotId` кода. Индекс изолирован от существующего
code-documentation corpus. Нужна заранее установленная локальная embedding-модель;
при её отсутствии возвращается MODEL_UNAVAILABLE. Ни запрос, ни build не скачивают
модель. Обычный поиск `query_document_tree(operation="search")` модели не требует.

## Хранение, лимиты и поставка

Данные находятся в `<Atlas data>/document-maps/<scope hash>/`: captured blobs по SHA256,
переиспользуемые revisions с SQLite и immutable manifests. Отдельно сохраняются
bindings и views. Jobs восстанавливают статус после restart; скрытого resume нет.
Single-flight разделяет extraction между consumers; отмена одного consumer не
обрывает другой. Ошибка не заменяет предыдущий опубликованный снимок.

Базовые пределы: 50 MiB на файл, 250 MiB на import, текстовые форматы до 5 MiB,
100 000 узлов; ответы до 64 000 UTF-8 bytes. Граф — до 120 nodes/300 edges,
строки/ячейки читаются окном. Worker имеет timeout 30 s, output budget 32 MiB,
наблюдаемый RSS ceiling 512 MiB с проверкой каждые 50 ms. Sampling допускает
overshoot и не является жёстким лимитом ОС. Отдельный процесс не называется sandbox.

POSIX capture использует безопасный openat, Windows capture — закреплённые Win32
handles с отказом reparse points и alternate data streams. Windows-специфичные
проверки запускаются на Windows; их выполнение на macOS не заявляется.
Базовые readers предварительно компилируются Cython и поставляются вместе со
встроенным Python runtime и зависимостями. Установленный отдельно Python не нужен;
существующие дополнительные анализаторы Python/.NET/Java сохраняются.
Пакеты собираются отдельно под ОС и архитектуру Python процесса (включая Rosetta).
Wheelhouse для одной платформы не является переносимым набором для остальных.

OCR, legacy DOC/XLS/PPT conversion, live log tail и redacted-only storage не
реализованы. Документы могут содержать конфиденциальные данные: capture сохраняет
оригинальные bytes локально. После успешного обновления сохраняются последние три
снимка каждой карты, текущий снимок и снимки, закреплённые видами, поиском или
исследованиями. Общие revisions/blobs удаляются только после проверки ссылок;
активные читатели и импорты удерживают lease и откладывают очистку.
Неизвестная схема ссылок блокирует удаление. POST
`/api/document-maps/{map}/retention` с `{"dryRun":true}` возвращает предварительный
расчёт; `false` запускает очистку. Не удаляйте revisions по возрасту вручную.

Каталог данных находится отдельно от заменяемого приложения: macOS —
`~/Library/Application Support/Code Atlas`, Windows — `%LOCALAPPDATA%/Code Atlas`,
Linux — `$XDG_DATA_HOME/code-atlas` (по умолчанию `~/.local/share/code-atlas`).
`ATLAS_DATA_DIR` задаёт свой каталог. Обычное обновление совместимой схемы проверяет
lock и marker версии на месте, без полного ZIP или копирования этого каталога.
Несовместимая схема требует отдельной миграции; установщик её молча не выполняет.

Зависимости закреплены в `requirements-documents.in`/`requirements.lock`.
Проверка offline разрешения: `.venv/bin/python -m pip install --dry-run
--ignore-installed --no-index --find-links wheelhouse -r requirements-documents.in`.

Автономная установка описана в [инструкции по установке](install.ru.md).
