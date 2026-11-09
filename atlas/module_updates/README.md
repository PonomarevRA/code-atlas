# Инкрементальный анализ состава групп

`atlas.module_updates` — встроенный чистый модуль source-pilot M7. Один владелец
состояния — immutable `UpdateState`, который host удерживает в существующем
`AnalysisContexts`. Модуль не читает исходники и не управляет cache/session/API.
HTTP/MCP, проверка текущего снимка, авторизация и TTL принадлежат адаптерам host.
Данные caller JSON не становятся захваченными фактами из-за корректного hash.

Публичные операции: `initial_state(projection, partition=None,
max_evaluations=4096)`, `derive_delta(old_projection, new_projection)` и
`update(previous_state, new_projection, delta=None, max_evaluations=4096)`.
Первая возвращает `UpdateState`, последняя — `UpdateResult` с `.state` и `.report`.
`state.report` и `state.to_dict()` дают независимые JSON-значения; graph/partition/
refined/parents/aggregate edges удерживаются в frozen tuple-backed records.
Ошибка `ModuleUpdateError` содержит `code` и HTTP-подсказку `status`.

Это **atlas-incremental-directed/v1**, собственная последовательная адаптация.
Она не является LD-Leiden или Leiden. Первичный
[LD-Leiden v2](https://arxiv.org/html/2502.18497v2) и отчёт ATL-218 задают
исследовательское направление. Параллельный decoupling, точный BestRef,
многоуровневая реализация авторов и их результаты здесь не воспроизводятся.

## Граф, objective и обновление

Вход — `module-analysis-v1`: class/folder units с disjoint sourceIds, точными
snapshot/sourceGraphHash/projectionHash/scope/coverage. Ограничения:200units,
5000typededge records,4MiB JSON; веса — положительные целые captured counts.
Типы суммируются по направленной паре, внутримодульные self dependencies
исключаются из Q, как в существующей метрике module_analysis.

`Q = sum_C(e_C/m - Kout_C * Kin_C/m²)`, gamma1; m — сумма направленных весов.
Полный before/after Q оценивает каждый кандидат с константами удаления из исходной
группы. При m0 Q неизвестна (`None`), сохраняются isolates, используется явно
заявленная singleton-конвенция. Частичная source coverage остаётся частичной;
наблюдаемая Q не является границей истинного значения.

Ground moves рассматривают соседние группы и новый singleton. Refine сохраняет
нетронутые refined groups, а внутри затронутого logical parent начинает с
singletons и сливает связанные части при положительном полном gain. Aggregate
block moves переносят неделимые refined groups, поднимая кандидат до ground Q.
Ground labels и refined parents хранятся отдельно. Два уровня и aggregate
self-loops не заменяются только прежними labels. Exact subtract-add repair
вычитает и добавляет каждый изменившийся summand один раз, даже при изменении
обоих родителей. Полная rebuild-проверка включена явно; она стоит полной работы.

Бюджет1..16384 (по умолчанию4096) считает все candidate Q evaluations трёх этапов.
Baseline/report Q и aggregate verification — отдельная работа. Не более8sweeps
на move-этап. При исчерпании бюджета возвращается последний полностью оценённый
результат с `stopReason`, без заявления сходимости/оптимальности. Input partition
— seed, а не must/cannot-link constraint.

`derive_delta` полна только относительно двух наблюдаемых проекций. Точные
identities/coverage и все изменения весов сверяются перед reuse. При изменении
глобального m активируется весь граф: null model зависит от него. Иначе frontier
включает endpoints, touched communities и соседей, расширяясь после moves.
Неизвестная/неполная delta, новые/удалённые units, смена ownership/profile/coverage
дают явный полный fresh recomputation. Невалидная новая проекция отклоняется.
Новые units получают singleton только для измерения прежнего разбиения на новом
графе; пользовательские constraints не придумываются.

Отчёт разделяет Q старого разбиения на старом графе, прежнего разбиения на новом
и результата на новом. Монотонность между снимками не обещается. Membership
disruption — доля common-ID пар, у которых изменилось co-membership; названия
групп на неё не влияют. Added/removed IDs и overlap mapping показываются отдельно.

## M8: отдельная геометрия

`web/module-layout-continuity.js` использует существующий
`stableArchitectureGeometry` на копиях Map. Фасад требует явного coordinate
frame; неизвестный/другой frame отключает reuse и displacement comparison.
Текущие card/label dimensions определяют занятое место. Изменившиеся размеры
могут переместить non-pins; pins сохраняют прежний центр в том же frame, новые —
заявленный текущий центр. Неизвестные/исчезнувшие pins перечисляются явно,
удалённые карточки не резервируют место. Pin-pin overlap сохраняется и обозначает
`readableWithoutOverlaps=false`; он не скрывается ради красивого результата.
Это opt-in фасад до200карточек; он не меняет membership, renderer defaults или
существующий session cache. Маршрутизация рёбер и минимальность displacement
не гарантируются. Browser/native интеграция квалифицируется отдельно.

Проверки: `tests/test_module_updates.py`, `tests/test_module_layout_continuity.mjs`;
frozen эксперимент и реальные ограничения — `tools/performance/module-updates`.
