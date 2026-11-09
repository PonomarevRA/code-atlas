# TestNG: тесты и DataProvider

Пакет `framework-testng`, версия `1.0.0`.

## Для чего

Дополняет снимок лексическими фактами библиотеки: точные роли и стратегии перечислены ниже. Код приложения и тесты не исполняются.

## Что объявлено

- Framework `testng`, язык `java`; supportsComposed: false.
- `org.testng.annotations.Test` → `test`; sourceArgument: `dataProvider`; displayArgument: `description`; disabledArgument: `enabled`.
- `org.testng.annotations.DataProvider` → `parameter-source`; argument: `name`.
- `org.testng.annotations.Parameters` → `parameter-source`; sourceArgument: `value`.
- `org.testng.annotations.BeforeSuite` → `lifecycle`.
- `org.testng.annotations.AfterSuite` → `lifecycle`.
- `org.testng.annotations.BeforeTest` → `lifecycle`.
- `org.testng.annotations.AfterTest` → `lifecycle`.
- `org.testng.annotations.BeforeGroups` → `lifecycle`.
- `org.testng.annotations.AfterGroups` → `lifecycle`.
- `org.testng.annotations.BeforeClass` → `lifecycle`.
- `org.testng.annotations.AfterClass` → `lifecycle`.
- `org.testng.annotations.BeforeMethod` → `lifecycle`.
- `org.testng.annotations.AfterMethod` → `lifecycle`.

## Пример проверки

```text
@Test(dataProvider = "rows")
void example(int value) {}
```

Проверьте ссылку dataProvider, описание и enabled; для DataProvider сопоставьте точное имя источника.

## Включение и обновление

Зависимости: `language-java`. У них должен быть собственный допустимый режим включения.

Для декларативного анализатора доступен выбор по подходящим файлам при обновлении карты; явное «Выключено» приоритетно. Если пакет требует отдельного импорта документа или настройки движка, это остаётся отдельным действием.

После изменения содержимого, включая wiki, перечитайте каталог: меняется общий fingerprint пакета. Исполняемую версию включают заново явно; изменение правил анализа требует нового снимка для новых фактов.

## Границы и диагностика

TestNG, XML-конфигурация запуска и произвольные Java-провайдеры не исполняются. Объявленная ссылка ещё не является вычисленным набором.

Если ожидаемые факты отсутствуют, проверьте точную версию и режим пакета, входные файлы, доступность нужного host-профиля и поля coverage/truncated/UNKNOWN. Отсутствие связи не доказывает отсутствие зависимости.

## Источник

Описание составлено по `mod.json` пакета `framework-testng` и действующим host-контрактам Atlas. Wiki — авторское объяснение, а не подтверждение исполнения.

Сверено по исходникам: 2026-10-28.
