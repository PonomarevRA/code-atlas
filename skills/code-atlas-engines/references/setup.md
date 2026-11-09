# Настройка движков

Используй адрес, порт и поля API работающего Atlas. Установленный адаптер определяет поддерживаемые версии; проверь его diagnostics и фактический probe после установки.

## Python

Профиль `semantic-python` принимает `pythonPath`. Требуется отдельный Python с Jedi и Parso. Проверенные адаптеры Atlas поддерживают Jedi 0.19/0.20, но этот диапазон может меняться вместе с версией приложения.

После разрешения установки создай отдельное окружение в выбранном пользователем каталоге. Для POSIX, подставив реальный путь вместо `ENGINE_ROOT` и поддерживаемый Python, пример выглядит так.

```sh
: "${ENGINE_ROOT:?Set ENGINE_ROOT to the approved directory}"
python3 -m venv "$ENGINE_ROOT/python"
"$ENGINE_ROOT/python/bin/python" -m pip install "jedi==0.19.2"
"$ENGINE_ROOT/python/bin/python" -m pip check
"$ENGINE_ROOT/python/bin/python" -I -c "import jedi, parso; print(jedi.__version__, parso.__version__)"
```

[Jedi 0.19.2](https://pypi.org/project/jedi/0.19.2/) опубликован в PyPI.

На Windows executable окружения находится в `Scripts/python.exe`. Сохрани выбранные точные версии в отчёте. При несовместимости выбери версию, которую принимает действующий адаптер, и повтори probe.

Передай абсолютный executable этого окружения в `set_semantic_engine_paths(modId="semantic-python", mode="patch", paths={"pythonPath":"/chosen/environment/bin/python"})`. Используй соответствующий HTTP endpoint, если MCP недоступен.

## TypeScript и JavaScript

Профили `semantic-typescript` и `semantic-javascript` настраиваются отдельно. Каждый принимает `enginePath` и `nodePath`. Наличие Node само по себе не подтверждает наличие TypeScript Compiler. Текущий Compiler API адаптер поддерживает TypeScript 5.x.

Для разрешённой установки в отдельном каталоге пример с закреплённой версией выглядит так.

```sh
: "${ENGINE_ROOT:?Set ENGINE_ROOT to the approved directory}"
npm install --prefix "$ENGINE_ROOT/typescript" --ignore-scripts --no-audit --no-fund typescript@5.9.3
```

Путь Compiler API и версия примера указаны в [метаданных TypeScript 5.9.3](https://raw.githubusercontent.com/microsoft/TypeScript/v5.9.3/package.json).

Задай `enginePath` как абсолютный путь к `typescript/node_modules/typescript/lib/typescript.js` внутри выбранного каталога и `nodePath` как абсолютный существующий Node executable. Если в проекте нужны оба языка, настрой оба профиля. Project package.json и его lock-файл для такой установки не меняются.

## C# и другие языки

Для `semantic-csharp` разрешены поля `dotnetPath`, `workerPath`, `sdkPath`. Сначала проверь установленный Atlas bridge, SDK, TargetFramework, project references и границы captured source cohort. Ошибка `source-cohort-missing-or-over-budget` может означать отсутствующие хэши либо превышение лимита набора. В проверенной версии адаптера лимит составляет 16 C#-файлов.

При `project-context-requires-msbuild`, `literal-target-framework-required` и неполном build context покажи конкретную причину и требуемую доработку интеграции. Обход проверки или самовольное расширение лимита не устанавливают достоверную компиляцию. Сборка анализируемого проекта, restore пакетов и запуск его scripts требуют собственного основания в задаче.

Профили других языков выбирай по `profiles` и `fields` текущего API. Для Swift/C++ проверяй контекст реального target. Работоспособность на одном языке не подтверждает остальные.

## HTTP и проверка результата

Прочитай `GET /api/mods/{modId}/semantic-engine-diagnostics?projectId={projectId}`. Разрешённые поля находятся в ответе. Сохрани выбор через `POST /api/mods/{modId}/semantic-engine-settings`.

```json
{"mode":"patch","paths":{"pythonPath":"/chosen/environment/bin/python"}}
```

Пример выше относится к semantic-python. `patch` сохраняет другие поля. Пустая строка возвращает отдельное поле в автоматический режим. `replace` с пустыми paths сбрасывает весь профиль.

Для уже выбранного снимка отправь `POST /api/projects/{projectId}/semantic-enrichment`.

```json
{"snapshotId":"actual-snapshot-id","nodeIds":["actual-raw-node-id"],"force":true}
```

Дождись завершения через GET того же endpoint с snapshotId. Сверь queryCount и опубликованные факты. При смене снимка выбери актуальный ID и повтори проверку. Отчёт должен отделять наличие, доступность, выполненный анализ, принятые факты и новую подтверждённую топологию.
