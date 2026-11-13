# Подготовка репозиториев / Preparing repositories

## GitHub Releases

Опубликуйте исходники в каждой редакции и отправьте тег `v<version>`, где
`version` совпадает с `packaging/desktop-product.json`. GitHub Actions собирает
нативные установщики из исходников этого тега. Публичная сборка проходит аудит
экспорта перед упаковкой.

Для проверенной локальной сборки запустите из checkout соответствующего тега
`python tools/publish_native_release.py --repo OWNER/REPO --tag v<version> --assets PATH`.
Каталог должен содержать установщик, его `.sha256` и `.json` от
`packaging/build_installer.py`. Флаг `--check` проверяет файлы без сети.
Для публикации нужен авторизованный GitHub CLI. При повторном запуске скрипт
добавляет отсутствующие платформы и сохраняет ранее опубликованные установщики.

After pushing the source commit for each edition, push the matching `v<version>`
tag to build native installers in GitHub Actions. Run the same publisher from
the tagged checkout to add a verified local build. Use a new version and tag
when releasing changes to the application.

## Русский

Установка описана в [install.ru.md](install.ru.md); задание локальному агенту —
в [INSTALL_AGENT.md](../INSTALL_AGENT.md). Обе версии содержат инструкции
по установке и [готовые навыки](../skills/README.md).

### Приватная версия

Соберите её в отдельном клоне выбранной ветки с её историей. Добавьте текущие
исходники, внутреннюю документацию и проектную память из проверенных реестров.
Включите ресурсы проектных навыков, если они перечислены в их управляемых
манифестах. Рабочие окружения, кэши, снимки и данные установленных экземпляров
остаются локально. Используйте Git checkout или bundle. Архив `git archive`
может исключать часть приватных исходников по правилам `.gitattributes`.
До отправки проверьте целевое назначение, его видимость и права доступа.
Проверьте известные форматы секретов в новом снимке и сохраняемой истории.

### Публичная версия

Рабочий checkout может содержать личную историю, локальную память и результаты
проверок. **Не отправляйте его историю в новый публичный репозиторий.**
`.gitignore` не удаляет уже отслеживаемые файлы и старые коммиты.
`.gitattributes` ограничивает архив, но не переписывает Git history.

Из корня рабочей копии с Python 3.12/3.13 выполните:

```sh
python3 tools/public_release.py audit
mkdir -p outputs/public
python3 tools/public_release.py export --destination outputs/public/code-atlas
python3 tools/public_release.py audit --root outputs/public/code-atlas --tree
```

В Windows используйте `python` вместо `python3` и создайте каталог командой
`New-Item -ItemType Directory -Force outputs/public`. Остальные параметры CLI те же.
Назначение экспорта должно быть новым: существующие файлы не заменяются.
Если исходники меняются при копировании, повторите экспорт в новый каталог.
При ошибке копирования может остаться неполный каталог: не публикуйте его и
не считайте наличие manifest признаком завершения. Успех подтверждают код возврата
команды и отдельный аудит; существующее назначение автоматически не удаляется.

Экспорт включает текущие отслеживаемые и новые неигнорируемые файлы из разрешённых
разделов. Он исключает `.git`, локальные настройки агентов, рабочую память,
отчёты, снимки, данные, резервные копии, окружения, результаты сборки и личные
скриншоты. Вместо рабочей памяти экспорт включает минимальный проверяемый
реестр и пример факта со ссылкой внутри репозитория.
Для веб-интерфейса экспорт использует закреплённую MIT-версию компонентов
AG Design. Прежние npm-архивы, расширение захвата вкладок и SDK AG Privacy
остаются в приватной поставке. Публичный Atlas запускается в браузере и
поддерживает карты локальных репозиториев и MCP.
Исходные локальные материалы не удаляются. Список и SHA256 каждого включённого
файла записываются в `PUBLIC_SOURCE_MANIFEST.json`; отдельный аудит проверяет его.

