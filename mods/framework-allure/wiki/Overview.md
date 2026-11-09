# Allure: метаданные шагов

Пакет `framework-allure`, версия `1.0.0`.

## Для чего

Дополняет снимок лексическими фактами библиотеки: точные роли и стратегии перечислены ниже. Код приложения и тесты не исполняются.

## Что объявлено

- Framework `allure`, язык `java`; supportsComposed: false.
- `io.qameta.allure.Step` → `step`; displayArgument: `value`.
- `io.qameta.allure.Description` → `metadata`; argument: `value`.
- `io.qameta.allure.Epic` → `metadata`; argument: `value`.
- `io.qameta.allure.Feature` → `metadata`; argument: `value`.
- `io.qameta.allure.Story` → `metadata`; argument: `value`.
- `io.qameta.allure.Owner` → `metadata`; argument: `value`.
- `io.qameta.allure.Severity` → `metadata`; argument: `value`.
- `io.qameta.allure.Link` → `metadata`; argument: `value`.
- `io.qameta.allure.Links` → `metadata`; argument: `value`.
- `io.qameta.allure.Issue` → `metadata`; argument: `value`.
- `io.qameta.allure.Issues` → `metadata`; argument: `value`.
- `io.qameta.allure.TmsLink` → `metadata`; argument: `value`.
- `io.qameta.allure.TmsLinks` → `metadata`; argument: `value`.
- `io.qameta.allure.AllureId` → `metadata`; argument: `value`.
- `io.qameta.allure.Flaky` → `metadata`; argument: `value`.
- `io.qameta.allure.Muted` → `metadata`; argument: `value`.
- `io.qameta.allure.Attachment` → `metadata`; argument: `value`.
- `io.qameta.allure.LabelAnnotation` → `metadata`; argument: `value`.

## Пример проверки

```text
@Step("load items")
void loadItems() {}
```

Проверьте роль step и метаданные Allure. @Step сам по себе не объявляет тест: для тестового метода нужен поддерживаемый тестовый framework.

## Включение и обновление

Зависимости: `language-java`. У них должен быть собственный допустимый режим включения.

Для декларативного анализатора доступен выбор по подходящим файлам при обновлении карты; явное «Выключено» приоритетно. Если пакет требует отдельного импорта документа или настройки движка, это остаётся отдельным действием.

После изменения содержимого, включая wiki, перечитайте каталог: меняется общий fingerprint пакета. Исполняемую версию включают заново явно; изменение правил анализа требует нового снимка для новых фактов.

## Границы и диагностика

Allure lifecycle и attachment callbacks не запускаются; наличие аннотации не подтверждает результат шага.

Если ожидаемые факты отсутствуют, проверьте точную версию и режим пакета, входные файлы, доступность нужного host-профиля и поля coverage/truncated/UNKNOWN. Отсутствие связи не доказывает отсутствие зависимости.

## Источник

Описание составлено по `mod.json` пакета `framework-allure` и действующим host-контрактам Atlas. Wiki — авторское объяснение, а не подтверждение исполнения.

Сверено по исходникам: 2026-10-28.
