# Frozen update pilot

```sh
.venv/bin/python -m pytest tests/test_module_updates.py -q
node --test tests/test_module_layout_continuity.mjs
.venv/bin/python tools/performance/module-updates/run.py --evaluations 128 --output tools/performance/module-updates/report-20261030.json
```

Восемь frozen traces /23updates: weighted bridge split/merge, equal-mass locality,
global null-model changes, unknown/incomplete delta, unit/ownership/profile/
coverage fallback и полная заранее выбранная тройка weighted-rewiring seeds7/42/91.
Source-derived trace начинает с исторического AST-import fixture
из module-analysis. В ней сохранены исходные file hashes; **изменения после
базового снимка — заявленные гипотетические edits**, а не реальные коммиты,
runtime dependencies или актуальный source diff. Источник и simulation identity
зафиксированы в JSON; старый source hash не выдаётся за hash новых реальных фактов.
Историческая folder-проекция содержит0наблюдаемых inter-unit dependencies при
частичном покрытии. Это честный no-edges baseline, а не свидетельство отсутствия
реальных зависимостей. Гипотетический cross-folder import добавлен отдельно.

В сохранённом прогоне128candidate evaluations на каждый вызов два инкрементальных
результата проиграли fresh Q. Все23результата и оба проигрыша оставлены в отчёте.
Наблюдение на этих малых входах не задаёт ожидаемую частоту выигрыша на Atlas.

Каждое обновление сравнивается с полным fresh запуском той же Atlas-адаптации на
том же входе и budget. Это не сравнение с точной LD-Leiden/Leiden реализацией.
Все выигрыши/проигрыши сохраняются. Независимый ordered-pair matrix oracle
проверяет Q, отдельное суммирование проверяет все aggregate pairs. Малый
exhaustive oracle перебирает все разбиения до7units; optimum не передаётся поиску.

`updateSeconds` и `freshSeconds` — отдельные wall-clock замеры полных pure calls,
включая validation/Q/aggregate verification/report construction. Delta derivation
и независимые oracles в эти замеры не входят. Бюджет candidate evaluations
не ограничивает общее время baseline/report checks; каждый этап учитывается явно.
Нет заявления CPU-time, RSS, capacity, выигрыша на реальном потоке или нового
production default. Partial coverage и undefined Q при отсутствии рёбер сохранены.
