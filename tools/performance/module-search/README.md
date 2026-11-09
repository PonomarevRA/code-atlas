# Ограниченный Pareto search

```sh
.venv/bin/python tools/performance/module-search/run.py --output tools/performance/module-search/report-20261030.json
```

Повторно используются шесть frozen fixtures M3, включая реальные захваченные
импорты/классы Atlas с partial coverage. Каждый seed выполняется дважды для
проверки полной воспроизводимости. GA, categorical random и Pareto local search
имеют одинаковый current seed и evaluation/attempt caps. Повторы и отвергнутые
hard constraints не бесплатны; все счётчики сохранены в JSON.

SArF simple/Leiden строятся **отдельно**, с сохранением provider status,
preprocessing и времени. Затем их разбиения оцениваются теми же целями MQ/moves.
Их upstream construction не имеет того же бюджета и не объявляется честным
состязанием по скорости/числу вычислений с GA. Backend unavailable сохраняется,
другой алгоритм под именем Leiden не подставляется. Baseline partitions не
передаются в качестве дополнительных seeds только одному search arm.

Измерения на малом frozen corpus не доказывают функциональную полезность,
качество на текущем checkout, maximum capacity, RSS или отзывчивость UI. Это
source-level проверка постановки и воспроизводимости, не consumer qualification.
