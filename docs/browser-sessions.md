# Временный анализ страницы

Браузерные карты существуют только в памяти работающего Atlas. Они не являются
проектами, не попадают в сохранённые снимки, semantic/vector indexes, activity
journal или резервные копии. Перезапуск Atlas удаляет все браузерные карты.
Лимит — восемь сессий и 64 MiB сериализованных удерживаемых данных. Это лимит
данных, а не измерение RSS Python или браузера.

В режиме `task` вызывайте `finish_browser_session` в `finally` после завершения,
отмены или ошибки задачи. Atlas не может определить конец разговора самостоятельно.
Режим `hour` позволяет повторные запросы до истечения часа. Оба режима имеют
жёсткий предел `ttlSeconds` от 1 до 3600; чтения проверяют срок по монотонным часам,
а фоновый сборщик освобождает просроченную память. Явный `finish` удаляет любую
сессию сразу и допускает повторный вызов. Содержимое, ранее переданное в чат,
подчиняется хранению самого чата.

## Защита перед построением карты

Захват страницы требует отдельно поставляемого расширения и локального SDK AG Privacy. При статусе `blocked` Atlas отклоняет захват. Инструкции для сборщиков ниже относятся к поставке с этими компонентами. Проверяйте `privacy` и явно сообщайте о маскировке. Исходники и `sourceHash` относятся к результату после обработки SDK. Встроенное чтение вкладок Codex/ChatGPT проходит через собственные средства этих клиентов. [Защита данных Atlas Browser](browser-privacy.md)

## Контракт

`POST /api/browser-sessions` принимает `capture`, `retention` и `ttlSeconds`.
Capture версии `atlas-browser-capture-v1` содержит `page` с HTTP(S) URL, заголовком
и временем наблюдения; `nodes` с уникальными `id`, `parentId`, тегами, атрибутами,
текстом, геометрией и вычисленными стилями; `scripts` с ID, URL и явным статусом
`captured`, `unavailable` или `oversized`. Query/fragment URL не сохраняются;
значения форм и inline event attributes исключены. Входные строки остаются
недоверенными данными, а не инструкциями агенту.

Лимиты: 4000 DOM-элементов, глубина 128, 64 скрипта, 1 MiB UTF-8 на скрипт и
4 MiB суммарного JS. Нормализованный capture ограничен 8 MiB; HTTP body — 16 MiB
для JSON escaping. JS разбирается существующей грамматикой Tree-sitter без
выполнения, загрузок и запуска кода страницы. Общий бюджет — 20000 посещённых
AST-узлов и 1000 объявлений. Ошибки разбора, недоступные скрипты и исчерпание
лимитов сохраняют явные статусы/предупреждения. Неиспользуемые скрипты автоматически
не подгружаются.

DOM `contains` — наблюдённая структура. `declares` — синтаксис JS.
`call_candidate` — лишь совпадение имени в том же скрипте: shadowing, импорт,
тип получателя и runtime dispatch не разрешены. `lexicalFacts` сохраняет
выражения `fetch` и DOM-селекторов; это не доказательство запроса или срабатывания
обработчика. Серверный код, workers/OOPIF, source maps и трассировка действий
не входят в первый вариант. Первая поставка предоставляет агентский граф через
HTTP/MCP; отдельного визуального renderer нет.

## Чтение через MCP и HTTP

Нужен общий MCP Atlas без привязки к проекту/data map. Привязанные подключения
отклоняют браузерные операции до HTTP-запроса.

1. Сборщик или `create_browser_session` возвращает случайный `sessionId`.
   `get_browser_capabilities` описывает лимиты/coverage/workflow; `get_browser_session`
   проверяет ровно выбранную карту. `list_browser_sessions` — общий host discovery.
2. `query_browser_session` ищет DOM и лексические объявления, селекторы и request
   expressions. Фильтруйте `kind`, следуйте `nextOffset` с тем же запросом.
3. `get_browser_node` показывает компактные факты и страницу связанных рёбер.
   `omittedFields` явно перечисляет слишком большие DOM-поля. Их исходные значения
   доступны через `read_browser_node_property(field,propertyIndex,offset,limit)`;
   индексы свойств идут в порядке capture, Unicode-ключи сохраняются точно.
4. `read_browser_node_source(nodeId)` сразу читает выбранную функцию/скрипт.
   Смещение — Unicode codepoints **от начала выбранного узла**. `sourceHash`
   идентифицирует весь исходный скрипт. `startByte/endByte` — UTF-8 byte offsets,
   их нельзя передавать в codepoint API. Для всего файла есть `read_browser_script`.
5. `finish_browser_session` в `finally` освобождает карту после любого исхода.

