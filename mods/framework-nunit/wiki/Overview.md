# NUnit: тестовые атрибуты

Пакет `framework-nunit`, версия `1.0.0`.

## Для чего

Дополняет снимок лексическими фактами библиотеки: точные роли и стратегии перечислены ниже. Код приложения и тесты не исполняются.

## Что объявлено

- Framework `nunit`, язык `csharp`; supportsComposed: false.
- `NUnit.Framework.TestAttribute` → `test`.
- `NUnit.Framework.TestCaseAttribute` → `test`.
- `NUnit.Framework.TestCaseSourceAttribute` → `test`.
- `NUnit.Framework.ValueSourceAttribute` → `parameter-source`.
- `NUnit.Framework.ValuesAttribute` → `parameter-source`.
- `NUnit.Framework.SetUpAttribute` → `lifecycle`.
- `NUnit.Framework.TearDownAttribute` → `lifecycle`.
- `NUnit.Framework.OneTimeSetUpAttribute` → `lifecycle`.
- `NUnit.Framework.OneTimeTearDownAttribute` → `lifecycle`.
- `NUnit.Framework.IgnoreAttribute` → `disabled`.
- `NUnit.Framework.ExplicitAttribute` → `disabled`.
- `NUnit.Framework.CategoryAttribute` → `metadata`.

## Пример проверки

```text
[TestCase(1)]
public void Example(int value) {}
```

Проверьте TestCase, TestCaseSource и Values/ValueSource как разные источники; также сверьте Ignore/Explicit.

## Включение и обновление

Зависимости: `language-csharp`. У них должен быть собственный допустимый режим включения.

Для декларативного анализатора доступен выбор по подходящим файлам при обновлении карты; явное «Выключено» приоритетно. Если пакет требует отдельного импорта документа или настройки движка, это остаётся отдельным действием.

После изменения содержимого, включая wiki, перечитайте каталог: меняется общий fingerprint пакета. Исполняемую версию включают заново явно; изменение правил анализа требует нового снимка для новых фактов.

## Границы и диагностика

Динамическое построение TestCaseData и пользовательские атрибуты не исполняются; неподдержанный источник должен остаться неизвестным.

Если ожидаемые факты отсутствуют, проверьте точную версию и режим пакета, входные файлы, доступность нужного host-профиля и поля coverage/truncated/UNKNOWN. Отсутствие связи не доказывает отсутствие зависимости.

## Источник

Описание составлено по `mod.json` пакета `framework-nunit` и действующим host-контрактам Atlas. Wiki — авторское объяснение, а не подтверждение исполнения.

Сверено по исходникам: 2026-10-28.
