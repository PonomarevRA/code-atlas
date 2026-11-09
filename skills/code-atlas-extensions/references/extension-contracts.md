# Реальные контракты расширений ядра

Для пользовательских модов оформления/поведения/группировки используй
[SDK v1](mods-v1.md). Ниже — внутренние точки ядра Atlas, не необходимость
редактировать исходники для каждого нового мода.

Перед реализацией перечитай названные файлы в checkout Code Atlas: руководство не заменяет проверку текущих сигнатур. Пример ниже — код для нового модуля приложения, а JSON — данные анализируемого проекта. Никакого загрузчика плагинов пример не создаёт.

## 1. Дополнение карты

`atlas/store.py` вызывает `attach_map_modules(graph, project_path)` после `build_graph` и перед `enrich_graph`. `_modules()` в `atlas/map_modules.py` сейчас возвращает два contributor: `skills` и `agent-memory`.

Contributor получает только `root: Path`, не граф. Возвращаемый узел имеет поля `id`, `label`, `kind`, `file`, `line`, `community`, `description`; `file` — путь от корня анализируемого проекта, неизвестные строка и сообщество — `None`. Группировка назначит сообщество позже.

Особенности текущего адаптера:

- `_accept_nodes` отбрасывает отсутствующий и уже занятый ID, но не проверяет остальные поля. Модуль обязан проверить собственную схему.
- В `edges` поле `source` — ID принятого узла **этого contributor**, поле `target` — путь к файлу, уже представленному в карте. Это не API произвольных связей между ID.
- `_resolve_edges` выбирает первый узел для соответствующего пути файла, убирает повторные цели, self-link и отсутствующие цели; оставляет до восьми целей на source. Результат всегда `relation: "documents"`, независимо от поля входной связи. Узел-цель может быть символом из файла, а не отдельным узлом типа `file`.
- Исключение contributor записывается в `metadata.mapModules` со статусом `error` и ограниченным текстом; остальные модули продолжаются. Это изоляция ошибок, не изоляция исполнения кода.
- Contributor выполняется при новой сборке. Изменение его исходника или sidecar требует пересборки индекса, чтобы попасть в опубликованный снимок.

### Пример: проверяемые заметки из одного JSON

Схема `docs/code-atlas-notes.json` в анализируемом проекте:

```json
[
  {
    "id": "cost-change-entry",
    "title": "Вход расчёта изменения стоимости",
    "description": "Заметка разработчика о контракте входа; не доказательство выполнения.",
    "source": "service/Controllers/CostChangeController.cs"
  }
]
```

Замени `source` на существующий путь из своего проекта. Пример проверяет содержимое целиком: максимум 64 KiB и 100 заметок. Отсутствующий sidecar даёт пустой вклад; ошибочный JSON, запись, повтор ID или небезопасный путь — ошибку модуля. До чтения ограничивается путь sidecar, после чтения — формат. Проверка `resolve()` препятствует обычному выходу через symlink; она не создаёт защиту от одновременной враждебной подмены файлов.

Новый файл **в Code Atlas**: `atlas/example_notes.py`:

