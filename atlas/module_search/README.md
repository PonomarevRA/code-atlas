# Module search: bounded offline proposals

`atlas.module_search` — встроенная чистая папка с одним владельцем временного
состояния поиска. Не устанавливает отдельный plugin lifecycle, не меняет карту,
ownership, raw IDs, исходники, geometry или default grouping.

```python
from atlas.module_search import search_partitions

result = search_partitions(
    projection, current_partition,
    constraints={"maxGroupUnits": 12, "mustLink": [], "cannotLink": []},
    seed=7, evaluations_per_method=64, max_proposals=16,
)
```

Вход — существующая `module-analysis-v1` projection уровня class/folder и явное
полное list-of-unit-ID-groups разбиение. `None` не заменяется singleton по
догадке. Hash проверяет согласованность, но не аутентифицирует произвольный
caller JSON: host adapter сам получает captured projection. Snapshot/hash/scope/
coverage возвращаются; partial evidence не превращается в полноту.

Собственный алгоритм `atlas-pareto-ga/v1` использует перенос must-link компоненты,
наследование целой группы одного родителя с оставшимися группами другого и
недоминируемый archive. Это адаптация, не Two-ArchiveGA/NSGA/TC-LM reproduction.
Python RNG version фиксируется внешним harness; вход/seed/версия алгоритма
позволяют воспроизвести результат в том же окружении. Нет скрытого worker/cache.

Две цели, без scalar свёртки:

* Максимизировать `atlasIncidentMQ`: для каждой группы сумма directed inter-unit
  dependency counts внутри неё делится на internal + половину суммы incoming
  и outgoing cut counts; при internal=0 фактор0. Сумма факторов — цель. Это
  явный incident convention Atlas, не утверждение точного совпадения authorMQ.
  Within-unit loops не оптимизируются. Fragmentation/MQ inflation возможны;
  min/max sizes/group counts должны отражать задачу, а не подбираться для победы.
* Минимизировать `movedUnits`: N минус максимальное совпадение между старыми и
  новыми группами при one-to-one matching, с padding пустыми группами. Точное
  matching не зависит от labels/порядка; это не MoJoFM, join cost или время
  рефакторинга. `movedFraction` — только нормировка того же числа.

`constraints` принимает mustLink/cannotLink пары известных IDs,
minGroupUnits/maxGroupUnits/minGroups/maxGroups. Together transitively замыкается;
компонента — неделимый gene. Противоречивые и не удовлетворяемые current
ограничения отклоняются. Invalid candidate отвергается, не ремонтируется молча.
`evaluate_partition` пересчитывает одну supplied partition тем же контрактом.

GA, random categorical и local Pareto mutation имеют одинаковые caps и общий
current seed, отдельные фиксированные RNG streams. Random не uniform по всем
разбиениям. Не более128units/10000edges/256objective evaluations на arm,
4×budget candidate attempts; duplicates считаются evaluations. `methods`
возвращает точные counts/stopReason. Отмена cooperative, перед началом поиска и
внутри fitness/matching; сохраняются только завершённые оценки. Подготовка
ограниченного входа и до трёх baseline оценок — отдельная короткая фаза.

`baselines={"sarf": partition, "leiden": partition}` необязательны. Нет backend
или partition — unavailable, hard constraint violation — infeasible. Upstream
generation cost не входит в fairness трёх arms; provenance caller-supplied не
превращается в проверенную algorithm identity. Baselines не дают GA бесплатный
дополнительный seed. Их создание/сравнение принадлежит offline harness/host.

Возвращается недоминируемый набор **среди вычисленных вариантов**, не доказанный
глобальный Pareto front. `max_proposals`1..16 и60kUTF8 output limit удаляют целые
предложения, `frontierSize/truncated` показывают ограничение. Слишком крупная
identity/baselines отклоняется413. При одинаковых целях возможны разные варианты;
ни несколько вариантов, ни выигрыш MQ не гарантируются каждым входом. Для
source/consumer qualification нужны реальные annotated cases и экспертный review.

Источник направления: [Paixao et al., полный авторский research note RN/16/07,
2December2016](https://oro.open.ac.uk/49002/1/rn_17_07_matheus_paixao.pdf),
соответствующий [журнальной работе2018](https://doi.org/10.1109/TEVC.2017.2691281).
Проверены directed MDG, MQ/DisMoJo, package-free/constrained experiments и threats.
Тот алгоритм использует Two-ArchiveGA и DisMoJo; наш оператор и exact matching
явно отличаются. Издательский MDPI2025 и accepted UCL PDF были недоступны при
квалификации: точное воспроизведение этих версий не заявляется.
