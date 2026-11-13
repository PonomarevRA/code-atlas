# Code Atlas

[Русский](README.md) · **English**

**A local map of code and documents for developers, analysts and AI agents.**

Atlas helps you understand an unfamiliar project: locate an operation's entrypoint, follow calls, read source, inspect tests/data and explore dependencies. The same interface opens in a browser or an optional desktop window. Local agents such as Codex and Cursor access facts through MCP.

[Install](docs/install.en.md) · [Install with an agent](INSTALL_AGENT.md#english) · [Use the map](#using-atlas) · [Architecture](#how-it-works) · [Documentation](#documentation)

## What you can do

| Task | Atlas capability |
| --- | --- |
| Explore an unfamiliar service | Project/folder/file/symbol hierarchy and entrypoint catalog |
| Understand an operation | Paths from entrypoints, method expansion, conditions/loops and source navigation |
| Find external dependencies | Static HTTP, Kafka, RabbitMQ, database and table facts where identifiable |
| Inspect tests | Annotations, declared inputs and data providers through framework mods |
| Assess changes | Complexity, code health, documentation, file/folder structure and change planning |
| Read documents | Trees for Markdown, YAML, Helm, DOCX, XLSX, PPTX, PDF and logs |
| Work with an agent | Symbol search, code/document facts, change assessment and saved explanatory maps through MCP |
| Customize the view | Light/dark themes, card styles, actions and grouping through mods |

The map comes from **static analysis**, not a trace of a running system. Unknown facts and truncation are explicit; a missing arrow does not prove a dependency is absent.

## Quick start

The [complete installation guide](docs/install.en.md) covers prerequisites, first-map verification, MCP, data, updates and troubleshooting. Copy the current repository URL from GitHub's **Code** menu and substitute it in `git clone "<repository-url>" code-atlas`, or extract the supplied source ZIP. Open a terminal in the source directory.

Use **64-bit Python 3.12 or 3.13**; use ARM64 Python on Apple Silicon. Initial dependency installation needs network access or a compatible prepared wheelhouse.

**macOS / Linux:**

```sh
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip check
.venv/bin/python -m atlas.cli --open
```

**Windows PowerShell:**

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m atlas.cli --open
```

Open **http://127.0.0.1:8765**; check `/api/health` and keep the terminal open. Stop with `Ctrl+C`. For subsequent launches, run only the last command. On macOS/Linux `sh start.sh --open` wraps the existing `.venv` without installing packages. `.venv/`, `wheelhouse/` and `dist/` are not supplied by a Git checkout.

To ask a local agent to perform and verify the installation, use the [copyable English task](INSTALL_AGENT.md#english). It preserves other MCP servers, data and existing installations, and separates actual verification from setup.

### Optional desktop window and native packages

Install `packaging/requirements-desktop.txt` in the prepared environment and run its Python with `-m atlas.cli --desktop`. macOS uses WKWebView, Windows needs WebView2 Runtime, and Linux needs QtWebEngine, system GUI libraries and a graphical session. Browser mode avoids that optional GUI stack. [Desktop/build requirements](packaging/README.md).

Open this repository’s **Releases** tab and choose the installer for your OS and CPU. The package includes its Python runtime and base parsers. If your target has no installer, use the source instructions above. [Native target boundaries](packaging/BUILD-TARGETS.md) · [Native installation/update](packaging/UPDATING.md).

## Using Atlas

### First five minutes

1. Select an authorized local repository by its absolute path and choose **«Построить карту»** (build map). Remote cloning is a separate network action.
2. Wait for the snapshot. Health and available search indexes may continue updating in background jobs.
3. Start with an HTTP operation, MCP tool, message handler or test in the entrypoint catalog; otherwise search for a method/file by name.
4. Double-click a card to open its path. Expand calls and inspect source to verify details.
5. Inspect the selected card and health on the right. **Ctrl/Cmd+F** opens map search; Back/Forward restore previous context.
6. After source changes choose **«Обновить индекс»** (update index). A saved snapshot does not update itself.

For an HTTP service, follow `handler → service → data loader → result`. For tests, start with a test, its annotations and data provider. Values declared in a test do not mean the test ran.

[Explore code](docs/wiki/Explore-Code.md) · [Test/data walkthrough](docs/wiki/Walkthrough-Tests.md). Some detailed reference pages are currently in Russian.

### Documents and logs

Open **«Документы»** (Documents), choose a directory or registered project, select files and build a map. The left tree, central structure/tables and right content pane share source coordinates. Agents read saved trees and table windows without reparsing originals.

Readers support `.md`, `.yaml`/`.yml`, static Helm charts, `.docx`, `.xlsx`, `.pptx`, text-based PDF and log profiles. **No OCR**; Excel formulas are not recalculated and Helm templates are not executed. [Formats and limits](docs/document-maps.md).

### Local MCP agents

Ready skills for Codex, Cursor, Claude Code, DeepSeek Harness and other clients
are listed in [skills/README.md](skills/README.md). Use `code-atlas` for code/data
exploration and `code-atlas-engines` for engine verification and setup.
[AGENTS.md](AGENTS.md) explains when an agent should offer installation.
Follow the [client setup steps](skills/README.md) to install the complete skill folders in your chosen project.

Keep Atlas running on the same computer as the MCP process. Use **«Для агентов · Cursor / MCP»** or `/api/agent-setup/config` to generate the real executable, port and project scope. Merge only the Atlas entry into the client's settings, reconnect MCP and start a new chat. Verify `list_projects`, then `search_nodes`/`describe_node` against the returned snapshot scope. HTTP setup URLs are not HTTP MCP transport endpoints.

[Step-by-step MCP setup](docs/install.en.md#4-connect-a-local-agent-through-mcp) · [MCP reference](docs/wiki/Agents-and-MCP.md).

Example requests:

> Find this HTTP handler, explain its path to the database and save a small map.
>
> Show tests using this data provider and the values declared in source.
>
> Before editing, capture health for the selected files, plan changes and compare the result afterward.

MCP supplies facts/tools; the agent composes the explanation. The external AI client's configuration determines where retrieved source context is sent.

### Mods, search and fact collection

In **«Моды»** enable language/framework rules, presentation and workflows. User mods live under `<data-dir>/mods`; active rules affect new snapshots, so rebuild after changes.

Language adapters cover C#, Python, Java, JavaScript/TypeScript, Go, C++, Kotlin, PHP, Rust and Swift with different levels of construct coverage. JUnit, TestNG, xUnit, NUnit, MSTest, Allure, Gradle and HTML/project rules use separate mods.

Additional semantic engines are optional. Distinguish local discovery, compatibility and actual application to a snapshot; installation alone does not prove enrichment. Semantic search needs a separately prepared local embedding model; the code map and name search work without it. `langgraph-guide` collects facts and `automatic-documentation` formats Markdown; **those mods currently have no connected LLM**.

[Mods and independent Graphify](docs/mods-and-graphify.md) · [Semantic search](docs/semantic-search.md) · [LangGraph workflows](docs/mods-langgraph.md) · [Code health](docs/code-health.md).

[Atlas Health for GitLab and GitHub](plugins/atlas-health/README.en.md) reports metrics and line findings for changed code without blocking merges.

## How it works

Atlas is a Python service with a local HTTP API, shared web interface and MCP access. Graphify extracts symbols/relations; Atlas providers and mods add facts and normalize them into saved snapshots. Document parsers create separate structured trees. API/UI/MCP consume these saved facts; health/search may run as background work. Current-source reads have separate provenance and may differ from the snapshot.

| Directory | Responsibility |
| --- | --- |
| [`atlas/`](atlas/) | CLI, API, storage, analysis, MCP, health and search |
| [`atlas/integrations/graphify/`](atlas/integrations/graphify/) | Graphify isolation and normalization |
| [`atlas/flow_languages/`](atlas/flow_languages/) | Language adapters for method flow |
| [`atlas/documents/`](atlas/documents/) | Document parsing/storage |
| [`atlas/research/`](atlas/research/) | LangGraph fact-collection workflows |
| [`web/`](web/) | Maps, cards, source panes and pages |
| [`mods/`](mods/) | Language/library/document/presentation mods |
| [`packaging/`](packaging/) | Native builds/installers/update scripts |
| [`tests/`](tests/), [`docs/`](docs/) | Checks, guides and examples |

[Architecture and request walkthrough](docs/architecture.md).

## Dependencies

Graphify (`graphifyy`) extracts code; FastAPI/Uvicorn serve local HTTP; the MCP Python SDK exposes agent tools; Tree-sitter/Pygments parse/highlight. NetworkX, local PyGraphistry/igraph provide graph analysis without a Graphistry account or GPU. FastEmbed/LanceDB provide optional semantic indexing; LangGraph manages fact-collection stages. Document libraries include python-docx, python-pptx, pypdf/pdfplumber and openpyxl. Drain3/lnav support logs. Cython/PyInstaller support native distribution; pywebview provides the optional window.

[Direct dependencies and native components](docs/third-party.md). Exact source versions: [requirements.lock](requirements.lock); desktop/build extras: [packaging/](packaging/). The official openpyxl upstream is [Heptapod](https://foss.heptapod.net/openpyxl/openpyxl).

## Data, updates and offline use

User data is separate from the application: macOS `~/Library/Application Support/Code Atlas`, Windows `%LOCALAPPDATA%\Code Atlas`, Linux `${XDG_DATA_HOME:-~/.local/share}/code-atlas`. An existing installation locator can select another directory; use the startup output as the actual path. Override with `--data-dir` or `ATLAS_DATA_DIR`, outside analyzed repositories. Managed clones/backups are adjacent `<data-dir>-repositories` / `<data-dir>-backups`.

Once dependencies are prepared, normal analysis/map reads are local. Git clones, dependency preparation, models and reference acquisition may need explicit network access. Executable JS/Python mods are trusted code with your account's permissions. Dynamic dispatch/reflection/computed resource names can remain unresolved; health is advice for review, not proof of correctness.

Native replacement uses a temporary copy of the previous application while verifying installation. After success, an inactive copy without protected data may be removed; a permanent rollback copy is not guaranteed. This is separate from user-data backups.

[Source updates, backups and offline preparation](docs/install.en.md#6-data-and-updates) · [Data/storage reference](docs/project-storage.md) · [Native update workflow](packaging/UPDATING.md).

## Development and checks

```sh
.venv/bin/python -m pytest -q
node tests/test_project_wiki.mjs
```

On Windows use `.venv\Scripts\python.exe`. JavaScript checks and wiki rebuilding need Node.js; CI uses Node 22. Rebuild the bundled wiki after editing `docs/wiki/` with `node scripts/build_project_wiki.mjs`. Native packaging additionally needs its toolchain/build dependencies and real executable/desktop checks; unit tests do not replace running the built application. [Build requirements](packaging/README.md) · [Target boundaries](packaging/BUILD-TARGETS.md).

## Documentation

- [Install manually](docs/install.en.md) or [with a local agent](INSTALL_AGENT.md#english).
- [Prepare a public source package](docs/publication.md).
- [User wiki](docs/wiki/Home.md), [code map](docs/wiki/Explore-Code.md), [tests/data](docs/wiki/Walkthrough-Tests.md).
- [MCP agents](docs/wiki/Agents-and-MCP.md), [health/planning](docs/code-health.md).
- [Documents/logs](docs/document-maps.md), [semantic search](docs/semantic-search.md).
- [Mods](docs/mods-and-graphify.md), [analysis rules](docs/analysis-mods.md), [third-party components](docs/third-party.md).
- [Native updates](packaging/UPDATING.md), [shutdown contract](docs/engineering/standalone-shutdown.md).

The app's wiki entry opens the bundled guide; its source pages live in `docs/wiki/`. Some technical references are currently available only in Russian.
