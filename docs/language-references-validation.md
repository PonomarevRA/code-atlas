# Проверка языковых справочников — 2026-10-11

Самопроверка `code-quality-gate`: **PASS**. Проверены новые `reference_*` модули,
страница/меню, интеграция server/MCP, подготовка ресурсов, packaging и agent skill.
Парсинг, хранение, поиск, writer, HTTP/MCP и представление разделены по модулям.

## Выполненные проверки

- 68 тестов прошли: `test_language_references`, `test_agent_mcp`, `test_agent_setup`,
  `test_agent_mcp_scope`, `test_documentation_api`, `test_semantic_index`,
  `test_semantic_service`, `test_frontend_method_documentation`.
- Проверены версии/перегрузки, локали, generic aliases, Python signatures без ID,
  Go receiver methods, отказ импорта ZIP/XML, atomic rebuild, несовпадение моделей,
  отсутствие implicit model/network/build при чтении и UTF-8 MCP continuation.
- Python compileall, JavaScript module syntax, `git diff --check`, skill validator
  и ограничения нового A2A объекта прошли.
- Источник Atlas на `127.0.0.1:8766` перезапущен. `/api/health` — `ok`, offline.
  Каталог содержит 8 корпусов, 167104 документа, 7 опубликованных LanceDB индексов.
- Реальный API: .NET Select/ToArray, Python list.append, JS array.map,
  Go strings.TrimSpace, Java Arrays.copyOf, Docker FROM и Helm values вернули
  документы. Русский semantic запрос Python вернул английские исходные страницы.
- Реальный stdio MCP → loopback HTTP: каталог, `ToArray<int>()` и полный документ
  прочитаны; новые tools имеют readOnlyHint и English default.
- Browser: переход к технической странице, RU/EN интерфейс, обновление локализованной
  выдачи, generic lookup, чтение полного документа и явный EN fallback проверены.
  В узкой панели таблица прокручивается, даты/числа сохраняют целые значения.
- Список проектов и снимок `4902e285d213431b9ee73908690ee362` остались прежними
  после перезапусков и запросов справочника. Пересборка кодовой карты не запускалась.

## Границы

Большой .NET API имеет готовые exact/FTS индексы; его необязательный LanceDB индекс
строится явной кнопкой. .NET guides и остальные корпуса имеют готовые смысловые
индексы. Они индексируют краткие описания, а не все абзацы. Для Java/Go сохранены
стандартные source/doc comments; исходные корпуса EN, интерфейс RU/EN.

Установленное приложение на 8765 и новая нативная сборка этим проходом не проверены.
Есть одно предупреждение существующей зависимости Starlette/httpx; тесты прошли.
Снимок интерфейса: `outputs/language-references-2026-10-11.jpg`.