```python
"""Заметки из ограниченного JSON; анализируемый проект остаётся только для чтения."""

import json
import re
from pathlib import Path, PurePosixPath

SIDECAR = "docs/code-atlas-notes.json"
MAX_BYTES = 64 * 1024
MAX_NOTES = 100


def _inside_root(root: Path, relative: str) -> Path:
    """Проверяет repo-relative путь и запрещает выход через .. или symlink."""
    path = PurePosixPath(relative)
    if not relative or "\\" in relative or ":" in relative:
        raise ValueError("Ожидается относительный POSIX-путь")
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Путь выходит за корень проекта")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Путь выходит за корень проекта")
    return resolved


def _text(record: dict, key: str, limit: int) -> str:
    """Читает обязательную непустую строку без молчаливого усечения."""
    value = record.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Некорректное поле {key}")
    return value.strip()


def contribute_notes(root: Path) -> dict:
    """Возвращает до 100 узлов заметок и documents-ссылки; пишет только вызывающий адаптер."""
    root = root.resolve()
    sidecar = _inside_root(root, SIDECAR)
    try:
        with sidecar.open("rb") as stream:
            content = stream.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return {"nodes": [], "edges": []}
    if len(content) > MAX_BYTES:
        raise ValueError("Файл заметок превышает 64 KiB")
    records = json.loads(content.decode("utf-8"))
    if not isinstance(records, list) or len(records) > MAX_NOTES:
        raise ValueError("Ожидается массив не более 100 заметок")
    nodes, edges, used = [], [], set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Заметка должна быть объектом")
        local_id = _text(record, "id", 64)
        if not re.fullmatch(r"[a-z][a-z0-9-]*", local_id) or local_id in used:
            raise ValueError("ID заметки некорректен или повторяется")
        used.add(local_id)
        title = _text(record, "title", 80)
        description = _text(record, "description", 600)
        source = _text(record, "source", 500)
        source_path = _inside_root(root, source)
        if not source_path.is_file():
            raise ValueError("Исходный файл заметки отсутствует")
        node_id = f"map-note:{local_id}"
        nodes.append({
            "id": node_id, "label": title, "kind": "note", "file": SIDECAR,
            "line": None, "community": None, "description": description,
        })
        edges.append({"source": node_id, "target": PurePosixPath(source).as_posix()})
    return {"nodes": nodes, "edges": edges}
```

При реализации выбери уникальный префикс ID модуля; `map-note:` здесь учебный. Конфликт с уже занятым ID будет отброшен адаптером. Отсутствие `documents` после сборки возможно, если исходный файл существует на диске, но не представлен в Graphify-карте.

Регистрация: импортируй `contribute_notes` из `.example_notes` в `atlas/map_modules.py` и добавь запись в **существующий** tuple `_modules()`, сохранив два прежних элемента:

```python
{"id": "example-notes", "label": "Заметки проекта", "contribute": contribute_notes},
```

Перезапусти backend, пересобери индекс. Проверь `metadata.mapModules`, наличие узла и `documents` к исходному файлу. Тип `note` будет доступен через общий фильтр типов; специальное оформление или анализ метода для этого типа не появляется автоматически. Если нужен новый источник call edges, расширяй соответствующий анализатор и его контракт, а не этот адаптер документов.

### Проверки contributor

Новый `tests/test_example_notes.py` должен отдельно проверять `contribute_notes` и реальный `attach_map_modules` после регистрации. Используй `tmp_path`, реальный source-файл и граф с его узлом. Проверь точные ID/поля/цель `documents`, сохранность прежних узлов и рёбер, повторный запуск с новым одинаковым входом. Добавь пустой/missing sidecar, невалидный JSON, >64 KiB, >100 записей, повтор ID, занятый graph ID, `../`, абсолютный путь и symlink наружу. Проверь, что ошибка нового модуля видна в metadata и не мешает `skills`/`agent-memory`. Докажи отсутствие записи в исходники сравнением содержимого fixture-файлов до/после.

Из корня Code Atlas:

```sh
.venv/bin/python -m pytest tests/test_example_notes.py tests/test_map_modules.py
```

`test_example_notes.py` появляется только после реализации примера; сейчас это инструкция для автора, не существующий тест.

## 2. Провайдер группировки

В `atlas/grouping_providers.py` контракт `GroupingProvider = Callable[[nx.Graph], list[set[str]]]`. `_PROVIDERS[id] = (function, label, description)`, `list_grouping_algorithms()` публикует каталог через `/api/grouping-algorithms`; неизвестный ID вызывает `ValueError`.

Провайдер получает **взвешенный неориентированный граф**, построенный `atlas/grouping.py::_weighted_graph`: направления, параллельные связи и отношения уже агрегированы в вес. Обычный провайдер не получает словари исходных узлов (`file`, `kind`, `codeProjectId`). Исключение `graphistry-structure` отдельно добавляет эти атрибуты и читает аннотации назад. Для нового алгоритма, которому нужны исходные атрибуты, согласуй отдельное обогащение и тесты; не предполагай их наличие.

Простейший учебный провайдер — компоненты связности, включая изоляты:

```python
def connected_partitions(graph: nx.Graph) -> list[set[str]]:
    """Группирует связанные компоненты; не меняет входной граф и его атрибуты."""
    return [set(component) for component in nx.connected_components(graph)]
```

