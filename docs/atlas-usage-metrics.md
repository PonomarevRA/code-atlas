# Метрики использования Atlas

Метрики принадлежат ядру; модам не нужна зависимость от Observer или код для отправки телеметрии. Снимок ограничен 192 агрегатами, 128000 байтами и фиксированными техническими labels. Он хранится в памяти процесса и не отправляется во внешние сервисы.

Локальные read-only endpoints с проверкой peer/Host/Origin:

- `/api/diagnostics/v1/metrics`: существующий `service-diagnostics/v1` с необязательным блоком `usage: atlas-usage/v1`.
- `/api/diagnostics/v1/prometheus`: текст Prometheus с теми же измерениями.

| Семейство | Что измеряет |
| --- | --- |
| `atlas_mod_calls_total` | Реальные callback-вызовы `semantic_probe`, `semantic_analyze`, `grouping`, по mod/operation/outcome |
| `atlas_mod_duration_seconds` | Гистограмма длительности callback-вызова, секунды |
| `atlas_mod_in_flight`, `atlas_mod_last_completed_timestamp_seconds` | Текущие callback-вызовы и время последнего завершения |
| `atlas_mod_cache_total` | Попадания в кэш discovery; компилятор при этом не запускается |
| `atlas_mod_rule_passes_total` | Выбранные декларативные source rules для прохода; не число callbacks или совпадений |
| `atlas_semantic_facts_total` | Полученные факты по confidence; повторный анализ считается повторно, это не уникальные связи карты |
| `atlas_http_requests_total`, `atlas_http_request_duration_seconds`, `atlas_http_in_flight` | API-запросы, длительность и работа в процессе; опрос диагностики/журналов исключён |
| `atlas_usage_dropped_observations_total` | Пропущенные наблюдения при достижении бюджета метрик |
| `atlas_usage_process_started_timestamp_seconds` | Начало процесса и граница сброса counters |

`outcome`: success/error/cancelled/unavailable; cache_hit используется только кэшем. Возвращённые движком failed/cancelled/unsupported учитываются как error/cancelled/unavailable. Partial означает завершённый callback с неполным статическим покрытием, а не полный семантический анализ.

HTTP labels содержат категорию маршрута и заявленный вид клиента из allowlist (`http`, `codex`, `cursor`, `claude`, `mcp_unknown`). Это не аутентификация и не идентификация агента. URL, query, body, тексты ошибок, исходный код, идентификаторы проектов/сессий, имена и токены в usage не включаются. Mod — технический ID пакета без версии/пути/provider suffix.

Counters живут до перезапуска. Prometheus rate/increase учитывает наблюдаемые сбросы; вызовы между последним scrape и остановкой могут быть потеряны. Это наблюдаемость, не точный аудит или бухгалтерия. Пустой каталог не даёт фиктивных запусков. Ошибки необязательных измерений не меняют результат/исключение/отмену анализируемой операции.

Observer проверяет всю форму блока до сохранения, копирует producer counters без повторного суммирования и добавляет `source` со своим стабильным ID. Неизвестный контракт, отсутствие блока и устаревший источник дают `ag_observer_atlas_usage_available=0`; counters при этом отсутствуют, а независимые измерения RAM/диска продолжают работать. Пакет Observer содержит переносимую копию `usage_contract.py`, без runtime-зависимости от Atlas; при изменении схемы обновляются обе копии и contract-тесты.

В Grafana панель **«Atlas: использование»**, UID `ag-observer-atlas-usage`. Новый seed создаётся только при отсутствии UID; существующий пользовательский вариант, включая locked/read-only, не меняется. Генератор `tools/build_atlas_usage_dashboard.py` пишет только этот новый JSON. Ручная визуальная/accessibility-проверка интерфейса отдельно от серверных и структурных тестов.
