# Нативная панель команд в заголовке standalone-приложений

Исследование: 2026-10-21. Область: Code Atlas, Ag Observer, ag-tasks (Python/pywebview 6.2.1) и AgMemory (Swift/AppKit/WKWebView). Только чтение исходников и официальной документации; рабочий код и установленные приложения не менялись. Это план технической реализации, а не доказательство уже работающего интерфейса.

## Рекомендация

На macOS реализовать **системный NSWindow + NSToolbar с unifiedCompact**, оставив настоящие close/minimize/zoom. Это позволяет разместить назад/вперёд, sidebar, переключение разделов и действия рядом с системными кнопками. Не включать `frameless=True`: установленный Cocoa backend pywebview при этом явно скрывает все три стандартные кнопки. Не рисовать их HTML/CSS-копии.

Именно toolbar внутри рамки окна соответствует запросу. Верхнее меню macOS (`NSMenu`), toolbar окна и вкладки содержимого имеют разные роли. Существующие меню сохранить как альтернативный доступ к командам и сочетаниям клавиш. Apple рекомендует отдавать toolbar частым командам/навигации, а не переносить туда всё меню; при недостатке ширины использовать системный overflow. [Apple HIG: Toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars?changes=_2)

Не пытаться получить одинаковые пиксели на всех ОС. Общими сделать идентификаторы команд, состояние и правила доступности; представление — отдельный адаптер Cocoa, Windows и Linux. Web-интерфейс сохраняет свой header как fallback для обычного браузера и неподдерживаемого native backend.

## Проверенный текущий стек

Версия пакета в Atlas `.venv`: **pywebview 6.2.1**. Прочитаны его установленные `webview/window.py`, `event.py`, `guilib.py`, `platforms/{cocoa,winforms,edgechromium,gtk,qt}.py`, а также актуальные desktop entrypoints всех четырёх проектов.

| Приложение / ОС | Фактическая точка интеграции | Следствие |
|---|---|---|
| Atlas macOS | `atlas/desktop_window.py:61`, `gui='cocoa'`; `window.native` — NSWindow | Добавляется маленький Cocoa adapter через PyObjC, без замены веб-движка |
| Observer / Tasks macOS | `ag_observer/desktop.py`, `ag_tasks/desktop.py`; обычный `create_window`, автоматический Cocoa backend | Тот же адаптер с собственным набором команд; не импортировать один сервис из другого |
| AgMemory macOS | `scripts/macos/AgMemoryDesktop.swift:30`, `DesktopWindow:NSWindow`, WKWebView и Auto Layout | NSToolbarDelegate/target-action непосредственно в Swift, меньше Python bridge-особенностей |
| pywebview Windows | `BrowserView.BrowserForm(WinForms.Form)`, WebView2.WinForms | Это **WinForms**, не WPF и не WinUI Window |
| pywebview Linux | Qt `BrowserView(QMainWindow)` либо GTK `Gtk.ApplicationWindow` | Разные возможности оконных декораций и жизненного цикла |

