# Atlas Health для CI

[English](README.en.md)

Atlas Health сравнивает две точные Git-ревизии и показывает локальные метрики методов, затронутых diff. Пилот поддерживает C#, Python, Java, JavaScript, TypeScript, Go и Rust. В отчёт входят читаемость R, вложенность, размер, документация, покрытие анализа и замечания с путями и строками. В пилоте графозависимые H, O и T имеют статус «недоступно». Исходный код и фрагменты diff в отчёты не включаются.

Снижение R или оценки документации на пять и более баллов считается регрессией. Если в HEAD для изменения нет строки, например при удалении метода, результат остаётся в артефакте без inline-замечания. Изменение идентичности декларации может отображаться как удаление и добавление.

Пилот работает в рекомендательном режиме. Замечания не проваливают анализ. Ошибка запуска остаётся видимой, но конфигурация задания допускает её. Для GitHub не добавляйте эту проверку в обязательные проверки защиты ветки.

## GitLab

1. Выберите опубликованный полный SHA Code Atlas с этой папкой и модулем `atlas.ci_health`. GitLab runner должен иметь доступ на чтение к репозиторию Atlas по HTTPS и к источнику Python wheels. Если требуется зеркало Atlas, передайте его адрес через вход `atlas-repository`.
2. В отдельный проект на том же GitLab-инстансе положите [шаблон компонента](gitlab/templates/atlas-health.yml) по пути `templates/atlas-health.yml` и зафиксируйте коммит. Для подключения по версии каталога выпустите компонент в GitLab CI/CD Catalog. Коммит шаблона и коммит Atlas могут различаться.
3. Добавьте в `.gitlab-ci.yml` анализируемого проекта блок ниже. Замените путь проекта и обе заглушки. `test` должен быть одним из этапов вашего пайплайна. Без регистрации в каталоге доступен вариант [include:project](gitlab/include.example.yml) с тем же шаблоном.

```yaml
include:
  - component: $CI_SERVER_FQDN/platform/code-atlas-components/atlas-health@REPLACE_WITH_COMPONENT_COMMIT_SHA
    inputs:
      stage: test
      atlas-sha: REPLACE_WITH_ATLAS_COMMIT_SHA
```

В конфигурации проекта должен быть разрешён пайплайн `merge_request_event`. Если проект использует `workflow: rules`, проверьте, что они пропускают это событие. При необходимости настройте `tags` для задания `atlas-health` в конфигурации потребителя.

Задание использует Linux runner с контейнером Python 3.12 и Git. Оно загружает Git-объекты в отдельный bare-репозиторий через стандартный доступ GitLab на чтение. Для detached MR берётся `CI_COMMIT_SHA`, для merged-results и merge train берётся `CI_MERGE_REQUEST_SOURCE_BRANCH_SHA`. База берётся из `CI_MERGE_REQUEST_DIFF_BASE_SHA`. Если runner не может получить точные коммиты, задание сообщает об ошибке без сравнения с другой ревизией.

Откройте артефакты задания `atlas-health` или отчёт **Code Quality** в MR. Для сравнения GitLab ищет отчёт базового пайплайна. Задание на основной ветке публикует пустой Code Quality baseline, поскольку отчёт Atlas предназначен для изменений MR. Для других целевых веток или старой базы без такого пайплайна используйте артефакты. Отображение строк в diff зависит от версии и тарифа GitLab. [CI/CD components](https://docs.gitlab.com/ci/components/) · [Code Quality](https://docs.gitlab.com/ci/testing/code_quality/) · [Выбор базового пайплайна](https://docs.gitlab.com/ci/testing/#baseline-pipeline-selection)

## GitHub Actions

1. Скопируйте [пример workflow](github/atlas-health.example.yml) в `.github/workflows/atlas-health.yml` анализируемого репозитория.
2. Замените `REPLACE_WITH_ATLAS_COMMIT_SHA` на опубликованный полный SHA Atlas с [composite action](github/action.yml). Для закрытого зеркала откройте этому репозиторию доступ к action средствами GitHub.
3. Откройте PR и проверьте вкладку **Checks**, сводку задания и артефакт `atlas-health`.

Workflow использует событие `pull_request` и права `contents: read`. Checkout нужен для Git-объектов, команды из исходников PR не запускаются. Action вычисляет merge base по точным SHA из события и добавляет до десяти замечаний на файлы и строки. Для файлов, которые отличаются между PR HEAD и ревизией проверки, аннотации остаются в полном артефакте с позициями PR HEAD. Сохраните `fetch-depth: 0`, чтобы на runner был доступен общий предок. Пример использует GitHub.com и `actions/upload-artifact` v4, для GitHub Enterprise Server проверьте поддерживаемую версию загрузчика. [Composite actions](https://docs.github.com/en/actions/reference/workflows-and-actions/metadata-syntax) · [Аннотации и сводки](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands)

## Локальное воспроизведение

Запустите из корня проверенного Atlas checkout с Git и Python 3.12. Подставьте точные SHA из MR или PR. Пятый аргумент задаёт новый временный venv.

```sh
work=$(mktemp -d)
sh plugins/atlas-health/run.sh /absolute/path/to/repo BASE_SHA HEAD_SHA "$work/report" "$work/venv"
```

В каталоге отчёта будут `atlas-health.json`, `atlas-health.md` и `gl-code-quality-report.json`. Сводка показывает, какие файлы и методы удалось оценить. При сравнении запусков сверяйте оба SHA и версию анализатора.
