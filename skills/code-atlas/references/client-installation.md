# Подключение клиентов

Пакеты `code-atlas` и `code-atlas-engines` используют общий формат Agent Skills. Копируй целиком выбранную папку с references и agents. Применяй настройки в разрешённой пользователем области, сохраняя существующие серверы и местные изменения.

| Клиент | Навыки одного проекта | Подключение Atlas |
| --- | --- | --- |
| Codex | .agents/skills | Выданный TOML объединить с MCP-настройками Codex |
| Cursor | .cursor/skills, также поддерживается .agents/skills | Выданную запись mcpServers объединить с .cursor/mcp.json выбранной области |
| Claude Code | .claude/skills | Использовать MCP-настройки Claude Code и схему клиента |
| DeepSeek Harness | .dsh/skills, также поддерживается .agents/skills | Использовать MCP-механизм действующей версии Harness |
| Другой агент | Выбранный клиентом каталог либо ручная загрузка Markdown | MCP через локальный клиент или вызовы HTTP через локальный tool host |

Claude Desktop использует собственную конфигурацию и загрузку навыков. Путь .claude/skills относится к Claude Code. Для DeepSeek через Codex, Cursor или другой клиент используй формат этого клиента. Модель вызывает инструменты через предоставленное приложение.

Если есть checkout Atlas, установщик `scripts/install_agent_skills.py` показывает предлагаемые пути. `--apply` выполняет копирование. Его `--destination` позволяет указать каталог другого клиента. При установке ZIP распакуй полные папки в skill root клиента.

Получай реальную команду, порт и scope у запущенного Atlas через `/api/agent-setup/config`, для Codex добавь `?format=toml`. Это локальный stdio-прокси к HTTP Atlas. Для client attribution в записи окружения допустимы `ATLAS_MCP_CLIENT_KIND=codex`, `cursor` и `claude`. Остальные клиенты используют `unknown` до появления отдельной поддержки. Для разных клиентов готовь отдельные записи.

После подключения проверь реальный MCP-вызов `list_projects`, затем выбранный символ с правильным snapshotId. Если клиент ещё не загрузил настройки, переподключи Atlas его поддерживаемым способом или начни новый чат. Облачному агенту нужен tool host с доступом к машине Atlas.

Официальные описания форматов использованы при подготовке этой инструкции.

- [Codex skills](https://developers.openai.com/plugins/concepts/skills)
- [Cursor skills](https://cursor.com/docs/skills)
- [Claude Code skills](https://code.claude.com/docs/en/skills)
- [DeepSeek Harness filesystem skills](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/skill/skill-filesystem/README.md)
- [DeepSeek tool calls](https://api-docs.deepseek.com/guides/tool_calls/)
