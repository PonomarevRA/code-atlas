# Source checkout-map probes

`serve.py` starts a separate source Atlas at an ephemeral loopback port. The default
data root is a new temporary directory. An explicit `--data-dir` must be empty or
marked by this helper; the normal Atlas ProcessLock excludes a second writer.
Stop the owned process explicitly after the probe. It never changes an installed
application, its map registry, MCP configuration or source checkout.

Create a private map with `POST /api/checkout-sessions` and JSON containing the
absolute checkout path and a stable task-specific ownerKey. Poll its metadata
until the private builder has finished and a snapshot is available. Metadata
reads do not extend the inactivity TTL. Rebuild requires JSON `{}`.

`probe.py --port PORT --session SESSION --output OUTPUT.json` uses the real stdio
MCP SDK to search, describe and capture bounded working-tree health. It preserves
the exact snapshot/source identity and records whether useful reads renewed the
one-hour lifetime. Oversized descriptions must be narrowed to a smaller actual
raw node; partial extraction and unknown recommendations remain explicit.

Reproducible UI composition: opt into `tests/test_checkout_maps_browser.py` with
the same `ATLAS_PLAYWRIGHT_MODULE` and `ATLAS_TEST_CHROMIUM` variables used by the
module-analysis browser fixture. It performs actual Graphify extraction of two
isolated maps, checks successful-read versus passive-list lifetime, settings
labels/path/expiry, readable mobile controls and exact-session deletion. Fixture
source and permanent raw JSON are checked byte-for-byte afterwards.

These are source checks. They do not install Atlas, prove arbitrary multi-process
mutations, qualify native packaging, or prevent agents editing the same source
files concurrently. New source tools require discovery; their presence is not
inferred from an old installed MCP connection.
