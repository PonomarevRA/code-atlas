# Временная карта рабочего checkout

Используй для задачи агента отдельную Atlas-карту, даже если постоянная карта
этого пути уже существует. Разные агенты/задачи получают разные папки.

1. Проверь discovery `ensure_checkout_map` и checkout-tools. Вызови ensure с
   абсолютным checkout и устойчивым уникальным `ownerKey` текущей задачи/агента
   (`A-Za-z0-9_.:-`, до100символов). Если ID чата недоступен, сгенерируй ключ один
   раз и сохрани его в рабочем контексте; не генерируй новый при каждом retry.
2. Сохрани `sessionId`, `path`, `expiresAt`, `projectId`. ID проекта может совпадать
   у двух сессий одного пути: каждая операция обязана передавать **sessionId**.
   ownerKey — заявленная область задачи, не аутентификация.
3. Пока карта строится, `search_checkout_file(sessionId, path, query?, limit?)`
   ищет типы и вызываемые объявления в одном известном UTF-8 файле до 1 МиБ.
   Передай точный путь от корня checkout через `/`. Запрос ищет подстроку имени
   с учётом лексического владельца без учёта регистра. Сохраняй `sourceHash`,
   `previewRevision`, координаты и `coverage`. Пустой или частичный результат
   относится к этому файлу. Если поиск по графу без `snapshotId` вернул
   `state=building`, используй `nextCalls`. Проверяй `get_checkout_map` с тем же
   `sessionId` после `retryAfterMs` до `project.status=completed` и доступного
   снимка. При ошибке изменения исходников
   закончи серию правок и явно вызови `rebuild_checkout_map`.
4. Найди raw-узлы через `search_checkout_nodes`; закрепи snapshotId. Прочитай
   `describe_checkout_node`, проверь sourceProvenance/readabilityRecommendations.
   Empty/unavailable рекомендации не доказывают читаемость.
5. Перед правками измерь затрагиваемые файлы `get_checkout_code_health` в режиме
   working-tree и подготовь `plan_checkout_code_changes` из полного draft либо
   ranges с baseline healthIdentity. После правок измерь вновь и сравни через
   `compare_checkout_code_health`. Unknown/truncation сохраняй; оценки не заменяют тесты.
6. После законченной серии выполни один rebuild этой временной карты и перепроверь
   изменённые символы. Исходники checkout общие: параллельное изменение файла может
   отклонить сборку; изоляция карты не разрешает Git/редакторские конфликты.
7. Закрой через `finish_checkout_map`, когда карта больше не нужна, либо оставь до
   expiry. В настройках → «Временные карты checkout» доступны путь, срок и удаление.

TTL — ровно3600секунд после успешного полезного чтения. GET list/status и повторный
ensure не продлевают его. Не создавай keepalive, heartbeat или фоновый touch.
Просроченный ID недоступен; новый ensure может создать новую папку. Удаление
сразу запрещает новые обращения, но физическая очистка ждёт принятые readers и
builder. `deleting`/`cleanup-failed` не выдавай за завершённое удаление.

При HTTP 429 проверь `list_checkout_maps`, включая страницы по `nextOffset`.
Если есть `reclaim.eligible=true`, повтори запрос через
`ensure_checkout_map_with_reclaim` с теми же checkout и ownerKey. Atlas повторно
проверяет кандидатов и при полном каталоге вытесняет одну временную карту с
самым долгим простоем от 20 минут, пропуская карты с активным чтением или
сборкой. Сохрани новый sessionId и опроси статус. Поле `reclaimed` сообщает ID
и время простоя удалённой карты. Её временные результаты анализа удаляются.
Если все карты заняты или использовались недавно, заверши свою ненужную сессию
или дождись освобождения слота.

Если новая MCP surface недоступна, проверь локальный HTTP Atlas с тем же origin,
без proxy/redirect. `/api/checkout-sessions` принимает POST
`{"checkout":"<absolute checkout>","ownerKey":"<stable task key>"}`.
POST `/api/checkout-sessions/ensure-with-reclaim` принимает то же тело.
GET `/{sessionId}` — статус; POST `/{sessionId}/rebuild` с JSON `{}` — перестроение;
DELETE `/{sessionId}` — завершение. Ранний поиск POST `/{sessionId}/file-search`
с path/query/limit. Поиск по снимку POST `/{sessionId}/search` с query/snapshotId/limit.
Пока первый снимок строится, поиск без snapshotId возвращает HTTP 409 с кодом
`CHECKOUT_MAP_BUILDING`, `jobId` и `retryAfterMs`. В MCP это результат
`state=building` без списка узлов. Описание POST `/{sessionId}/describe` с snapshotId/nodeId;
health/plan/compare используют существующие Atlas HealthQuery/Plan/Compare DTO.
Все POST требуют application/json. Не угадывай скрытые маршруты.

Если capability отсутствует и в HTTP, явно сообщи ограничение версии/подключения.
Не заменяй временную сессию неоговорённой постоянной регистрацией. Новый source
API не означает, что установленный runtime обновлён; установку квалифицируй отдельно.


## Обзор изменений агента

Если задача требует обзор «До / После», дождись свежей карты **до первой правки**, найди одну функцию и вызови `begin_change_review(sessionId, snapshotId, nodeId, title?)`. Сохрани `reviewId`. После правок перестрой карту один раз, дождись нового снимка и вызови `finish_change_review(reviewId, sessionId, snapshotId)`. Если сессия истекла, используй карту того же checkout с тем же `ownerKey`. Передай пользователю возвращённый локальный URL. `get_change_review(reviewId, offset?)` читает список функций, а браузерный обзор открывает сохранённые пути. Он остаётся доступен после завершения временной карты. Путь статический, частичное покрытие снимков должно остаться видимым.
