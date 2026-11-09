# Standalone installers and background operation

The distribution contract applies to Code Atlas, AgMemory, Ag Observer, Ag Tasks and Ag Design. Each application owns its backend and data. There is no runtime dependency on a shared checkout or on Observer/Design.

## Local build

Build the application's native bundle on the target OS first, then:

```sh
python packaging/build_installer.py --manifest packaging/desktop-product.json --source 'dist/Code Atlas.app' --output releases/local
```

macOS output is a DMG containing a native Setup.app and the complete self-contained application. Windows uses Inno Setup 6; Linux packages a frozen native setup GUI and a self-contained payload. Building macOS on this machine does not validate Windows/Linux executables. Target workflows build those separately. Never upload incomplete/foreign observability binaries in Observer releases.

macOS builds are ad-hoc signed, not notarized or trusted Developer ID releases. No installer changes Gatekeeper/quarantine settings or asks for system-wide security exceptions. Native widget extension compilation is a separate acceptance boundary: source/ad-hoc compilation does not prove system-gallery registration. See the widget provider documentation in ag-design/desktop/widgets.

## Installation and updates

Setup offers application, artifact/data and backup directories; close-to-tray is enabled by default, launch at login is an explicit option. Paths must be absolute, canonical, writable and separate. Data/backups cannot be placed inside replaceable applications or source repositories. Setup preserves an existing nonempty data root; changing it needs a separately reviewed migration. Existing application destinations must prove product identity. Quit the application from its tray/menu and stop its MCP runtime before updating; closing the window alone may leave it running. Updates stage the application and retain the previous bundle for rollback only until relocated signature/registration checks pass. After verified success, retire only the exact inactive transaction-owned app; preserve active copies, embedded data, manual/data backups and external stores. Installation and verification never open a browser automatically.

Stable locators:

