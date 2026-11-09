# Завершение standalone без блокировки выключения

Контракт относится к Atlas, AgMemory, Observer и Tasks. Храните данные отдельно от
заменяемого приложения. Сохраняйте принятые операции в ходе работы: завершение
процесса — ограниченный этап освобождения ресурсов, а не единственная точка сохранения.

## Общая последовательность

1. Прекратить приём новых запросов и фоновых страниц. Сохранить принятую транзакцию,
   курсор, настройки и законченные публикации; повторные команды выхода не начинают
   новый drain и не продлевают срок.
2. Выполнить cleanup вне UI-потока: завершить текущий короткий запрос/пакет,
   закрыть журнал и БД, штатно остановить только собственные дочерние процессы.
3. Не спрашивать «подождать/отменить» при системном выходе. Подтверждение завершения
   не зависит от доступности UI или внешних сервисов.
4. Python desktop: общий watchdog 8 с, ожидание ASGI до 6 с и HTTP drain до 2 с.
   HTTP timeout не пропускает lifespan. При истечении общего срока завершаются
   только живые потомки владельца; `os._exit(1)` исключает бесконечные Python finalizers.
   Watchdog остаётся вооружён после cleanup, пока сам процесс не вышел.
5. Swift AgMemory: .NET host получает 6 с, native owner — 7 с. По истечении срока
   владелец завершает только свой `Process` и разрешает Quit без диалога.

ОС может прекратить процесс раньше этих сроков. При SIGKILL, отключении питания,
неработающем диске или зависшем runtime нельзя обещать сохранение незавершённой
операции. Уже подтверждённые записи защищены существующими транзакциями/атомарными
публикациями; диагностическая очередь не равна подтверждённой записи. Не делайте
полный backup, VACUUM или полную переиндексацию условием выхода. Не выбирайте
процессы по имени или PID из файла.

## macOS

Используется `NSApplicationDelegate.applicationShouldTerminate`: быстрый возврат
`NSTerminateLater`, фоновый drain, затем `replyToApplicationShouldTerminate(true)`.
`NSTerminateCancel` для системного выхода не используется. Старый delegate renderer
сохраняется для прочих selector-вызовов. Красная кнопка направляется в этот же Quit.
Completion доставляется в common **и modalPanel** run-loop modes: один main dispatch
queue или default-mode callback может не обслуживаться во время deferred Quit.

Python TERM/INT будят native loop через nonblocking pipe и CoreFoundation descriptor,
без дополнительного периодического polling. Swift использует DispatchSourceSignal;
deadline работает на global queue и не зависит от AppKit modal loop.
Не включайте NSSupportsSuddenTermination, пока не доказано, что все критические
изменения уже записаны и дочерние процессы отдельно управляются ОС.

## Windows

Для текущего Python WinForms/WebView2 renderer отдельно обрабатывается
`FormClosing` с `CloseReason.WindowsShutDown`: не вызывается пользовательский veto,
`Cancel=false`, drain запускается без ожидания UI. Обычное закрытие формы сохраняет
асинхронное ожидание cleanup. `ShutdownBlockReasonCreate` не используется.
При переходе на другой renderer нужно сохранить Win32-семантику: разрешить
`WM_QUERYENDSESSION`, выполнить короткий cleanup при `WM_ENDSESSION`.
.NET-хост standalone использует тот же шестисекундный HostOptions budget на любой ОС.

## Linux

Python desktop явно использует Qt. `commitDataRequest` и `aboutToQuit` запускают
тот же ограниченный drain. Не вызываются `QSessionManager.cancel`, взаимодействие
с пользователем или `requestPhase2`. POSIX TERM/INT обслуживаются wakeup pipe и
`QSocketNotifier`. Там, где desktop compositor не предоставляет Qt session protocol
(в том числе некоторые Wayland-сеансы), остаётся путь завершения через SIGTERM.
При внешнем systemd-unit его TimeoutStopSec должен давать приложению указанный
бюджет; приложение само не меняет настройки session manager/systemd.

## Данные и восстановление

Observer прекращает новые страницы, но позволяет закончить уже начатую транзакцию.
Курсор сохраняется вместе с событиями, затем закрывается SQLite WAL. Grafana и
Prometheus сначала получают terminate вместе, делят 3 с grace и 1 с reaping;
их история не удаляется при остановке. Tasks сохраняет issue и audit атомарно с
SQLite synchronous=FULL. Atlas сохраняет последний опубликованный снимок;
прерванная сборка при старте отмечается failed, её можно повторить. AgMemory
сохраняет записи своим хранилищем и ограниченно дренирует очереди журналов.

## Проверка без перезагрузки

Автотесты запускают только временные процессы/хранилища: быстрый выход, повторный
Quit, ошибка drain, истёкший срок, сохранённая SQLite-запись, собственный зависший
дочерний процесс и независимый процесс. На macOS также проверяются реальные
modal run-loop callbacks и wakeup pipe для TERM. Swift deadline тестируется
отдельным Foundation executable; .NET tests проверяют standalone/server policy.

Выход из сеанса, перезагрузка и выключение компьютера **не запускались**.
Реальные Windows/Linux session notifications требуют отдельной проверки на каждой
ОС и renderer. Моки adapters и cross-platform Python tests не доказывают этот
сценарий. У AgMemory собственный native launcher пока macOS; будущие Windows/Linux
оболочки должны следовать приведённому контракту, а не копировать AppKit API.

## Первичные источники

- [Apple: applicationShouldTerminate](https://developer.apple.com/documentation/appkit/nsapplicationdelegate/applicationshouldterminate(_:))
- [Apple: NSTerminateLater и modal run loop](https://developer.apple.com/documentation/appkit/nsapplication/terminatereply/terminatelater)
- [Apple: progressive saving и sudden termination](https://developer.apple.com/library/archive/documentation/General/Conceptual/MOSXAppProgrammingGuide/CoreAppDesign/CoreAppDesign.html)
- [Microsoft: shutdown notifications и частое сохранение](https://learn.microsoft.com/en-us/windows/win32/shutdown/shutting-down)
- [Qt: session management](https://doc.qt.io/qt-6/qsessionmanager.html)
- [.NET Generic Host shutdown](https://learn.microsoft.com/en-us/dotnet/core/extensions/generic-host)
