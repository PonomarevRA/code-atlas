# Стабильный MCP facade перед ядром

Transport и tool contract принадлежат facade; прикладные вычисления выполняет независимо обновляемое ядро.

ID: `stable-mcp-facade` · версия: `1.0.0` · статус карточки: **draft**.

Content hash: `545207048bf537748217c0ca51e017396388787247b05efd67d9f2cfcdea33b9`.

Catalog version: `3b1765c0fa5449368237cddb8631362e34807809260037307e3e8e95780f3fa5`.

Квалификация применения в проекте: **not-assessed**. Чтение карточки не подтверждает внедрение.

## Задача

Обновление алгоритма завершает MCP-процесс и теряет действующие клиентские каналы.

## Когда применять

- MCP-проект должен обновлять ядро при сохранении совместимого tool surface.
- Есть стабильная process/HTTP граница.

## Когда не применять

- Меняются schemas/semantics/capabilities или сам facade SDK.
- Нельзя сохранить ownership текущих операций.

## Граница класса или папки

Папка MCP adapter независима от core; composition root связывает их через port.

## Ответственность

- Сохранить канал и JSON-RPC mapping.
- Передать нормализованный вызов ядру и вернуть договорённый результат.

## Публичный контракт

Facade сообщает поддерживаемый protocol profile; вход/выход/ошибки совместимы с существующим клиентом.

## Зависимости

- Core не импортирует MCP SDK.
- Facade не содержит алгоритм и не читает глобальный graph internals.

## Состояние и время жизни

Transport/session там, где применимо, живут в facade; job/cursor имеют отдельного владельца.

## Ошибки

- Смена tool names/schema/semantics требует отдельного compatibility решения.

## Пример

```python
class ToolFacade:
    """Делегирует use case; transport lifetime находится снаружи."""
    def __init__(self, core):
        self.core = core

    def describe(self, request):
        return self.core.describe(request)
```

Минимальный пример показывает направление зависимости, но не реализует seamless update или negotiation.

## Контрпример

```python
def update_core(stop_mcp_process, replace_binary):
    stop_mcp_process()
    replace_binary()
```

Остановка владельца stdio уничтожает действующий канал.

## Как внедрять

- Снимите точный публичный контракт и runtime profile.
- Отделите use case от транспорта до обновления.

## Ограничения

- Рецепт draft; live qualification Atlas ещё не подтверждена этой карточкой.
- MCP2026-07-28 HTTP sessions отличаются от legacy initialize/session.

## Что проверять

- Подключить клиента до обновления и проверить вызовы до/после.
- Проверить schema, errors, scope и отмену.

## Альтернативы

- Явный reconnect при обновлении несовместимого facade.

## Как писать код

- Описание метода объясняет вход, результат и существенные ошибки; комментарий ветви объясняет причину.
- Используйте ясные локальные этапы и ранние выходы; новый класс или интерфейс нужен только для самостоятельной ответственности.

## Применимость и версии

- python
- Примеры: синтаксис Python 3.12/3.13; типизированные интерфейсы не заменяют runtime-проверки.

Инженерная рекомендация для проверки на конкретном проекте; не автоматическое правило рефакторинга.

Примеры проверяются синтаксически при сборке каталога. Они не исполняются и не доказывают поведение реального backend или MCP-клиента.

## Источники

Ссылки проверены: 2026-10-30. Рецепт Atlas отделён от гарантий источников.

[MCP 2025-11-25 transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) — stdio принадлежит запущенному клиентом процессу; выделенный facade — предлагаемая архитектура, не гарантия протокола.

[MCP 2026-07-28 Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http) — В этой версии удалены protocol-level sessions; профиль нельзя смешивать с legacy handshake/session правилами.

## Продолжить

[generation-handoff](Pattern-generation-handoff.md) · [versioned-contracts](Pattern-versioned-contracts.md) · [Каталог паттернов](Code-Patterns.md)
