# MSTest: тесты и данные

Пакет `framework-mstest`, версия `1.0.0`.

## Для чего

Дополняет снимок лексическими фактами библиотеки: точные роли и стратегии перечислены ниже. Код приложения и тесты не исполняются.

## Что объявлено

- Framework `mstest`, язык `csharp`; supportsComposed: false.
- `Microsoft.VisualStudio.TestTools.UnitTesting.TestMethodAttribute` → `test`.
- `Microsoft.VisualStudio.TestTools.UnitTesting.DataTestMethodAttribute` → `test`.
- `Microsoft.VisualStudio.TestTools.UnitTesting.DataRowAttribute` → `parameter-source`.
- `Microsoft.VisualStudio.TestTools.UnitTesting.DynamicDataAttribute` → `parameter-source`.
- `Microsoft.VisualStudio.TestTools.UnitTesting.TestInitializeAttribute` → `lifecycle`.
- `Microsoft.VisualStudio.TestTools.UnitTesting.TestCleanupAttribute` → `lifecycle`.
- `Microsoft.VisualStudio.TestTools.UnitTesting.ClassInitializeAttribute` → `lifecycle`.
- `Microsoft.VisualStudio.TestTools.UnitTesting.ClassCleanupAttribute` → `lifecycle`.
- `Microsoft.VisualStudio.TestTools.UnitTesting.AssemblyInitializeAttribute` → `lifecycle`.
- `Microsoft.VisualStudio.TestTools.UnitTesting.AssemblyCleanupAttribute` → `lifecycle`.
- `Microsoft.VisualStudio.TestTools.UnitTesting.IgnoreAttribute` → `disabled`.
- `Microsoft.VisualStudio.TestTools.UnitTesting.TestCategoryAttribute` → `metadata`.

## Пример проверки

```text
[DataTestMethod]
[DataRow(1)]
public void Example(int value) {}
```

Проверьте DataRow и источник DynamicData отдельно; неизвестный вычисляемый набор не должен превращаться в литеральные строки.

## Включение и обновление

Зависимости: `language-csharp`. У них должен быть собственный допустимый режим включения.

Для декларативного анализатора доступен выбор по подходящим файлам при обновлении карты; явное «Выключено» приоритетно. Если пакет требует отдельного импорта документа или настройки движка, это остаётся отдельным действием.

После изменения содержимого, включая wiki, перечитайте каталог: меняется общий fingerprint пакета. Исполняемую версию включают заново явно; изменение правил анализа требует нового снимка для новых фактов.

## Границы и диагностика

MSTest и C#-методы-провайдеры не запускаются. Атрибуты обнаружения не являются отчётом о прохождении теста.

Если ожидаемые факты отсутствуют, проверьте точную версию и режим пакета, входные файлы, доступность нужного host-профиля и поля coverage/truncated/UNKNOWN. Отсутствие связи не доказывает отсутствие зависимости.

## Источник

Описание составлено по `mod.json` пакета `framework-mstest` и действующим host-контрактам Atlas. Wiki — авторское объяснение, а не подтверждение исполнения.

Сверено по исходникам: 2026-10-28.
