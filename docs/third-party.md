# Сторонние библиотеки Code Atlas

[Главная страница](../README.md) · [Архитектура](architecture.md)

Здесь перечислены прямые зависимости Atlas и основные компоненты поставки.
Это карта назначений, а не полный SBOM транзитивных зависимостей.
Ссылки ведут на исходные репозитории; для openpyxl указан официальный Heptapod.
Версии ниже соответствуют текущим requirements-файлам, а не новейшим upstream-релизам.

## Откуда берутся зависимости

| Файл | Что фиксирует |
| --- | --- |
| [`requirements-core.in`](../requirements-core.in) | Собственные зависимости Atlas и включаемые наборы |
| [`resources/integrations/graphify/requirements.in`](../resources/integrations/graphify/requirements.in) | Отдельно закреплённый Graphify |
| [`requirements-documents.in`](../requirements-documents.in) | Readers документов и логов |
| [`requirements-research.in`](../requirements-research.in) | LangGraph и SQLite checkpoints |
| [`requirements.lock`](../requirements.lock) | Зафиксированный Python-набор, включая транзитивные пакеты |
| [`packaging/requirements-desktop.txt`](../packaging/requirements-desktop.txt) | Desktop-оболочка и платформенные bindings |
| [`packaging/requirements-build.txt`](../packaging/requirements-build.txt) | Сборщик и компилятор документных модулей |

## Сервис, карта и протоколы

