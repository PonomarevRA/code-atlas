# Shared design review, 2026-10-22

Atlas сохраняет свою зелёную палитру, двухсторонние activity rails и специализированную карту. `@ag-design/components` задаёт общую геометрию и реальные фабрики кнопок истории/поиска; `web/design-adapter.css` связывает семантические токены с прежней палитрой и формами. Import order: tokens → styles → adapter. Конкретные места повторного использования перечислены в `ag-design.integration.json`.

Добавлен skip-link к карте. При coarse pointer высота рабочей области учитывает увеличенную до 52 px toolbar, поэтому нижняя часть карты не обрезается. В standalone история и collapse остаются настоящими native toolbar controls; браузерный fallback и CSP сохранены.

Новый локальный npm root закрепляет два tarball; `npm run design:verify` проверяет 5 vendor assets по SHA-256. Данные Atlas остаются во внешней системной папке; `node_modules` не входит в Git. Пакеты не обращаются к ag-design во время работы приложения.

Focused suites shell/workspace/map и native source прошли; повторный review coarse-pointer исправления — PASS. ARM64 standalone собран и подписан; CUA подтвердил настоящий заголовок/меню и сворачивание левой панели. Это не полный регресс всех визуализаций карты, не проверка Windows/Linux и не сертификация доступности.

Общая база и правила находятся в соседнем проекте ag-design; исходники и готовые архивы пакетов входят в поставку Atlas. Новая версия библиотеки не обновляет приложение автоматически: требуется явное обновление lock, vendor manifest и сборки.
