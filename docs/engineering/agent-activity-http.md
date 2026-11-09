# Получение журналов по единому HTTP API

Один стандарт для Atlas и AgMemory: `agent-activity-http/v1`. Клиенту достаточно
base URL запущенной новой версии сервиса. MCP не участвует в этом интерфейсе.
Существующие миничаты продолжают использовать совместимые прежние маршруты.

| Запрос | Назначение |
| --- | --- |
| `GET /api/agent-activity/v1/status` | Размер журнала, сохранённые границы, head cursor и состояние |
| `GET /api/agent-activity/v1/events` | Последняя страница; до 100 событий / 256 KiB |

Полный переносимый контракт, JSON Schema, OpenAPI 3.1 и общие примеры:
[`contracts/agent-activity/http-v1`](../../contracts/agent-activity/http-v1/README.md).
Individual events сохраняют формат `agent-activity/v1`, включая краткие
`request` и `response`; тела памяти и внутренние служебные поля не выдаются.

## Примеры

Подставьте адрес сервиса в `BASE_URL`. Для локального HTTP-клиента Origin не нужен.
Сохраняются loopback/Host и существующие ограничения доступа сервиса.

```sh
curl --fail-with-body "$BASE_URL/api/agent-activity/v1/status"
curl --fail-with-body "$BASE_URL/api/agent-activity/v1/events?limit=20"
```

Ответ страницы содержит `events`, `nextCursor`, `highWatermark`, `direction`,
`oldestSequence`, `hasMore`, `truncated` и `health`.
Порядок событий внутри страницы всегда от старых к новым.

Чтобы продолжить назад, передайте **nextCursor** предыдущей страницы:

```sh
curl --get --fail-with-body "$BASE_URL/api/agent-activity/v1/events" \
  --data-urlencode 'journalEpoch=ПОЛУЧЕННОЕ_ПОКОЛЕНИЕ' \
  --data-urlencode 'beforeSequence=ПОЛУЧЕННЫЙ_NEXT_CURSOR' \
  --data-urlencode 'limit=20'
```

Для получения новых событий используйте `afterSequence` вместо `beforeSequence`.
Начальную точку можно взять из `status.cursor`; это позволит наблюдать только
последующие события. Sequence передаётся строкой десятичных цифр, без преобразования
в JavaScript Number. Для чтения всей доступной истории начните без курсора.

`highWatermark` — конец сохранённого журнала на момент чтения; он не заменяет
`nextCursor`, пока `hasMore=true`. Иначе можно пропустить события между страницами.
Когда forward-страница дочитана, её nextCursor продвигается до highWatermark,
даже если фильтр не нашёл ни одной записи. Такой курсор можно использовать при
следующем опросе. Сам API не запускает polling в фоне.

Общие фильтры: `sessionId`, `operation`, `projectId` (точное совпадение) и `q`.
В общем API `q` — буквальная подстрока с учётом регистра в операции, preview
поиска, кратком запросе или ответе. Это одинаковая семантика Python и .NET.
Фильтр projectId не находит записи AgMemory: у памяти нет code-project ID.
Фильтры не выбирают actor/scope или путь к данным.

## Сброс курсора и ошибки

- **400 invalid_query**: неверные/повторённые/неизвестные параметры. Исправить запрос.
- **403 forbidden**: запрос не прошёл правила локального доступа.
- **409 cursor_expired**: очистка, другое поколение или вытесненная история.
  Ответ содержит `resetCursor`. Клиент явно выбирает новую начальную точку;
  автоматический reset мог бы незаметно потерять историю.
- **503 journal_unavailable**: журнал недоступен; безопасную попытку чтения можно повторить.

Ошибка API содержит `error.code`, фиксированный `message`, `retryable` и
`resetCursor` (null, кроме 409). Host/transport может отклонить запрос раньше API
и вернуть собственное тело; HTTP-статус нужно проверять до разбора JSON.

`truncated=true` обозначает укороченную по байтовому пределу страницу. Следующий
курсор позволяет прочитать оставшееся; API не перескакивает через большие события.
`health.droppedTotal` отдельно отражает потери при сборе журнала. Неизвестные
межпроцессные queue gauges возвращаются как null, а не как нулевые значения.

Read-only reference client: [`agent_activity_http_client.py`](../examples/agent_activity_http_client.py).
В установленной старой сборке эти новые versioned маршруты ещё отсутствуют;
обновление подключения MCP не добавляет HTTP endpoints в исполняемый файл.
