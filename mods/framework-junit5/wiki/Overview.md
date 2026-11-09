# JUnit 5: тесты и источники

Пакет `framework-junit5`, версия `1.1.0`.

## Для чего

Дополняет снимок лексическими фактами библиотеки: точные роли и стратегии перечислены ниже. Код приложения и тесты не исполняются.

## Что объявлено

- Framework `junit5`, язык `java`; supportsComposed: true.
- `org.junit.jupiter.api.Test` → `test`.
- `org.junit.jupiter.api.RepeatedTest` → `test`.
- `org.junit.jupiter.api.TestFactory` → `test`.
- `org.junit.jupiter.api.TestTemplate` → `test`.
- `org.junit.jupiter.params.ParameterizedTest` → `test`; displayArgument: `name`.
- `org.junit.jupiter.params.provider.ValueSource` → `parameter-source`; datasetStrategy: `junit-single-values-v1`; sourceArgument: `value`.
- `org.junit.jupiter.params.provider.MethodSource` → `parameter-source`; datasetStrategy: `junit-literal-provider-v1`; sourceArgument: `value`.
- `org.junit.jupiter.params.provider.CsvSource` → `parameter-source`; datasetStrategy: `junit-positional-csv-v1`; sourceArgument: `value`.
- `org.junit.jupiter.params.provider.CsvFileSource` → `parameter-source`; datasetStrategy: `resource-reference-v1`; sourceArgument: `resources`.
- `org.junit.jupiter.params.provider.EnumSource` → `parameter-source`; datasetStrategy: `junit-enum-constants-v1`; sourceArgument: `value`.
- `org.junit.jupiter.params.provider.ArgumentsSource` → `parameter-source`; datasetStrategy: `unresolved-v1`; sourceArgument: `value`.
- `org.junit.jupiter.params.provider.ArgumentsSources` → `parameter-source`; datasetStrategy: `unresolved-v1`; sourceArgument: `value`.
- `org.junit.jupiter.params.provider.NullSource` → `parameter-source`; datasetStrategy: `unresolved-v1`; sourceArgument: `value`.
- `org.junit.jupiter.params.provider.EmptySource` → `parameter-source`; datasetStrategy: `unresolved-v1`; sourceArgument: `value`.
- `org.junit.jupiter.params.provider.NullAndEmptySource` → `parameter-source`; datasetStrategy: `unresolved-v1`; sourceArgument: `value`.
- `org.junit.jupiter.api.BeforeAll` → `lifecycle`.
- `org.junit.jupiter.api.BeforeEach` → `lifecycle`.
- `org.junit.jupiter.api.AfterAll` → `lifecycle`.
- `org.junit.jupiter.api.AfterEach` → `lifecycle`.
- `org.junit.jupiter.api.Disabled` → `disabled`; argument: `value`.
- `org.junit.jupiter.api.DisplayName` → `metadata`; argument: `value`.
- `org.junit.jupiter.api.DisplayNameGeneration` → `metadata`; argument: `value`.
- `org.junit.jupiter.api.Tag` → `metadata`; argument: `value`.
- `org.junit.jupiter.api.Tags` → `metadata`; argument: `value`.
- `org.junit.jupiter.api.TestInstance` → `metadata`; argument: `value`.
- `org.junit.jupiter.api.Nested` → `metadata`; argument: `value`.
- `org.junit.jupiter.api.Order` → `metadata`; argument: `value`.
- `org.junit.jupiter.api.TestMethodOrder` → `metadata`; argument: `value`.
- `org.junit.jupiter.api.extension.ExtendWith` → `lifecycle`; argument: `value`.

## Пример проверки

```text
@ParameterizedTest
@CsvSource({"1, 2"})
void example(int left, int right) {}
```

Проверьте объявленный CSV-набор и параметры. Для ArgumentsSource должно остаться явное unresolved вместо выдуманных строк.

## Включение и обновление

Зависимости: `language-java`. У них должен быть собственный допустимый режим включения.

Для декларативного анализатора доступен выбор по подходящим файлам при обновлении карты; явное «Выключено» приоритетно. Если пакет требует отдельного импорта документа или настройки движка, это остаётся отдельным действием.

После изменения содержимого, включая wiki, перечитайте каталог: меняется общий fingerprint пакета. Исполняемую версию включают заново явно; изменение правил анализа требует нового снимка для новых фактов.

## Границы и диагностика

Converters, factories и произвольные Java-провайдеры не исполняются; supportsComposed означает поддержку объявленных правил композиции, а не любых расширений JUnit.

Если ожидаемые факты отсутствуют, проверьте точную версию и режим пакета, входные файлы, доступность нужного host-профиля и поля coverage/truncated/UNKNOWN. Отсутствие связи не доказывает отсутствие зависимости.

## Источник

Описание составлено по `mod.json` пакета `framework-junit5` и действующим host-контрактам Atlas. Wiki — авторское объяснение, а не подтверждение исполнения.

Сверено по исходникам: 2026-10-28.