| Пакет | Версия/ограничение прямой зависимости | Назначение и исходники |
| --- | --- | --- |
| graphifyy | 0.9.85, extra `mcp` | Базовое извлечение графа; [Graphify](https://github.com/Graphify-Labs/graphify) |
| fastapi | >=0.118,<1 | HTTP API; [FastAPI](https://github.com/fastapi/fastapi) |
| uvicorn | >=0.35,<1 | ASGI-сервер; [Uvicorn](https://github.com/Kludex/uvicorn) |
| mcp | >=1,<2 | MCP Python SDK и FastMCP; [python-sdk](https://github.com/modelcontextprotocol/python-sdk) |
| httpx | >=0.28,<1 | HTTP-клиент; [HTTPX](https://github.com/encode/httpx) |
| networkx | 3.7 | Графовые алгоритмы; [NetworkX](https://github.com/networkx/networkx) |
| graphistry | 0.59.0 | Локальная CPU-группировка через igraph; [PyGraphistry](https://github.com/graphistry/pygraphistry) |
| igraph | 1.0.0 | Вычисление сообществ и графовые операции; [python-igraph](https://github.com/igraph/python-igraph) |
| PyYAML | 6.0.3 | YAML-данные и конфигурация; [PyYAML](https://github.com/yaml/pyyaml) |
| Pygments | 2.21.0 | Подсветка исходников; [Pygments](https://github.com/pygments/pygments) |
| ijson | 3.4.0.post0 | Потоковое чтение JSON; [ijson](https://github.com/ICRAR/ijson) |
| gherkin-official | 42.0.1 | Разбор Gherkin; [cucumber/gherkin](https://github.com/cucumber/gherkin) |

MCP SDK включает FastMCP, используемый Atlas; отдельный пакет с похожим именем
`fastmcp` не указан в прямых requirements. Graphistry не отправляет карту
в облачный визуализатор: Atlas вызывает локальные вычисления igraph.

## Синтаксический разбор

[Python bindings Tree-sitter](https://github.com/tree-sitter/py-tree-sitter)
закреплены на версии **0.25.2**. Отдельные грамматики:

| Язык / пакет | Версия | Исходный репозиторий |
| --- | --- | --- |
| C# / tree-sitter-c-sharp | 0.23.5 | [tree-sitter-c-sharp](https://github.com/tree-sitter/tree-sitter-c-sharp) |
| Python / tree-sitter-python | 0.25.0 | [tree-sitter-python](https://github.com/tree-sitter/tree-sitter-python) |
| Java / tree-sitter-java | 0.23.5 | [tree-sitter-java](https://github.com/tree-sitter/tree-sitter-java) |
| JavaScript / tree-sitter-javascript | 0.25.0 | [tree-sitter-javascript](https://github.com/tree-sitter/tree-sitter-javascript) |
| TypeScript / tree-sitter-typescript | 0.23.2 | [tree-sitter-typescript](https://github.com/tree-sitter/tree-sitter-typescript) |
| Go / tree-sitter-go | 0.25.0 | [tree-sitter-go](https://github.com/tree-sitter/tree-sitter-go) |
| C++ / tree-sitter-cpp | 0.23.4 | [tree-sitter-cpp](https://github.com/tree-sitter/tree-sitter-cpp) |
| Kotlin / tree-sitter-kotlin | 1.1.0 | [tree-sitter-grammars/tree-sitter-kotlin](https://github.com/tree-sitter-grammars/tree-sitter-kotlin) |
| PHP / tree-sitter-php | 0.24.1 | [tree-sitter-php](https://github.com/tree-sitter/tree-sitter-php) |
| Rust / tree-sitter-rust | 0.24.2 | [tree-sitter-rust](https://github.com/tree-sitter/tree-sitter-rust) |
| Swift / tree-sitter-swift | 0.7.3 | [alex-pinkus/tree-sitter-swift](https://github.com/alex-pinkus/tree-sitter-swift) |

Наличие грамматики не обещает полный семантический анализ языка. Atlas отдельно
определяет, какие конструкции поддерживает его провайдер и какие факты неизвестны.

## HTML и CSS

Эти пакеты используются для статического исследования HTML/CSS, а не как
фреймворк исполнения интерфейса Atlas:

| Пакет | Версия | Исходники |
| --- | --- | --- |
| html5lib | 1.1 | [html5lib-python](https://github.com/html5lib/html5lib-python) |
| cssselect2 | 0.8.0 | [Kozea/cssselect2](https://github.com/Kozea/cssselect2) |
| tinycss2 | 1.4.0 | [Kozea/tinycss2](https://github.com/Kozea/tinycss2) |
| webencodings | 0.5.1 | [python-webencodings](https://github.com/SimonSapin/python-webencodings) |

Сам `web/` использует локальные HTML/CSS/JavaScript-модули и SVG. Node.js нужен
для JS-проверок и подготовки wiki; серверный запуск и готовое приложение его не требуют.

## Документы и логи

| Пакет | Версия | Назначение и исходники |
| --- | --- | --- |
| markdown-it-py | 4.2.0 | Структура Markdown; [markdown-it-py](https://github.com/executablebooks/markdown-it-py) |
| python-docx | 1.2.0 | DOCX; [python-docx](https://github.com/python-openxml/python-docx) |
| openpyxl | 3.1.5 | XLSX; [официальный upstream на Heptapod](https://foss.heptapod.net/openpyxl/openpyxl), [документация](https://openpyxl.readthedocs.io/en/stable/) |
| python-pptx | 1.0.2 | PPTX; [python-pptx](https://github.com/scanny/python-pptx) |
| pypdf | 6.21.1 | Текст и структура PDF; [pypdf](https://github.com/py-pdf/pypdf) |
| pdfplumber | 0.11.10 | Координаты и кандидаты таблиц PDF; [pdfplumber](https://github.com/jsvine/pdfplumber) |
| defusedxml | 0.7.1 | Ограниченное чтение XML; [defusedxml](https://github.com/tiran/defusedxml) |
| drain3 | 0.9.11 | Группировка сообщений по шаблонам; [Drain3](https://github.com/logpai/Drain3) |
| psutil | 7.2.2 | Наблюдение ресурсов worker-процессов; [psutil](https://github.com/giampaolo/psutil) |

`lnav` — **внешний native-компонент**, не пакет pip. Поставляется parser-only
вариант 0.14.1 с проверяемым манифестом под конкретные ОС/ABI;
[исходный lnav](https://github.com/tstack/lnav), [правила поставки Atlas](../resources/lnav/README.md).
Наличие установленного произвольного `lnav` в PATH не заменяет сертифицированный пакет.

Комплект lnav может включать [PCRE2](https://github.com/PCRE2Project/pcre2),
[libarchive](https://github.com/libarchive/libarchive), libunistring и другие библиотеки;
точный состав различается между платформами. Манифесты и тексты лицензий находятся
в `resources/lnav/<target>/`. В текущих macOS-пакетах также встречаются
libb2, LZ4, XZ/liblzma, SQLite и Zstandard. Native-инвентарь не следует
восстанавливать из одного Python lock-файла.

## Поиск и исследовательские процессы

| Пакет | Версия | Назначение и исходники |
| --- | --- | --- |
| fastembed | 0.8.1 | Локальные embeddings; [FastEmbed](https://github.com/qdrant/fastembed) |
| lancedb | 0.25.3 | Локальный векторный индекс; [LanceDB](https://github.com/lancedb/lancedb) |
| langgraph | 1.2.15 | Состояния сбора фактов; [LangGraph](https://github.com/langchain-ai/langgraph) |
| langgraph-checkpoint-sqlite | 3.1.2 | SQLite checkpoints; [checkpoint-sqlite](https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-sqlite) |

Embedding-модель — отдельный артефакт, не LLM и не библиотека. Используется
[paraphrase-multilingual-MiniLM-L12-v2](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2).
Её подготовка выполняется отдельно; поиск не должен автоматически скачивать веса.
LangGraph-моды сейчас собирают и оформляют факты без подключения генеративной модели.

## Окно, сборка и проверки

| Компонент | Версия/ограничение | Назначение и исходники |
| --- | --- | --- |
| pywebview | 6.2.1 | Окно поверх веб-интерфейса; [pywebview](https://github.com/r0x0r/pywebview) |
| Cython | 3.1.6 | Компиляция документных модулей; [Cython](https://github.com/cython/cython) |
| PyInstaller | 6.22.3 | Standalone runtime; [PyInstaller](https://github.com/pyinstaller/pyinstaller) |
| setuptools | 84.0.0 | Инфраструктура сборки; [setuptools](https://github.com/pypa/setuptools) |
| Pillow | 12.3.0 | Растровый экспорт; [Pillow](https://github.com/python-pillow/Pillow) |
| pytest | >=9,<10 | Python-проверки; [pytest](https://github.com/pytest-dev/pytest) |

Desktop backend зависит от ОС: macOS использует Cocoa/WKWebView и
[PyObjC](https://github.com/ronaldoussoren/pyobjc), Windows — системный WebView2,
Linux — [PySide6/QtWebEngine](https://github.com/pyside/pyside-setup). Это отдельные
платформенные зависимости, не одинаковые DLL для всех архитектур.
Для автономной сборки Python уже включён в пакет. Дополнительные анализаторы
могут по-прежнему использовать установленные внешние runtimes.

Шрифт Noto Sans поставляется с [OFL-лицензией](../packaging/assets/NotoSans-LICENSE.txt).
Лицензии библиотек определяются их собственными upstream-проектами и компонентами
конкретного пакета. Страница не назначает им единую лицензию.

Проверено по requirements, metadata установленных пакетов и upstream-ссылкам
21 октября 2026 года. Страница не подтверждает выполнение CI на всех целевых ОС.
