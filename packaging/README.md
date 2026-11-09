# Native standalone packaging

Build on the target OS and CPU architecture. A macOS x86_64 artifact is not a Windows, Linux, or Apple Silicon build.

For repeatable local updates, close Atlas and run `sh packaging/update-local.sh --build`
(macOS/Linux) or `.\packaging\update-local.ps1 -Build` (Windows). Omit the build
flag to install ready artifacts without an external Python. Apple Silicon defaults
to ARM64 even under Rosetta. `--dry-run` / `-DryRun` prints the selected paths
without changes. See [the update runbook](UPDATING.md) for interpreter selection,
custom data directories, validation and rollback boundaries.

```sh
python -m pip install -r requirements.lock -r packaging/requirements-build.txt
python packaging/build.py
```

PyInstaller onedir includes Python, Graphify, grammars, Graphistry/igraph, Pillow, web assets, skill and wiki. Build does not install packages itself. Put the licensed Noto font/license and icon variants in `packaging/assets` before building. Root CI owns signing/publishing; unsigned local macOS builds are not notarized.

Document modules are compiled ahead of time through Cython into native `.pyd`/DLL
extensions on Windows and `.so` extensions on macOS/Linux. Compilation happens in
`build/native-documents/overlay`, never in the source checkout or on the target
machine. Portable release optimization uses `/O2` or `-O2`; numeric safety checks
and Python annotation semantics remain enabled. This does not promise a speedup
for every parser. Runtime Python and parser dependencies are included in the
application; installing Python, Cython, .NET, Java or a compiler is unnecessary
for the base document readers. Existing optional external analysis runtimes are
preserved and are not disabled by this packaging mode.

The build manifest records extension filenames, ABI, compiler flags, and source
and binary SHA256 at the pre-freeze stage. Signing can change native binary bytes.
`python packaging/check_native.py` runs the actual frozen executable with an empty
PATH: all expected modules must come from native files inside the bundle, and
real Markdown/YAML/DOCX/XLSX/PPTX/PDF/JSONL fixtures must retain their contents and
table values. A source-only check or a successful compilation is not this gate.

Artifacts: macOS `dist/Code Atlas.app`; other systems `dist/CodeAtlas/`. The platform installer is copied beside the artifact in `dist`. macOS also retains an onedir directory for CLI smoke tests. `build-manifest.json` records native platform, CPU, Python and data schema.

## Runtime and data

No-argument installed app launch opens a separate desktop window. The same interface remains available at `http://127.0.0.1:8765` in a browser while the app is running. The Atlas menu can open the current map in the browser, preserving its URL parameters. Closing the desktop window keeps its local server in the menu bar; quit from that menu before updating or backing up data.

The executable supports `--desktop`, `--open` (browser), `--no-open` (server only), `--version`, `--port`, `--data-dir`, `--migrate-from OLD`, `--backup-data`, existing `--mcp-config PROJECT`, and `--agent-mcp-config --project-id PROJECT`. These three UI modes are mutually exclusive. Source CLI is silent by default; only explicit `--open` launches a browser. Service commands do not import GUI dependencies.

The shell uses pywebview 6.2.1 and the existing local HTTP server, with no second frontend or privileged project-file JavaScript bridge. WebView storage lives under `<data-dir>/desktop-webview`; downloads are enabled for map exports, and external links open in the browser. Build dependencies include the desktop requirements; no dependency is downloaded at runtime.

- macOS: Cocoa / system WKWebView, with bundled PyObjC bindings.
- Windows: Edge WebView2; Microsoft WebView2 Runtime must already be installed. `CodeAtlasDesktop.exe` opens without a console, while the adjacent `CodeAtlas.exe` handles CLI, MCP and analysis workers with stdio. GUI startup logs are retained in the default data directory as `desktop.log`, rotated at the next launch above 2 MB.
- Linux: QtWebEngine with PySide6, bundled into the native build. A graphical session and system Qt/X11 libraries are required; `--no-open` remains available for headless systems.