Для известных личных имён/проектов можно передать `--forbidden-terms` с путём
к локальному JSON-массиву строк. Храните этот файл вне публичных исходников,
например в игнорируемом `outputs/`; не включайте туда реальные секреты.
Значения найденных токенов не выводятся. Проверяются известные форматы ключей,
домашние пути и контакты; новые бинарные файлы требуют точного review pin в
`packaging/public-binaries.json`. Имена и содержимое npm-архивов проверяются
в памяти с ограничениями размера и без извлечения путей на диск.

Это проверка известных форматов, а не доказательство отсутствия любых скрытых
данных. Отдельно просмотрите новые изображения и происхождение бинарных файлов.
Лицензии и авторство сторонних компонентов сохраняются рядом с ними.
Код Atlas распространяется по [MIT](../LICENSE), выбранной владельцем проекта;
[стандартный текст MIT](https://opensource.org/license/mit).
У сторонних компонентов сохраняются отдельные условия: см. [NOTICE](../NOTICE)
и исходные уведомления рядом с ними.

Для GitHub используйте только проверенный экспорт: перенесите его в отдельный
каталог, выполните `git init` и настройте выбранную публичную Git-идентичность.
Перед первым коммитом проверьте `git status` и повторите аудит с `--published`.
Перед отправкой сверьте состав первого коммита с проверенным экспортом
и убедитесь, что его история начинается с одного корневого коммита.
Публикуйте эту историю только в проверенное публичное назначение.
CI `public-source.yml` проверяет текущие файлы; он не очищает ранее опубликованную
историю. Если реальный секрет уже был опубликован, одного нового коммита с
удалением недостаточно: отзовите его у поставщика и отдельно очистите историю.

## English

[User installation](install.en.md) and [agent installation](../INSTALL_AGENT.md#english)
apply to both repositories. Both include the [ready-to-install skills](../skills/README.md).

### Private edition

Prepare a separate clone of the selected branch with its history. Include current
source, internal documentation and project memory selected through its verified
registries. Include project skill resources listed in their managed manifests.
Keep environments, caches, snapshots and installed-instance data local. Use a Git
checkout or bundle. `git archive` can omit private source under `.gitattributes`.
Check the exact destination, its visibility and your access before sending anything.
Scan the new snapshot and retained history for known credential formats.

### Public edition

Do not push a private working checkout's history to a new public repository.
Ignore rules do not untrack committed files or remove previous commits; archive
attributes do not rewrite history. Run the commands above (use `python` and a
PowerShell directory-creation command on Windows). The destination must be new.
A failed copy can leave an incomplete directory. Do not publish it or treat a
manifest alone as completion; require successful exit and a separate tree audit.
An existing destination is never automatically removed.

The exporter includes current tracked and nonignored new source in explicitly
allowed areas. It excludes Git metadata and history, agent settings, working memory,
research evidence, screenshots, runtime data, snapshots, backups, environments and
build products. A minimal registry and example fact use a repository-local
reference. The web UI uses a pinned MIT release of AG Design components.
The previous npm archives, tab capture extension and AG Privacy SDK stay in
the private edition. The public Atlas still runs in a browser and provides
local repository maps and MCP. Local originals remain intact. Every exported file is recorded
by SHA256 in `PUBLIC_SOURCE_MANIFEST.json`; audit the exported tree separately.

Optional `--forbidden-terms` accepts a local JSON string array of private names or
project identifiers. Keep it outside the public payload; do not put actual secrets
in it. Reports omit matched values. Known key, home-directory and contact formats are checked;
changed/new opaque binaries require exact reviewed pins. Npm member names and
contents are inspected with size limits and without filesystem extraction.

Pattern checks and hashes cannot prove that arbitrary hidden data is absent.
Review new images, binary provenance and third-party terms. Preserve supplied
license/copyright notices. Atlas source uses the owner-selected [MIT license](../LICENSE),
following the [standard MIT text](https://opensource.org/license/mit).
Third-party terms remain separate: see [NOTICE](../NOTICE) and adjacent vendor notices.

Move the verified export to a separate directory, initialize a fresh Git repository,
choose your public commit identity and inspect `git status` before the first commit.
Run `python3 tools/public_release.py audit --published`. Compare the initial commit
with the audited export and confirm it is the single root of the new history.
Publish that history only to the verified public destination.
The included CI checks current files, not previously published history. Revoke any
actually exposed credential at its provider and handle historical cleanup separately.
