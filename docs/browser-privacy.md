# Atlas Browser: защита данных

Atlas Browser 0.4 обрабатывает захват страницы через локальный AG Privacy до построения временного графа. Атлас проверяет хеши SDK перед загрузкой и выбирает библиотеку для архитектуры текущего процесса Python.

Публичная версия Atlas поставляется без этого SDK. Без проверенного SDK `GET /api/browser-privacy` возвращает `blocked`, а попытка захвата страницы завершается с HTTP 424. Локальные карты исходников и данных, их веб-интерфейс и MCP доступны независимо от захвата страниц.

## Режимы

При первой установке открываются настройки. Полная защита — default. Минимальный режим и выключение требуют явного предупреждения, отметки согласия, нажатия пользователем и отдельного системного подтверждения macOS (Touch ID/пароль через LocalAuthentication). Browser-generated trusted click сам по себе не разрешает ослабление. Подтверждение ограничено20s; отказ/timeout не отправляет control credential. Возврат к strict не требует биометрии. Выбор сохраняется для новых карт в обоих браузерах. Агент не получает управляющий API/ключ и не может передать mode/skipPrivacy в capture. Любая смена режима удаляет все предыдущие browser sessions; уже отправленные сообщения чата вернуть нельзя.

Полный режим заменяет произвольные строки целиком, включая JS, атрибуты и URL. Сохраняется только фиксированная техническая структура DOM/геометрии; входные DOM/script IDs заменяются локальными порядковыми ID. Глубокий анализ JS поэтому ограничен. Нет ложного обещания полноты NER.

Минимальный режим скрывает распознанные credentials, остальные ПДн могут оставаться оригинальными. Исходники свыше96KiB UTF8 пропускаются целиком со статусом oversized: разрезание секрета между chunks запрещено. Off выдаёт оригиналы по человеческому выбору.

`privacy` во всех content-bearing HTTP/MCP responses содержит mode, masked и mustDiscloseMasking с общей инструкцией. Агент обязан явно сообщить о маскировке, без исходных значений и типов. HMAC placeholders непрозрачны, mapping не сохраняется. Source hashes/Unicode offsets относятся к выданному post-privacy коду, не доказывают совпадение с оригинальным JS.

## Доверенная установка

Приватное состояние находится в browser-privacy каталоге данных Atlas: installation pin, signing key и32-байтный native control credential. Файлы600, каталог700; SDK не содержит ключей. P-256 signatures связывают mode с точными policy/config bytes, current config digest закреплён отдельно; контекст immutable. Политика SDK загружается из проверенных bytes, не перечитывается из изменяемого файла при следующем human action.

Обычные HTTP/MCP capture/read инструменты не принимают режим. Native operation privacy-configure доступна только extension options.html; content scripts/popup не могут её вызвать. Native bridge добавляет приватный control credential, который не возвращается браузерному JS. Обработчик POST /api/browser-privacy проверяет credential и буквальное bool-подтверждение. Cookies/redirects/proxy отключены. Native HTTP20s, background transport bounded; privacy processing has6s whole-capture budget и атомарный отказ без частичного результата.

Safari sandbox получает собственную копию credential после включения расширения:

```sh
.venv/bin/python tools/install_browser_privacy_control.py
```

Команда не печатает secret, не меняет режим и не включает unsigned extensions. Перед ней запустите обновлённый Atlas и откройте настройки расширения для создания strict profile. Opera source native host читает credential из обычного private data directory Atlas. Путь source host должен указывать на ту же проверенную версию кода.

Это граница выдачи продукта, не изоляция от привилегированного процесса/агента с доступом ко всему host filesystem. Подпись не спасает от замены доверенного executable или control keys. Установка, ключи и raw-capture процесс должны оставаться вне инструментария агента.

## Codex и другие агенты

Atlas дополняет браузерные возможности Codex/ChatGPT временной картой и независимыми MCP/HTTP reads. Он не заменяет browser runner. Встроенные ответы Codex/ChatGPT browser tools не проходят через этот модуль: поддерживаемый pre-model interception hook здесь не реализован/не квалифицирован. Для protected задачи пользователь создаёт Atlas capture до выдачи текста модели; нельзя сначала прочитать raw tab и затем обещать ретроактивную маскировку.

Сборщик Safari получает DOM, стили и встроенный JavaScript. Для внешних и динамических скриптов используйте сборщик Chromium и подходящий режим защиты.


Системная авторизация основана на [Apple LAContext](https://developer.apple.com/documentation/localauthentication/lacontext): callback не вызывает повторный canEvaluatePolicy, контекст инвалидируется при завершении/20s deadline. Тесты подменяют только результат human authorization; настоящее Touch ID/password interaction и поведение пользовательского Safari проверяются вручную. CUA для этого не запускается.


## Native SDK внутри macOS bundle

Неизменённые NativeAOT библиотеки поставляются как bounded data archive, а не как неподписанный вложенный Mach-O code. Перед загрузкой выбирается только текущая архитектура, проверяются распакованный размер и исходный SHA256 из закреплённого manifest. Одна приватная600-копия в700 temporary directory переиспользуется процессом и удаляется при завершении. В этом кеше только бинарная библиотека, без capture/ключей. Runtime никогда не использует непроверенный pyc: адаптер компилируется из уже проверенных source bytes. Подписанный universal helper для системного подтверждения остаётся отдельным кодовым компонентом. Developer ID/notarization/hardened-library-validation distribution не квалифицированы этой локальной ad-hoc поставкой.