Все браузерные MCP-инструменты имеют input/output schema, явные границы страниц
и `openWorldHint=false`; finish помечен идемпотентным. HTTP и MCP возвращают
`code`, `retryable`, `recoveryAction`; MCP переводит только allowlisted HTTP status,
не отражая произвольный текст удалённой ошибки. Неизвестные/истёкшие ID не различаются.
Одинаковый stdio-контракт предназначен Codex, Cursor, Claude и совместимым клиентам;
инструкции доступны в `skills/code-atlas/references/browser-sessions.md` и setup guide.
Работа каждого редактора отдельно не проверялась; его настройки не меняются.

HTTP-эквиваленты: `GET /api/browser-sessions` и
`GET /api/browser-sessions/{sessionId}` и
`POST /api/browser-sessions/{sessionId}/{query,node,source,node-source,property,finish}`.
Эти пути — шаблоны: замените sessionId фактическим ID, а последнюю часть — одной
операцией. Чтения ограничены 64 KiB; большие списки/связи выдаются страницами.
Истёкшие/неизвестные сессии возвращают 404. Старые repository maps не меняются.


## Сборщик Chrome/Chromium

Исходники расширения: `extensions/atlas-browser/`. Оно запускается только по
кнопке «Создать карту страницы» в меню значка или пункту его контекстного меню. Разрешения: activeTab, debugger,
nativeMessaging, contextMenus и storage (только RAM session metadata). История, cookies, загрузки, сторонние серверы и
постоянное browser storage не используются. Скрипты читаются через debugger API;
код страницы в процессе Atlas не исполняется. При ошибках и смене страницы
собственный debugger снимается. Большие/исчезнувшие скрипты имеют явный статус.

1. Запустите сервер **из этой версии исходников**, например
   `.venv/bin/python -m atlas.cli --port 8765`. Уже установленная старая версия
   приложения может не иметь новых API; эта задача не заменяет её нативную сборку.
2. Откройте `chrome://extensions`, включите режим разработчика и выберите
   «Загрузить распакованное расширение» → каталог `extensions/atlas-browser/`.
3. Скопируйте точный ID расширения. На macOS зарегистрируйте host явно:

   ```sh
   .venv/bin/python tools/install_browser_capture_host.py \
     --extension-id <32-буквенный-ID> \
     --destination "$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts"
   ```

   Для Chromium на macOS каталог —
   `~/Library/Application Support/Chromium/NativeMessagingHosts`.
   Для Chrome на Linux — `~/.config/google-chrome/NativeMessagingHosts`;
   для Chromium — `~/.config/chromium/NativeMessagingHosts`.
   Нужен существующий Python; installer ничего не скачивает. Другой порт задаётся
   `--port`. Installer исходной версии рассчитан на macOS/Linux; Windows installer
   не входит в этот вариант. Перед переездом/удалением checkout обновите host:
   launcher содержит его абсолютный путь.
4. На нужной HTTP(S) странице откройте меню Atlas. Выберите «До конца задачи» или
   «До одного часа» и нажмите «Создать карту страницы». Меню показывает ход,
   результат и понятный следующий шаг при ошибке. Подсказка работает по нажатию,
   Enter/Space и закрывается Escape. Существенные ограничения видны постоянно.
5. Нажмите «Скопировать для агента»: инструкция содержит точный ID этой вкладки,
   порядок чтения и обязательный `finish` в `finally`. Не выбирайте автоматически
   последнюю карту из общего списка. Перед новым захватом удалите текущую карту.
   Кнопка «Удалить карту» удаляет её немедленно; при сбое ID сохраняется для повтора.
   Закрытие меню/вкладки само по себе не обещает завершения задачи: действует TTL.

Меню использует офлайн-пакет AG Design 0.10.4, точный SHA-256 npm-артефакта и
манифест каждого поставляемого файла. Палитра Atlas сохранена; доступны обе темы
и повышенный контраст, 320px и 200% текста. Chrome context menu остаётся нативным.
В `storage.session` сохраняются только ID/срок/счётчики/состояние по вкладке, без
DOM, JS, URL и заголовка. Это память браузерной сессии, не постоянное хранилище.
После перезапуска Atlas меню проверяет только свой ID. Потерянные native ответы
могут оставить карту до TTL; новая карта не выбирается автоматически.

Снимок охватывает основной frame; shadow DOM представлен плоско. DOM, styles и
JS наблюдаются последовательно, не атомарно. CDP сначала получает browser-side
snapshot, который может занимать больше памяти, чем итоговые 4000 элементов;
лимиты сохранённого пакета не являются ограничением памяти самого браузера.
Source maps, iframe/worker contexts и runtime-запись действий не подгружаются.

