# Install Code Atlas from source

[Русский](install.ru.md) · [English](install.en.md) · [Overview](../README.en.md) · [Installation by an agent](../INSTALL_AGENT.md#english)

These instructions install the local browser application. A public native release is not assumed. The repository includes native build recipes, but a recipe is not a tested release for every platform.

## 1. Choose a directory and Python

Use a directory writable by your account. Administrator privileges are not required for the virtual environment or Atlas. You need:

- 64-bit Python **3.12 or 3.13** with `venv` and `pip`. These versions have been used in local source checks; this is not a guarantee that every locked native dependency has a wheel on every OS/CPU. On Apple Silicon use ARM64 Python.
- Git if you will clone/update a checkout or use Git operations. A source ZIP can be extracted instead.
- Network access for the first dependency installation, or a prepared compatible wheelhouse. Running an already prepared environment does not repeat the installation.

Use the repository URL supplied by the publisher. Replace `<repository-url>` below; it is a placeholder, not an address. If you already have a checkout, inspect it and use its root instead of cloning over it.

```sh
git clone "<repository-url>" code-atlas
cd code-atlas
```

Node.js is needed for JavaScript development checks and rebuilding the wiki, not for this browser launch. Optional semantic analyzers can have their own Node/.NET/Java/Python requirements.

## 2. Create the environment and run

Run these commands from the directory containing `requirements.lock` and `atlas/`. No environment activation is needed. If `.venv` already exists, inspect its Python/version and ownership first; reuse it deliberately or choose a new installation directory rather than recreating an unknown environment.

**macOS / Linux (POSIX shell):**

```sh
python3 --version
python3 -c "import platform,struct; print(platform.machine(), struct.calcsize('P') * 8)"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip check
.venv/bin/python -m atlas.cli --version
.venv/bin/python -m atlas.cli --open
```

**Windows PowerShell:**

```powershell
python --version
python -c "import platform,struct; print(platform.machine(), struct.calcsize('P') * 8)"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m atlas.cli --version
.\.venv\Scripts\python.exe -m atlas.cli --open
```

If `python` selects a different Windows version, use an installed interpreter explicitly, for example `py -3.13 -m venv .venv`. Continue using the resulting `.venv\Scripts\python.exe`.

Keep that terminal open. Visit **http://127.0.0.1:8765** if the browser did not open. Check **http://127.0.0.1:8765/api/health**: a ready server returns `status: "ok"`. This checks HTTP readiness, not successful analysis of a project. Stop the server with `Ctrl+C` in its terminal.

For subsequent launches use only the last command. On macOS/Linux, `sh start.sh --open` is a convenience wrapper for the existing `.venv`; it does not install or update packages. Windows uses the Python command above. Do not run `setup-offline.sh` unless you have prepared its wheelhouse.

Atlas listens on loopback for one local user. Do not change it into a public network service. If another Atlas already uses the same data directory, reuse it or stop it deliberately; do not delete its lock or terminate an unknown process. A different port alone does not create an independent data profile. To run an intentionally separate profile, pass **both** `--port 8877` and `--data-dir` with a new absolute directory outside the source/analyzed repositories. Keep that same port in subsequent MCP setup URLs.

## 3. Build your first map

1. In Atlas select a local repository by its **absolute path**, then choose **«Построить карту»** (build map). Start with a small repository you own or are authorized to inspect. Cloning a remote project is a separate network action.
2. Wait for the snapshot to publish. Background health/search work may continue; its progress does not mean the code map has disappeared.
3. Search for a known method or file, open it and inspect the source and call evidence. The entrypoint catalog is another useful starting point.
4. After changing source files, choose **«Обновить индекс»** (update index). Saved snapshots do not automatically follow working-tree edits.

A map records static evidence, not execution. Missing edges, computed topic/table names and dynamic dispatch can remain unknown. [Explore the map](wiki/Explore-Code.md).

## 4. Connect a local agent through MCP

Keep Atlas running on the **same computer** as the MCP client. This integration launches a **stdio proxy**; the HTTP URLs below generate configuration, they are not HTTP MCP endpoints. A cloud-only agent cannot reach your computer's loopback address.

Open **«Для агентов · Cursor / MCP»** in Atlas, or read:

- [Setup and scope](http://127.0.0.1:8765/api/agent-setup)
- [JSON for clients using `mcpServers`](http://127.0.0.1:8765/api/agent-setup/config)
- [TOML for Codex](http://127.0.0.1:8765/api/agent-setup/config?format=toml)
- [Machine-readable project list](http://127.0.0.1:8765/api/projects)

For repository work choose the actual project ID from that list. Add `?projectId=PROJECT_ID` to the JSON URL, or `?projectId=PROJECT_ID&format=toml` for TOML. Replace and URL-encode the ID; do not use a path or a snapshot ID. Use the unscoped setup only when you intend host-wide access.

The response contains the actual Python executable, `PYTHONPATH`, port and optional project scope. **Merge only the `code-atlas-agent` entry**, preserving all other servers/settings. Back up an existing config before editing; resolve an existing entry deliberately instead of appending duplicate keys.

- Cursor: merge the JSON into the intended `.cursor/mcp.json` (project or client scope).
- Codex: merge the TOML tables into the client's `config.toml`, usually `~/.codex/config.toml`.
- Other clients: use their supported stdio MCP configuration format; do not assume Cursor's file location applies.

Ready skills are available in the [skill catalog](../skills/README.md) and at `/api/agent-setup/skills`. Use `code-atlas` for maps and `code-atlas-engines` for engine diagnostics and setup. Install the complete skill folders for your client and connect MCP using the steps above.

From the Atlas source directory, run `python3 scripts/install_agent_skills.py --client codex --project-root "/path/to/target-project"` with the path of the project you want to analyze. On Windows, use `python` and a Windows path. For Cursor, use `--client cursor`. Review the reported destinations, then repeat with `--apply`. The installer preserves local edits in existing skill folders.

Reconnect/enable the server in the client, then **start a new chat** to refresh discovered tools. Ask it to call `list_projects`; for an authorized map, use `search_nodes` and `describe_node` with the returned project/snapshot scope. Saving a config does not prove the client connected. Report actual calls separately from setup. If the executable location or port changes, fetch fresh config and reconnect.

## 5. Optional features

The public package includes the web interface for source and data maps. Capturing an open page requires a separately supplied browser extension and AG Privacy SDK. Atlas rejects capture when protection is unavailable. [Browser maps](browser-sessions.md)

The locked source environment includes Graphify and base parsers. Optional language semantic engines improve particular facts; availability depends on compatible local runtimes and enabled mods/settings. In **«Моды»** inspect engine availability, run the selected map's compatibility check, and distinguish **discovered / compatible / actually applied**. Installing the application does not prove an engine enriched a snapshot. [Mods](mods-and-graphify.md).

Semantic search needs a separately prepared local embedding model; name search and the code map work without it. Do not download an embedding model or LLM as an installation prerequisite. [Semantic search](semantic-search.md).

A separate desktop window is optional. From the same environment install `packaging/requirements-desktop.txt`, then run `python -m atlas.cli --desktop` using that environment's executable. macOS uses WKWebView, Windows needs WebView2 Runtime, Linux needs QtWebEngine and OS GUI libraries/a graphical session. Prefer browser mode when the GUI stack is unavailable. [Desktop/build requirements](../packaging/README.md).

For offline preparation on a machine with the **same OS, CPU architecture and Python ABI**, the POSIX scripts `sh prepare-offline.sh` and `sh setup-offline.sh` prepare/use `wheelhouse/`. They do not provide a Windows installer. On either platform, a compatible prepared wheelhouse can instead be installed with the environment's Python using `-m pip install --no-index --find-links wheelhouse -r requirements.lock`. Missing wheels must be resolved during preparation, not silently fetched during an offline run.

## 6. Data and updates

Default per-user data locations are:

| OS | Default data directory |
| --- | --- |
| macOS | `~/Library/Application Support/Code Atlas` |
| Windows | `%LOCALAPPDATA%\Code Atlas` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/code-atlas` |

An existing desktop installation locator can select a different directory. `--data-dir` overrides it; `ATLAS_DATA_DIR` also supports an explicit choice. The startup terminal prints the actual directory. Keep it outside application/source/analyzed directories. Managed clones and backups live alongside it as `<data-dir>-repositories` and `<data-dir>-backups`. Do not commit these directories or MCP configs containing local paths.

To update a source checkout:

1. Stop Atlas and disconnect its MCP processes before changing that environment. Preserve your source edits and note the data directory. Do not use `git reset --hard` or `git clean` to make an update fit.
2. Obtain the publisher's intended revision in a clean checkout. If using Git, inspect `git status` first; use a deliberate fast-forward update only when appropriate.
3. Re-run the environment's `-m pip install -r requirements.lock` and `-m pip check`, then launch with the same data directory. Rebuild the map when analysis rules changed.
4. Refresh MCP config if paths/port changed; reconnect and start a new chat.

Data is separate from the checkout. For an explicit backup, with Atlas stopped, run the environment's `-m atlas.cli --backup-data` (also pass the same `--data-dir` if customized); the command prints the archive path. Never delete data or a schema marker to get past an incompatibility. Use a compatible version or a separately agreed migration. Native package updates are a different workflow: [packaging/UPDATING.md](../packaging/UPDATING.md).

## Troubleshooting and verification boundary

| Symptom | Next check |
| --- | --- |
| `venv`/`pip` missing | Check the chosen Python installation. On Linux, ask the machine owner to provision the distribution's venv package; do not use `sudo pip`. |
| Locked dependency cannot install | Read the first dependency/platform error; confirm 64-bit Python version and CPU. Do not remove version pins to hide it. An unsupported wheel/build combination needs a qualified environment. |
| Port occupied or server lock held | Identify/reuse your existing Atlas or choose an independent data profile and port. Do not kill an unrelated service or remove a live lock. |
| Browser did not open | Open the printed loopback URL and `/api/health`; inspect the terminal for the actual failure. |
| MCP has no tools | Check the generated executable path, port and Atlas readiness; reconnect and open a new chat. A config file alone is not a connection test. |
| An optional engine is missing/failed | Inspect per-mod settings and actual compatibility/apply status. Base-map readiness does not prove semantic coverage. |

These commands follow the repository's source interfaces. Local macOS source/native checks exist; **a complete fresh source installation on every Windows/Linux/CPU combination is not claimed**. See the [native target qualification boundary](../packaging/BUILD-TARGETS.md). Record your actual OS/CPU/Python, dependency check, HTTP readiness, first-map result and MCP tool call when verifying an installation.
