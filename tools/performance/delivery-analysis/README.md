# Frozen синтетические комплектации

Четыре JSON-входа помечены `declared-synthetic`. Они не извлечены из Atlas,
не являются реальными требованиями продукта и не исполняют код проекта.

- `hand-optimum`: ручной минимум3 против all-in-one6 при двух модулях.
- `module-induced-dependency`: добавленный файлом модуля функционал вызывает
  дополнительное dependency closure; однопроходного расширения недостаточно.
- `cycle-and-trace`: SCC, multi-file requirement и пустая комплектация.
- `seeded-tradeoff`: восемь файлов, двенадцать requirements и шесть вариантов;
  mappings сгенерированы один раз с seed1945 и затем заморожены в JSON.

```sh
.venv/bin/python tools/performance/delivery-analysis/run.py --evaluations 64 --output tools/performance/delivery-analysis/report-20261030.json
```

По умолчанию используются search seeds7,42,91. Все четыре клетки
SCC/no-SCC × uniform/TC-inspired имеют один и тот же лимит64полных objective
evaluations и в сохранённом отчёте полностью его расходуют. Дополнительный
exhaustive oracle оценивается отдельно и не передаёт оптимум в поиск.

Сохранены все48результатов;25хуже применимого малому encoding точного
минимума. TC-inspired иногда выигрывает, иногда проигрывает uniform.
Из результата нельзя выбрать production default или перенести результаты
публикации21/120 на этот прототип. Encoding/SCCconstraint и траектория поиска
различаются; это наблюдаемая абляция, не оценка чистой причинной роли crossover.

Точные методы адаптации primary PDF, ограничения входа, направления и closure
описаны в `atlas/delivery_analysis/README.md`. Отчёт содержит hashes
нормализованных проблем, версии Python/NetworkX, назначения, состав поставок
и implemented requirements, actual search budget, gap и все lost cases.
`elapsedSeconds` — прошедшее время по `time.perf_counter`, а не CPU time.
`timingKind` и `timingScope` у каждого run явно описывают полный вызов
`compare_search`: exact oracles, baselines и все четыре клетки поиска вместе.
Это не время отдельного оператора. Чтение fixture и сериализация отчёта
в замер не входят. Oracle и baselines входят в этот замер времени, но остаются
вне лимита objective evaluations генетического поиска. RSS, native приложение,
UI, реальные проекты и production производительность не квалифицировались.
