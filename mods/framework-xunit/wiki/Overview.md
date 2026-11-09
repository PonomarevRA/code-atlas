# xUnit: Fact, Theory и данные

Пакет `framework-xunit`, версия `1.0.0`.

## Для чего

Дополняет снимок лексическими фактами библиотеки: точные роли и стратегии перечислены ниже. Код приложения и тесты не исполняются.

## Что объявлено

- Framework `xunit`, язык `csharp`; supportsComposed: false.
- `Xunit.FactAttribute` → `test`; disabledArgument: `Skip`.
- `Xunit.TheoryAttribute` → `test`; disabledArgument: `Skip`.
- `Xunit.InlineDataAttribute` → `parameter-source`.
- `Xunit.MemberDataAttribute` → `parameter-source`.
- `Xunit.ClassDataAttribute` → `parameter-source`.
- `Xunit.TraitAttribute` → `metadata`.

## Пример проверки

```text
[Theory]
[InlineData(1)]
public void Example(int value) {}
```

Сравните Fact и Theory, InlineData и ссылки MemberData/ClassData; проверьте Skip и Trait.

## Включение и обновление

Зависимости: `language-csharp`. У них должен быть собственный допустимый режим включения.

Для декларативного анализатора доступен выбор по подходящим файлам при обновлении карты; явное «Выключено» приоритетно. Если пакет требует отдельного импорта документа или настройки движка, это остаётся отдельным действием.

После изменения содержимого, включая wiki, перечитайте каталог: меняется общий fingerprint пакета. Исполняемую версию включают заново явно; изменение правил анализа требует нового снимка для новых фактов.

## Границы и диагностика

xUnit и произвольные MemberData/ClassData не запускаются; набор из исходника и фактическое исполнение теста — разные доказательства.

Если ожидаемые факты отсутствуют, проверьте точную версию и режим пакета, входные файлы, доступность нужного host-профиля и поля coverage/truncated/UNKNOWN. Отсутствие связи не доказывает отсутствие зависимости.

## Источник

Описание составлено по `mod.json` пакета `framework-xunit` и действующим host-контрактам Atlas. Wiki — авторское объяснение, а не подтверждение исполнения.

Сверено по исходникам: 2026-10-28.