- macOS: `~/Library/Application Support/AGDesktop/<appId>/installation.json`
- Windows: `%LOCALAPPDATA%/AGDesktop/<appId>/installation.json`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/ag-desktop/<appId>/installation.json`

Schema: `ag-desktop/install-v1`; fields: appId, applicationPath, dataDirectory, backupDirectory, closeToTray, launchAtLogin and optional preferredPort (1024–65535). Explicit service CLI/environment storage overrides take precedence. Invalid/corrupt locators fail visibly; they never silently select another empty database. Updates preserve a configured preferredPort. The tray's storage view shows the runtime's effective data path.

## Tray lifecycle

A normal user window close hides only if a real native tray icon was successfully created. Open restores the existing window/backend; it does not create a second server. Agent requests opens the local bounded history view. Check for updates performs a bounded off-thread request to the pinned PonomarevRA repository and opens the official release page after an explicit click. It never executes release text or downloaded hooks.

Quit, Cmd-Q, SIGTERM and OS session ending enter bounded graceful shutdown, bypassing hide-on-close. Unsupported Linux desktop trays retain close-to-quit behavior. Do not add synchronous database flushes, blocking confirmation dialogs, OS shutdown vetoes or blanket process kills.

## Widget snapshots

`GET /api/desktop/v1/widget` returns `ag-desktop/widget-v1`: appId/applicationVersion/capturedAt/status/origin/source and up to eight bounded request summaries. Serialized UTF-8 is bounded below the native 64 KiB transport cap. No model invocation, request/response body retention, arbitrary file reads, credentials or authenticated-agent claims are introduced. Existing agent journals supply actual request/response summaries; services without them provide explicitly identified operation metadata. Polling endpoints do not feed their own history.

System widgets are snapshots with visible capture/stale/offline status, rather than guaranteed continuous updates. macOS WidgetKit, Windows packaged widget providers and KDE Plasma require their own build/registration/runtime checks. A live journal opened from the tray is a separate application view.

## Release publication

Repositories are pinned under `PonomarevRA/{code-atlas,ag-memmory,ag-observer,ag-tasks,ag-design}`. Releases carry OS/architecture-specific installers, per-file SHA256, SHA256SUMS and `ag-desktop/release-v1` identity metadata. Tags must match the packaged application version. Version values in the runtime, native bundle and desktop product manifest must agree. Current user decision: prepare artifacts locally; repository creation, code push and release upload are deferred.

## Verification boundaries

Run storage corruption/nesting/identity tests, actual native close → tray → restore → Quit tests on isolated stores, installer fresh/update preservation tests, widget byte/privacy/identity/offline checks and matching native build checks. No test reboots or logs off the user's computer. A passing source review is not a claim of unexecuted OS runtime, widget gallery or public download acceptance.

## Shared macOS ag-systems menu

`desktop_support/cocoa_tray.py` and `packaging/desktop/AGSystemsTray.swift` implement the same small native UI protocol for Atlas, Design, Observer, Tasks, AgMemory and the Safari containing app. The preferred owner is the lowest live participating GUI PID; followers remove their fallback item only after that owner advertises an actual status item. Dead/stale owners expire and another application restores the menu. Headless HTTP/MCP processes do not create a GUI participant or duplicate backend.

Close and the native yellow button hide the application and select accessory activation policy, leaving the shared menu instead of a Dock thumbnail. Show restores the existing window and regular activation policy. Explicit Quit/Cmd+Q and OS shutdown keep the existing bounded service-owned termination. Close-to-tray preferences and failed-tray fallback remain respected.

The menu preserves Show, Hide, Quit, Agent requests, Storage and Updates for every participating service; the Safari helper has only window/quit commands. Notifications carry fixed application/operation IDs and bounded presence strings, never page content, keys, paths or userInfo. Presence receipts are limited before OS lookups and native reconciliation runs on the timer. Quit is deduplicated before creating a worker; dialog actions coalesce, including nested AppKit modal callbacks.

This is **same-user UI coordination, not sender authentication or a security boundary**. A bundle/PID lookup checks the claimed process, not the sender of a distributed notification. No privacy-setting or general command execution is exposed. Runtime uses existing AppKit/Foundation; services remain independently packaged without a daemon, networking or a dependency on another running AG service. Source copies must stay identical; consumer-specific application branding and shutdown ownership remain local.

Update only the reviewed tray modules/native host over the current installed baseline. Preserve all unrelated frozen payloads, resources, service versions and external stores. Keep rollback bundles during verification or while a live owner/protected data prevents retirement; successful verified updates otherwise retain zero previous executable bundles. A running process uses already-loaded code until restarted; replacing files does not prove the active GUI changed. Do not interrupt busy service owners solely to show a new icon. Safari's extension bundle and permissions can remain byte-identical when updating only its containing host.

API references: [sandbox-compatible notifications](https://developer.apple.com/documentation/foundation/distributednotificationcenter/post%28name%3Aobject%3Auserinfo%3A%29), [accessory activation policy](https://developer.apple.com/documentation/appkit/nsapplication/activationpolicy-swift.enum/accessory). Native fixtures verify lifecycle, bounded callback work and owner failover without personal browser/window control; manual appearance, VoiceOver and real focus restoration remain separate checks.

Safari host delivery gate: Xcode debug builds can put actual Swift behavior in `Atlas Browser.debug.dylib` behind a tiny launcher. A launcher hash/signature alone does not qualify updated lifecycle code. Build with `ENABLE_DEBUG_DYLIB=NO`, reject debug delegation with `otool -L`, check lifecycle symbols in the installed main executable, preserve sandbox/extension bytes, and verify actual native background/window state. Storyboard attachment is idempotent and also occurs from `viewDidAppear` when the window exists.

The new shared native menu uses an intentional single Russian locale (`ru`; fallback `ru`), with unchanged product names and OS-owned system menus. No automatic translation or native VoiceOver qualification is claimed.


## Shared native tray activity

macOS clients share one elected `AG` status item. The item shows a semantic colour dot and, for observed in-flight API requests, the product name (or concurrent product count). Native menu rows repeat the product and state in text and include a legend. This menu currently uses Russian labels and catalogue-owned product names. Colour alone never carries meaning.

`GET /api/desktop/v1/activity` returns `ag-desktop/activity-v1`, a fixed product ID, state, active-request count and coverage statement. The four Python services keep this state only in memory. Their ASGI observer never reads bodies, query values or client/session identifiers; completed outcomes remain visible for15seconds. Status, desktop telemetry and long-lived event/stream reads are excluded. These are observed API requests, not authenticated agents or proof that a model is executing.

Each GUI polls only its actual numeric loopback origin on one background worker, with no proxy/redirect support, a one-second timeout and a2KiB response budget. No network work runs on AppKit. Eight seconds without a valid response makes the signal stale. An unavailable protocol explicitly remains unknown. Native peers exchange only `1|product|pid|state` through the existing same-user notification namespace. Membership is OS-checked; activity caches are bounded to the live presence table even on followers and expire independently. This UI coordination is not sender authentication.

Blue means a request is in flight, green a recent completion, red a recent failed request, orange stale telemetry, and gray idle/unknown as distinguished by text. No queued/waiting/model-execution state is inferred. AgMemory uses the existing64KiB widget journal only at the origin announced by its own child, with redirects/proxies/cache/cookies disabled. It projects recent completed outcomes; a start event without a finish cannot prove a live request and remains unknown. Atlas Browser itself has no service-request tracker; captures appear as Atlas service work.

In-place updates capture and hash approved bytes before replacement, preserve unrelated frozen/archive/sealed resources and native entitlements, and qualify the exact new GUI/listener. Successful delivery removes temporary rollback files and unused compiled candidates; it creates no additional `.app` copies. Existing MCP clients and browser processes are not terminated. Test/source/native qualification and manual VoiceOver/rendered theme contrast remain distinct evidence boundaries.