Native builds must be produced and exercised on each target OS. `python scripts/smoke_desktop.py` validates the real WebView, DOM, HTTP API and shutdown; Linux CI runs it with Xvfb. Unit tests alone do not validate a platform renderer.

Default persistent data locations:

- macOS: `~/Library/Application Support/Code Atlas`
- Windows: `%LOCALAPPDATA%\Code Atlas`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/code-atlas`

`ATLAS_DATA_DIR` or `--data-dir` overrides these. Managed Git clones and backups are sibling directories named `<data-directory>-repositories` and `<data-directory>-backups`.

Data schema marker is version 1. Future/unknown schema versions are rejected,
never silently downgraded. Installation replaces only application files. The
staged executable runs `--prepare-update`: normal same-schema updates do not copy,
ZIP, migrate or rewrite the separate data directory. A running server blocks the
update. Unknown/old schema requires an explicit backup and migration procedure;
it is never migrated during binary replacement. `--backup-data` remains available
for a manual backup; existing automatic ZIP rotation keeps the last three.
Custom data roots must be supplied through `ATLAS_DATA_DIR` to the installer and
must be outside the replaced application. The previous app is retained only during the installation transaction, then retired after verified success unless active owners or embedded data prevent safe cleanup. Models, managed clones and document snapshots remain in
their existing data locations.

Document retention runs after successful document publication, keeping the last
three snapshots of every map and all snapshots pinned by views, search indices
and research. OS leases exclude deletion during reads/imports. Corrupted or
unknown reference schemas postpone cleanup. This policy does not delete code
maps, Git clones, user-authored notes or arbitrary files from the data directory.

## Legacy migration

```sh
CodeAtlas --migrate-from /absolute/legacy/.atlas --data-dir /absolute/new/data
```

Migration requires a stopped legacy server and a nonexistent destination. It copies all knowledge, saved views, settings and snapshots to staging, rewrites only internal `_graph`, `_rawGraph` and `rawGraphPath` prefixes, then publishes the directory atomically. Source repository paths and free text remain unchanged. Legacy files stay in place. Symbolic links are rejected rather than followed silently. The marker records the migration origin and time. Source-mode launches migrate adjacent `.atlas` automatically only if the default stable directory does not yet exist; packaged apps use the explicit migration command.

## Isolated workers and MCP

The frozen executable accepts the existing internal `-m atlas.offline_runner ...` and `-m atlas.agent_mcp ...` commands. Only those modules and the PNG worker are dispatched; arbitrary Python modules are not exposed. Offline worker socket restrictions remain active. Standalone PNG uses bundled Pillow and Noto, ignoring nonportable legacy `png-runtime.json` interpreter paths.

Git operations still require an installed Git CLI. Code analysis and PNG do not require an external Python. Windows/Linux recipes require their native CI build and test before claiming support. Package installation/signing/download verification is a release-stage responsibility; no runtime dependency download is performed.


### Silent local Safari updates

Builds do not launch Safari, its preferences, or extension options. The generated
containing host remains an `APPL` Mach-O executable, while its Xcode product type
avoids registering each verification output with LaunchServices. The extension
also stays silent on install/update; open its settings explicitly from its menu.

Use the owned build-and-install transaction instead of accumulating renamed apps:

```sh
python3 tools/build_safari_browser.py --destination /private/tmp/atlas-safari-new --install-to "$HOME/Applications/Atlas Browser.app"
```

Use a fresh build directory. Close Safari and quit the Atlas Browser helper before
updating: the installer refuses live file owners rather than interrupting them.
It verifies staged and relocated signatures, registers only the installed app,
restores the previous app on failure, and retires its own previous/generated
bundle after verified success. It never starts the browser. A newly acquired
owner, embedded database/local configuration, or failed ownership check retains
the affected bundle and reports why. Existing manual/data backups are outside
this retention policy. Build without `--install-to` when an artifact is needed
without installation; that artifact is unregistered and remains caller-owned.

The shared macOS setup and shell installer use the same zero-successful-rollback
policy for executable apps. Runtime stores and data backup retention are separate.
