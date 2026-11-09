# Standalone application shell

Use this convention for Atlas, AgMemory, ag-observer, ag-tasks and future standalone applications. The user requested the shared convention on 2026-10-21.

- Fill the native window's content area on first launch. Remove browser-page outer margins, inset rounded application frames, duplicate borders and shadows in native mode.
- Keep the operating system's window controls. Hide HTML traffic lights in native mode; retain browser controls where useful in browser mode.
- On macOS use genuine native application, File, Edit, View, Window and Help menus. Include only supported actions, with normal keyboard shortcuts and native text-editing commands.
- Filling the content area does not mean entering operating-system fullscreen automatically. The native green button and View menu control fullscreen.
- Preserve browser behavior, local HTTP boundaries, data directories and CSP. Do not introduce `unsafe-eval` to implement menus.
- Keep services independently packaged. Reusing this convention must not create a runtime dependency on another service.
- Put frequent navigation commands in the native window toolbar when supported. On macOS start with a framed `NSWindow` and `NSToolbar` in `unifiedCompact` style; keep real traffic lights. The sidebar toggle belongs there, with a tooltip and `Cmd+Shift+S`, leaving an icon rail when collapsed.
- Place the toggle above the corresponding navigation group. In expanded mode, a visual header divider continues the sidebar/content boundary; navigation stays on the left in every view, including Grafana; collapsing changes only its width. Put the blue application mark and name in the real native titlebar, on the same row as window controls and the sidebar toggle, to the right of the sidebar divider. Hide the duplicate web identity row only after native-identity readiness; retain browser fallbacks and move necessary commands to native menus or the compact content toolbar. Share measured boundaries with the web header instead of maintaining unrelated pixel offsets.
- Use public titlebar accessories when precise grouping requires them. A CSS split is not an `NSSplitView`, so do not create a fake split view just to use a tracking-separator toolbar item. If a collapsed left rail is narrower than the native traffic-light cluster, preserve the real OS controls and hide only the impossible titlebar divider; retain the icon rail and toggle.
- Treat the OS menu bar and window toolbar as separate surfaces. Keep corresponding web controls until a successful native capability handshake, and retain the web fallback in ordinary browsers and unsupported backends.
- Reuse the application's existing history for native Back/Forward; a SPA history is not the WebView's URL history. In Atlas, `atlas/native_navigation.py` reads the existing map buttons' enabled state and invokes their fixed actions. Disabled-state notifications share the bounded, null-only presentation channel; reload and close reject stale native updates. The native group is Back, Forward, Sidebar, followed by the measured divider and application identity.
- Keep shared command identities and semantics across operating systems, with separate native adapters. Windows HWND/Windows App SDK integration and GTK header bars require their own packaging and runtime checks; Qt may retain a toolbar below the system title bar.

Keep runtime data outside both the repository and replaceable application bundle. Atlas defaults to the OS user-data directory (`~/Library/Application Support/Code Atlas` on macOS). The repository ignores legacy `.atlas`, local captures under `outputs`/`evidence`, databases and log segments. Keep authored contracts, policies and synthetic fixtures in Git; never force-add working databases, project snapshots or captured journals. Compatible application updates inspect schema/lock state without copying or migrating user data.

Verify source contracts and the built native artifact separately. A passing CSS or menu test alone does not establish rendered appearance, keyboard behavior or installation parity.

See [the source-backed titlebar study](standalone-titlebar.md) for APIs, current pywebview backend constraints, platform fallbacks and acceptance criteria. Implemented macOS behavior and the Windows/Linux research plan must be reported separately.

Bounded exit and OS-specific termination policy: [standalone-shutdown.md](standalone-shutdown.md).

### Local desktop automation

Launch Atlas with `--desktop --enable-desktop-automation` to allow local MCP clients to inspect and navigate its desktop window. This mode trusts local processes on the machine. The transport header identifies request format and browser requests are rejected. Ordinary launches leave automation disabled.

Call `inspect_desktop_window` to obtain the window, project and snapshot identifiers. Pass all three to `execute_desktop_command` or `capture_desktop_window`. Commands use the same map and journal controllers as the interface. A reopened window has a new ID. Refresh the state after a conflict.

The command set covers opening a saved project or node, opening and filtering MCP history, closing history, and returning to the map. The capture tool returns the Atlas WebView content as a PNG where its renderer supports capture. It covers the web surface, while the operating system owns the outer title bar and native menus.