Atlas принудительно выбирает `edgechromium` на Windows и `qt` на Linux; Observer/Tasks сейчас оставляют выбор pywebview. Официальная таблица подтверждает WKWebView, WebView2/Edge и альтернативы Qt/GTK, но фактический установленный исходник точнее описывает конкретный host. [pywebview: engines](https://pywebview.flowrl.com/guide/web_engine.html)

Code Atlas исследование: проекты сверены по `/api/projects` с точными рабочими путями. Atlas snapshot `1c5c6ada3fa64ea49d8d7be2e000d0d3`, Observer `8f5a92217d2745d49a10321e24f7fd6b`, Tasks `a944d04b84f848bfb777fd58463c912b`, AgMemory `ad60cd50c16749c49b79981d2a807393`. Выполнены search/describe. Atlas describe сообщает расхождение snapshot/current source; Observer file-node не является точной декларацией. Swift desktop в карте не найден. Поэтому перечисленные lifecycle-детали подтверждены точечным чтением текущих файлов, а не выдаются за полную статическую карту.

## macOS: API и ограничения

### NSToolbar — первый этап

Сохранить `.titled/.closable/.miniaturizable/.resizable`. Назначить toolbar и `.unifiedCompact`, если API доступен на целевой macOS; `.unified` или стандартный toolbar — допустимый fallback. `unifiedCompact` документирован как расположение toolbar рядом с заголовком с уменьшенными отступами. Не задавать координаты системных кнопок и не обходить private titlebar hierarchy. [Apple: ToolbarStyle](https://developer.apple.com/documentation/appkit/nswindow/toolbarstyle-swift.enum?changes=_6)

Контролы: назад/вперёд, sidebar слева; ограниченный selector разделов в середине; контекстные действия/поиск справа. Использовать NSToolbarItem, штатные spacers и target/action. Программному toolbar нужен delegate с default item identifiers и фабрикой пользовательских items; delegate/targets необходимо явно удерживать на время жизни окна в Python. [Apple: toolbar delegate](https://developer.apple.com/documentation/appkit/nstoolbardelegate/toolbardefaultitemidentifiers%28_%3A%29?language=objc), [Apple: NSToolbarItem](https://developer.apple.com/documentation/AppKit/NSToolbarItem)

`titleVisibility=.hidden` допустим для устранения лишнего текста при собственном названии текущего раздела; это не основание удалять style mask или скрывать standardWindowButton. Имя приложения/документа всё равно сохранять в window.title для системной идентификации. Реальное поведение кнопок проверить отдельным GUI-тестом. [Apple: TitleVisibility](https://developer.apple.com/documentation/appkit/nswindow/titlevisibility-swift.enum?changes=_7)

### Прозрачный titlebar и расширенное содержимое — отдельная необходимость

`titlebarAppearsTransparent` отвечает за фон, а не за перенос команд. Apple связывает его полезное применение с `fullSizeContentView`. Этот mask растягивает contentView на полную высоту окна; контент следует ограничивать `contentLayoutRect/contentLayoutGuide`, чтобы он не оказался под titlebar/toolbar. Для первого этапа NSToolbar не требуется автоматически включать этот mask. [Apple: transparent titlebar](https://developer.apple.com/documentation/appkit/nswindow/titlebarappearstransparent?changes=_2_3&language=objc), [Apple: fullSizeContentView](https://developer.apple.com/documentation/appkit/nswindow/stylemask-swift.struct/fullsizecontentview)

Если позднее нужен фон web-содержимого до самого верха: отдельный контейнер, WebView с top-anchor к contentLayoutGuide, измеряемая геометрия при resize/fullscreen. Не фиксировать высоту шапки вроде 28/40px. В AgMemory текущий WKWebView прикреплён к четырём краям container — при включении mask верхнее ограничение нужно осознанно изменить. В Python нельзя просто обернуть WebView до первой загрузки: текущий Cocoa backend назначает его contentView в `webView_didFinishNavigation_`.

### Accessory и вкладки

NSTitlebarAccessoryViewController — штатный путь для специального дополнительного view в titlebar–toolbar области; он учитывает fullscreen/visual effects. По умолчанию `.bottom` добавляет область ниже titlebar; допустимы `.left/.right` с особыми правилами размеров. Поэтому accessory не обещает автоматически одну строку, как на скриншоте. Применять для реально нужного custom control/второго ряда после прототипа, не как обязательную замену NSToolbar. [Apple: accessory controller](https://developer.apple.com/documentation/appkit/nstitlebaraccessoryviewcontroller?changes=_8), [Apple: layoutAttribute](https://developer.apple.com/documentation/AppKit/NSTitlebarAccessoryViewController/layoutAttribute)

Разделы «Карта/Логи/Метрики/Задачи» — состояние приложения. Их selector не должен превращаться в системные document-window tabs. NSWindow tabbing группирует отдельные окна; AgMemory сейчас явно задаёт `.disallowed`. Сохранить это, пока отдельные документы/окна не являются требованием. [Apple: window tabbing](https://developer.apple.com/documentation/appkit/nswindow/tabbingmode-swift.property)

### Жизненный цикл pywebview и синхронизация

Документированный `window.native` доступен в `before_show`. В 6.2.1 Cocoa `initialized` вызывается раньше создания NSWindow и не подходит. `before_show` синхронный; `loaded/shown` обработчики обычно выполняются в отдельных потоках. Устанавливать базовый toolbar на Cocoa main thread в `before_show`; любые последующие native updates планировать через `AppHelper.callAfter`. Не вызывать из этого блокирующего обработчика методы, ожидающие загрузку страницы. [pywebview API: native/events](https://pywebview.flowrl.com/api/)

**Проверенное расхождение документации:** changelog/API обещает `window.native.webview`, но установленный `cocoa.py` не присваивает NSWindow такое поле. WebView становится `native.contentView()` после navigation finish; до этого contentView ещё не является им. В фазе реализации проверять реальный класс/capability, не опираться вслепую на `native.webview` и не закреплять private BrowserView.instances как публичный контракт. Установка одного NSToolbar не требует доступа к WKWebView.

Общий bounded command/state protocol: фиксированный список commandId, enabled/selected, currentView/canGoBack/canGoForward. DOM остаётся владельцем SPA-состояния; native только отображает его и передаёт разрешённые действия. Не читать DOM 30–60 раз/сек, обновлять по событиям навигации/смены раздела. При reload bridge и toolbar идемпотентны. Адаптер не принимает произвольные JS/selector/URL/путь от страницы. В AgMemory сейчас намеренно нет page→native message bridge; если нужна такая синхронизация, это явное ограниченное расширение с проверкой main-frame и точного loopback origin. Native UI не отменяет HTTP-проверки человеческого merge/CSRF.

История SPA не обязательно равна WKWebView backForwardList: определить app-router semantics до подключения стрелок. Native WK history подходит настоящим переходам; sidebar и section selection требуют команд приложения.

### Перетаскивание и доступность

Оставить свободное место системного titlebar для drag; кнопки и selector должны получать клики, не начинать drag. Не включать перетаскивание по всему WKWebView через `isMovableByWindowBackground` — конфликтует с выделением текста/канвой. API означает перемещение за фон окна и не заменяет проверку hit-testing интерактивных элементов. [Apple: window background dragging](https://developer.apple.com/documentation/appkit/nswindow/ismovablebywindowbackground)

Иконки получают доступные названия, tooltips, selected/enabled state; стандартные NSControl дают базовую accessibility, custom views требуют явной семантики. Проверить VoiceOver, Full Keyboard Access, фокус между toolbar/WebView, Cmd-shortcuts и disabled back/forward. Системный title/window name сохранить даже при визуально скрытом заголовке. [Apple: NSAccessibilityProtocol](https://developer.apple.com/documentation/appkit/nsaccessibilityprotocol)

## Windows и Linux

**Windows:** WinUI TitleBar control нельзя вставить в текущий WinForms host без новой интеграции. Однако AppWindowTitleBar — HWND-based API Windows App SDK; Microsoft допускает его и для WinForms/WPF. Возможный второй этап — Windows-specific helper с Windows App SDK interop и capability check, сохранив системные caption buttons. Нужно резервировать LeftInset/RightInset, правильно переводить DPI physical/logical, задавать passthrough regions интерактивных элементов и пересчитывать их при resize/DPI. Если SDK/OS не поддерживается — обычная рамка+toolbar под ней. Это отдельная зависимость/пакетирование, а не одна настройка pywebview. [Microsoft: titlebar customization](https://learn.microsoft.com/en-us/windows/apps/develop/title-bar?tabs=winui3)

Альтернатива Win32/DWM требует собственного non-client hit-testing и больше платформенного кода; не рекомендуется как первый этап этих приложений. Не предлагать WPF WindowChrome для WinForms host. [Microsoft: custom DWM frame](https://learn.microsoft.com/en-us/windows/win32/dwm/customframe)

**GTK:** native GtkHeaderBar + `GtkWindow.set_titlebar` поддерживает команды и стандартные frame controls. Установить до показа; документация предупреждает, что замена после show зависит от window manager. Текущий GTK backend — Gtk.ApplicationWindow, поэтому нужен ранний GTK-specific hook. Проверять X11/Wayland/окружение; не обещать идентичность GNOME/KDE. [GTK3: HeaderBar](https://docs.gtk.org/gtk3/class.HeaderBar.html), [GTK3: set_titlebar](https://docs.gtk.org/gtk3/method.Window.set_titlebar.html)

**Qt:** текущий QMainWindow позволяет штатный QToolBar, но это область layout окна, не универсальный native-titlebar embed на Linux. Свойство `unifiedTitleAndToolBarOnMac` относится именно к Mac. Без отдельного доказанного compositor/platform пути оставить системную декорацию+toolbar ниже, не включать FramelessWindowHint ради сходства. [Qt: QMainWindow](https://doc.qt.io/qt-6/qmainwindow.html)

## Поэтапный план и критерии приёмки

1. Зафиксировать 5–8 реально нужных команд каждого продукта, их app-router semantics, доступность и приоритет overflow. Для Tasks не добавлять прямой merge одним кликом: действие открывает существующий review flow. Для Observer оставить Grafana отдельным разрешённым view, без изменения ownership сервиса.
2. Изолированный macOS-прототип на одном Python-приложении: framed NSWindow + NSToolbar unifiedCompact, назад/вперёд/sidebar/одна primary action; без изменения fullSizeContentView и без переписывания контента. Проверить source и реальный frozen `.app`.
3. Только после проверки native state handshake скрыть соответствующие дублирующиеся web-controls при capability `nativeToolbarReady`. Браузер/ошибка адаптера сохраняют web header. Распространить Python adapter как самостоятельный небольшой модуль/одинаковый контракт, без межсервисных импортов. Порт в Swift AgMemory — собственный NSToolbarDelegate и сохранение имеющегося WKWebView/lifecycle.
4. Если обычный NSToolbar не даёт желаемой композиции, отдельно исследовать accessory или fullSizeContentView+contentLayoutGuide. Не добавлять оба механизма заранее.
5. Windows helper и GTK headerbar — самостоятельные этапы с собственными runtime fixtures. Qt fallback сохраняется. Пиксельное сходство не является основанием отказаться от системного поведения.

Native acceptance: настоящие traffic lights/caption controls, hover/fullscreen menu, drag пустой области, клики всех команд без drag, двойной клик titlebar по системной настройке, minimize/zoom/fullscreen/restore, graceful close/Cmd-Q и очистка owned subprocesses. Light/dark/inactive windows, Retina/обычный DPI, minsize (Observer/Tasks сейчас390×560; Atlas960×640; AgMemory760×520), длинная локализация, overflow, окно у края экрана. Нет перекрытия первого ряда web-контента; вкладки/selection переживают reload/back; один набор listeners/toolbar после повторной навигации.

Проверить accessibility tree/VoiceOver/keyboard focus и browser fallback. Выполнить реальные Windows10/11 DPI/Snap/system-menu тесты и Linux GTK/Qt X11/Wayland до заявлений о поддержке этих платформ. Код-компиляция, unit tests и официальный API сами по себе не доказывают результат оконного менеджера.

**Граница исследования:** в этой задаче не создавался новый GUI-прототип, не запускались новые native API и не проводилась проверка Windows/Linux. Техническая реализуемость macOS обоснована официальными AppKit API и существующим доступом к NSWindow, но конкретная композиция на скриншоте требует runtime-прототипа. Никаких production edits, rebuild или Outcome от исследователя.

## Уточнение по скриншотам: положение кнопки и разделителя

Позднее в той же задаче пользователь уточнил композицию: кнопка находится над соответствующим меню; вертикальный разделитель в шапке продолжает границу меню и содержимого. В свёрнутом виде остаётся полоса иконок. Последнее уточнение пользователя: во всех разделах, включая Grafana, меню остаётся слева. Сворачивание меняет только ширину панели. Синий значок и название приложения находятся в самой нативной шапке, на одной горизонтали с системными кнопками и переключателем меню, справа от разделителя. Отдельная HTML-строка под шапкой не соответствует запросу. Дублирующая web-строка скрывается только после успешного native-identity handshake; в обычном браузере остаётся fallback.

Изолированный AppKit-прототип подтвердил публичный `NSTitlebarAccessoryViewController` с настоящей `NSButton` и `NSBox`-разделителем: при левой границе 200/300 pt разделитель находится на 199/299 pt; при правой панели 48 pt в окне 1160 pt — на 1112 pt. Это измерение собственного тестового окна, а не пиксельная проверка пользовательского приложения. Штатные кнопки окна сохраняются. При левой полосе 48–56 pt она уже группы системных кнопок: в шапке скрывается только невозможный разделитель, без переноса/перерисовки traffic lights.

Состояние веб-панели передаётся без привилегированных действий: optional zero-argument уведомление `standalone_sidebar_changed`, затем native сам читает фиксированные поля положения/ширины из текущей собственной loopback-страницы, проверяет типы, origin и границы. Уведомления coalesce/debounce; нет опроса 30–60 раз в секунду, произвольных JS, URL, путей или команд из страницы. Web fallback скрывается после успешной установки нативного управления. При закрытии pending callbacks прекращаются; нативный Quit ждёт завершения принадлежащих приложению серверов.

При строгом CSP нельзя использовать `window.expose` pywebview 6.2.1 для этого уведомления: генерируемый `new Function` блокируется `script-src self`. Cocoa adapter использует публичный `WKScriptMessageHandler` с одним фиксированным каналом, принимает только `null` от main frame точного собственного loopback origin и вызывает только coalesced notify. CSP остаётся строгим; обычная JS-функция отправки не использует eval.