Native host принимает одно UTF-8 JSON сообщение с native-endian 32-bit length,
проверяет origin установленного расширения и передаёт пакет только на заданный
loopback-порт, без proxy/redirect. Ответ содержит ID, срок и счётчики; DOM/JS не
пишутся в файлы или stderr. Первоначальная проверка использовала временный Chromium headless профиль, настоящий CDP, framed host и production HTTP/MCP adapters. В0.3.0 после явного согласия пользователя дополнительно проверены реальные Opera и Safari: захват синтетической страницы, чтение исходников и удаление через кнопку; точные ID после удаления дают404. Подробности — docs/engineering/browser-settings-safari-verification.md.

Ссылки на протоколы: [Chrome debugger](https://developer.chrome.com/docs/extensions/reference/api/debugger),
[native messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging),
[DOMSnapshot](https://chromedevtools.github.io/devtools-protocol/tot/DOMSnapshot/).

Сборщик разделяет общий deadline20секунд между CDP-командами; попытка detach
ограничена одной секундой. Поздний успешный attach также освобождается.
Неотвечающий браузер может потребовать ручного отключения debugger.
Тайм-аут CDP до передачи capture не создаёт карту в Atlas. Если после принятия
пакета потерялся ответ native/HTTP, сессия может сохраниться до TTL; её можно
найти через list_browser_sessions и удалить через finish_browser_session.


## Иконка и настройки0.3.0

Меню содержит ссылку «Настройки и как это работает». `options.html` также открывается
из управления расширениями. Сохраняются только `retention` (`task`/`hour`) и целый
`ttlMinutes` от1до60 в `storage.local`. Они применяются к новым картам, переводятся
в `ttlSeconds` перед отправкой и не продлевают живую карту. Значения браузерных
карт остаются в RAM, отдельно от preferences. Кнопка связи передаёт только
`operation=health`: bridge читает доступность сервиса и возвращает boolean/protocol,
без списка сессий, URL, DOM или JS.

Иконка: `icons/atlas.svg` — исходник; PNG16/32/48/128 — manifest assets; большие
PNG предназначены содержащему Safari приложению. `manifest.key` — публичный
ключ стабильного ID распакованной Chromium/Opera-сборки, не секрет. Приватный
ключ не поставляется и не нужен для распакованной установки.

## Safari на macOS

Это отдельный адаптер: `safari-collector.js` использует `scripting.executeScript`
в изолированном контексте верхнего документа. Он читает DOM, выбранные стили и
inline JavaScript. Исходники внешних/module/dynamic JS не подгружаются повторно;
внешние script elements имеют `unavailable`, отсутствие динамического inventory
отмечено предупреждением. Popup и options объясняют это до захвата. Epoch
`performance.timeOrigin` и URL проверяются после наблюдения для отказа при
навигации, включая перезагрузку на тот же адрес.

Сборка на Mac с существующим Xcode:

```sh
python tools/build_safari_browser.py --destination /absolute/new/build-directory
```

Скрипт не скачивает зависимости, не перезаписывает старый output и не меняет
настройки браузера. Он делает Safari manifest без Chromium debugger/key/type,
создаёт containing app через converter и собирает локально подписанный bundle.
Swift handler остаётся в sandbox с outgoing-network entitlement. Только capture,
health и status/finish точного ID отправляются на фиксированный127.0.0.1:8765;
redirect/proxy не используются, ответ собирается до64KB. Никаких DOM/JS в логах
или ответах. Source: `packaging/safari-browser/SafariWebExtensionHandler.swift`.

Safari требует включить расширение в Settings→Extensions. Для локальной ad-hoc
сборки потребуется Settings→Developer→Allow unsigned extensions; это разрешение
сбрасывается при перезапуске Safari и может требовать пароль macOS. Временная
загрузка folder без containing app не проверяет native messaging. Реальные
браузерная активация, права сайта и Safari→Swift→Atlas pipeline подтверждаются
отдельно от успешной сборки и standalone Swift/DOM fixture.

Источник: [Apple: Running your Safari web extension](https://developer.apple.com/documentation/safariservices/running-your-safari-web-extension),
[Apple: Native messaging](https://developer.apple.com/documentation/safariservices/messaging-between-the-app-and-javascript-in-a-safari-web-extension).


Для Opera на macOS регистрируйте host в
`~/Library/Application Support/Google/Chrome/NativeMessagingHosts/`, согласно
[документации Opera](https://help.opera.com/en/extensions/message-passing/).
Каталог `com.operasoftware.Opera/NativeMessagingHosts` в проверенной Opera137.0
не обнаруживался. Дополнительных разрешённых extension ID не требуется.
