# Очистка и удаление карт / Clear snapshots and remove maps

## Русский

Откройте **Техническая страница → Хранилище карт**. Отметьте карты кода или
данных и выберите действие. Ничего не отмечается автоматически.

- **«Очистить снимки — оставить запись»** удаляет снимки, сохраняя регистрацию
  карты для последующего построения.
- **«Удалить выбранные карты»** удаляет снимки и регистрации выбранных карт
  одной подтверждённой операцией. Завершённые карты исчезают из списка.

Оба действия сохраняют исходники и авторские виды. Если неизвестные файлы
мешают безопасной очистке, удаление карты также блокируется. Для отдельной
уборки записи без удаления файлов используйте кнопку в её строке.
Перед подтверждением проверьте список, даты, объём и предупреждения.

Оба действия удаляют текущие и предыдущие снимки **только выбранных карт**, включая
их сгенерированные графы и принадлежащие проекту объекты хранения. Отмены после
успешного удаления нет. Для ручной резервной копии используйте отдельную команду
backup при остановленном Atlas; очистка сама не создаёт скрытую копию всех карт.

Сохраняются:

- исходники и управляемые клоны репозиториев;
- при очистке снимков — записи проектов и карт данных для явной пересборки;
- сохранённые авторские виды кода и данных;
- карты документов, исследования, модели, настройки и резервные копии.

После **очистки снимков** карта остаётся в списке в состоянии **«Не построена»**.
Для кода запустите построение заново; для карты данных запросите обновление.
У сохранённого вида остаётся историческое содержимое, но переходы к удалённому
снимку или прежние внешние Graphify/MCP-пути могут больше не работать.
Обновите другую вкладку, если она продолжает показывать ранее загруженную карту.

Дата и версия, если она есть в сохранённых метаданных, помогают выбрать карты.
**Неизвестная версия не означает устаревший алгоритм**. Расположение файлов и
формат хранения также не доказывают актуальность анализа.

При занятом хранилище дождитесь завершения анализа/импорта и повторите просмотр.
Если карта изменилась после просмотра, прежнее подтверждение отклоняется:
нужно проверить новый список и подтвердить его отдельно.

При разрыве соединения сначала проверьте результат той же операции. Повтор
незавершённой очистки относится только к её собственным временно перемещённым
данным: он не должен удалять новую карту, построенную позже. При частичной
ошибке каталог уже может быть очищен, а часть файлов ещё ожидать удаления.
Не считайте предварительный объём фактически освобождённым местом; результат
показывает незавершённую очистку. Размеры файлов логические, не число свободных
блоков APFS/сжатой файловой системы.

Раздел **«Незавершённые очистки»** позволяет найти подтверждённые операции после
перезагрузки страницы или перезапуска сервера. Сервер может продолжить удаление
уже подтверждённых временно перемещённых данных при запуске, включая частичную
очистку. Просмотр состояния ничего не запускает; ручной повтор требует отдельного
действия пользователя. Если операция
уже завершилась, открытая вкладка проверяет её по сохранённому идентификатору.

Для агента: эта возможность не разрешает самостоятельно выбирать и удалять
существующие пользовательские карты. Сначала получите явный выбор карт и
подтверждение последствий. Доступен тот же same-origin API: предварительный
просмотр `POST /api/technical/map-cleanup/preview`, подтверждённая операция
`POST /api/technical/map-cleanup/{planId}/execute`, чтение результата
`GET /api/technical/map-cleanup/{planId}`. Передаются тип и ID карты, а не пути
файлов. Идентификатор плана закрепляет проверенный набор данных и действие:
`clear-snapshots` (по умолчанию), `delete-map` или `forget-registration`.
При `delete-map` флаги результата `mapsCleared` и `registrationRemoved`
подтверждают разные последствия. При незавершённом удалении проверяйте ту же
операцию; старое подтверждение не удаляет новую регистрацию с тем же ID.

