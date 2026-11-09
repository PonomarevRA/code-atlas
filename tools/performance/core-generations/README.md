# Исполняемая проверка процессов

```sh
.venv/bin/python tools/performance/core-generations/run.py --output outputs/core-generations/source-proof.json
```

Проба создаёт два source bundle из текущих вычислительных файлов. N+1 содержит
совместимый рефакторинг настоящей функции, который копирует два уже рассчитанных
словаря. Оба процесса выполняют реальный module analysis; echo/fake scorer не
используются. Сравниваются все результаты, кроме явно исключённого elapsed time.
Отчёт сохраняет разные PID, хеши actual compute source и bundle, bounded retire
и итоговое закрытие. Время — wall-clock всей последовательности, не CPU и не
отдельный оператор. Подготовка source bundles в замер не входит.

Прежний настоящий MCP-клиент проверяется отдельно в интеграционном harness
стабильного host. Эта проба не касается установленного native приложения,
его frozen offsets, текущих пользовательских карт или живых MCP-процессов.