Добавь функцию в отдельный backend-модуль и зарегистрируй tuple в `_PROVIDERS`. `enrich_graph` сам создаёт community ID из состава, подписи, координаты и сводные связи. Собственные community ID провайдер не возвращает. Все узлы должны входить ровно в одну непустую partition: обёртка не исправляет произвольные ошибки покрытия. При графе без рёбер обычный провайдер не вызывается: `enrich_graph` выделяет каждый узел отдельно.

Сохраняй исходный граф, фиксируй seed при случайном алгоритме и проверяй перестановки входа/разный `PYTHONHASHSEED`. Убедись в конечных координатах, корректных изолятах и устойчивости пустого графа. Каталог/API и список провайдеров в тестах нужно расширить согласованно. Если меняется семантика существующего алгоритма, обнови `GROUPING_VERSION` в `atlas/grouping.py`: кэш зависит от неё, ID алгоритма и размера/mtime источника, не от исходника функции.

```sh
.venv/bin/python -m pytest tests/test_grouping.py tests/test_grouping_providers.py tests/test_grouping_cache.py
```

## 3. Провайдер отображения

`web/display-providers.js` содержит `{id, label, projectBlocks, bundles}`. `displayProvider()` возвращает `topology` при неизвестном ID; `web/app.js` строит select из этого списка и использует flags возможностей. Каталог не содержит фабрику renderer.

Для нового renderer понадобятся отдельный JS-модуль и dispatch в `web/graph.js`. Сверяй со связным примером `FlowRenderer` в `web/flow-renderer.js`:

- `GraphView.renderOverview` и `render` передают `{mode: 'overview'|'nodes', items, edges, selected}`. Не путай community ID обзора и raw ID символов.
- `renderExpansion` передаёт выбранный display provider отдельному expansion view. Проверь отображение окружения, а не только главной сцены.
- `setSelected` и `setSearchMatches` имеют отдельные ветки для `flow`. Подключи эквивалентные ветки нового renderer; выбор и поиск не должны перекладывать карту или сбрасывать камеру.
- Устанавливай геометрию через `GraphView.installGeometry`, обновляй `nodeElements`/`communityElements`, очищай прежние элементы и скрывай устаревшие tooltip. Это нужно для fit, ingress, selection и minimap.
- Передавай `onSelect(rawId)`/`onCommunity(groupId)` без новых выдуманных raw ID. Пользовательские строки вставляй через `textContent`, а не HTML.
- Сохрани направления и количество показанных связей, ограниченную геометрию и явную маркировку статических циклов. Порты и arrowhead должны попадать на границы карточек.
- Сохрани focus/Enter/Space выбора и стрелки панорамирования `GraphView`; не перехватывай ввод в полях, Alt+Left браузера и модификаторы. Панорамирование не создаёт новые semantic history entries.
- Сериализация display ID использует `displayProvider` в `web/navigation-history.js`. Проверь URL, reload, Back/Forward, смену вида и восстановление камеры. `renderPathCards` — отдельная сцена пути исполнения, не вариант обычного `FlowRenderer`; её поддержку нельзя считать автоматически готовой.

Новый тест строится на `tests/svg_fixture.mjs` и реальном `GraphView`, как `tests/test_flow_renderer.mjs`. Сравни JSON входных nodes/edges до/после, точные callback ID, переключение старого/нового renderer, overlays выбора/поиска, конечные bounds и состояние камеры. Подключи новый Node-тест pytest wrapper по образцу `tests/test_frontend_flow.py`, чтобы он выполнялся в общем CI.

```sh
node tests/test_flow_renderer.mjs
.venv/bin/python -m pytest tests/test_frontend_flow.py tests/test_frontend_navigation_history.py tests/test_frontend_navigation_integration.py tests/test_frontend_keyboard_pan.py
git diff --check
```

После тестов осмотри реальное отображение в браузере: длинные подписи, fanout, циклы, поиск, ingress, раскрытие, разные масштабы, клавиатура и история. Укажи фактически проверенный набор. Успех тестов существующего `flow` ещё не доказывает работу нового renderer.