Офлайн-команда `--optimize-storage` остаётся отдельной операцией: она очищает
восстанавливаемые кеши и не удаляет постоянные снимки.

### Потерянный снимок и удаление записи

Если опубликованная папка снимка удалена вручную, открытие текущей карты или
поиск без закреплённой ревизии запускает одну попытку восстановления из исходной
папки. Повторные обращения используют то же задание. После ошибки или прерывания
повторную сборку нужно запустить явно; список карт и исторические запросы ничего
не перестраивают. После намеренной очистки снимков автоматической сборки нет.
Перемещённая или подменённая ссылкой исходная папка требует явного исправления.

Кнопка **«Удалить запись из списка»** — отдельное действие с подтверждением.
Она удаляет регистрацию, сохраняя исходники, файлы снимков и авторские виды.
Поэтому удаление записи не освобождает объём снимков. При неизвестных файлах
обычная очистка по-прежнему блокируется; удаление записи не обходит этот запрет.

## English

If a published snapshot folder is manually removed, opening the current code map
or searching without a pinned revision schedules one recovery attempt. Repeated
requests reuse that job. A failed or interrupted attempt requires an explicit
rebuild; pinned/history and catalog reads do not schedule repair. Deliberately
cleared snapshots remain unbuilt. Missing or redirected source roots require
explicit correction. **Remove entry from list** separately confirms removal of
the registration only: sources, snapshot files and authored views remain. It does
not reclaim snapshot storage or bypass the unknown-file cleanup safeguard.

Open **Technical page → Map storage**, select code/data maps and choose an action.
**Clear snapshots — keep entry** retains registrations for rebuilding;
**Delete selected maps** removes both snapshots and registrations in one confirmed
operation. Sources and authored views remain with either action. Unknown-file
checks also apply to deletion; removing an entry alone does not bypass cleanup.
Selection is never automatic. Review the exact maps, dates, size and warnings
before confirming. Cleanup removes current and historical generated snapshots
of selected maps, including their owned graph objects. Successful removal cannot
be undone; use the separate stopped-server backup command if a backup is needed.

Source repositories and managed source clones are preserved. With snapshot clearing,
code/data registrations remain available for rebuilding. With map deletion, their
entries disappear from the catalog. Authored code/data views, document maps, research,
models, settings and backups remain. Cleared maps show **Not built**; explicitly
build code maps or refresh data maps to analyze them again. Historical views remain
readable, but links to deleted snapshots and old external Graphify/MCP paths may
no longer work. Refresh another tab if it still shows previously loaded content.

Use stored dates and known version metadata to choose maps. Unknown versions and
legacy storage layouts do not prove that an analysis algorithm is obsolete.
Busy work blocks cleanup; a changed snapshot invalidates the old preview and
requires a new review and confirmation.

After a lost acknowledgement, query the same operation before retrying. A retry
of incomplete purge addresses only that operation's owned staged data, never a
newly rebuilt map. Partial failure may leave physical data pending even after the
catalog is reset. Preview size is not measured freed disk space; logical file bytes
also differ from allocated blocks on compressed or cloned filesystems.

**Unfinished cleanup operations** exposes confirmed operations after a page or
server restart. Startup may resume already-confirmed staged data, including a
partial purge. Reviewing a receipt starts no work; a manual retry requires an
explicit action. An existing tab can check its saved operation ID even if cleanup
has already completed.

Agents must not independently choose existing user maps for deletion. Require an
explicit map selection and confirmation. The same-origin preview/execute/status
API above accepts map kind/ID, never arbitrary filesystem paths. The offline
`--optimize-storage` cache operation remains separate from snapshot cleanup.

The preview API binds `action` (`clear-snapshots` by default, `delete-map`, or
`forget-registration`) to the operation ID. For combined deletion, inspect both
`mapsCleared` and `registrationRemoved`. Checking or replaying an old operation
must not remove a new registration with the same ID.
