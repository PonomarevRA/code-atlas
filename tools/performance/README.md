# Isolated service HTTP measurements

For bounded backend analysis changes, `analysis_runner.py` compares explicit
source trees against a hash-checked private fixture manifest. It records semantic
inventory, cold/shared source-flow latency, CPU and sampled RSS. Use
`analysis_parity.py` for exact declaration scores/components/findings and
`overlay_runner.py` for cold JSON/integrity validation versus warm derivative
reads. `storage_runner.py` measures one physical canonical JSON read and hydration
against an exact fixture hash; compare its full decoded-graph hashes across
plain and packed inputs in fresh paired processes. It creates only private
temporary store state and excludes overlays from that measurement.
These tools measure backend analysis. Preserve raw receipts outside the
repository and retain failed or superseded runs.

`service_runner.py` launches one frozen standalone backend at a time against a
fresh copy of synthetic data. It does not load a user's database or launch a
native WebView. Embedded children are included only when the specification
starts them. Use a separate Observer full-stack profile for Grafana/Prometheus.

The host Python environment needs `httpx` and `psutil`. Freeze artifact and seed
manifests explicitly before starting a series:

```sh
python tools/performance/service_runner.py /absolute/private/spec.json --freeze-manifests
python tools/performance/service_runner.py /absolute/private/spec.json \
  --output /absolute/private/before --repeats 3 --requests 600 --idle-seconds 10
```

Put bundles, copied fixtures and measurements outside source repositories.
Specifications must explicitly override **every** data location, including an
external component library and backups. HOME remains unchanged. The inherited
environment is allowlisted; declared runtime/GC knobs are recorded for pairing.
Stop other AG desktop applications gracefully before timing, keep an exact
restore list, and restore them in a `finally` block. Do not stop foreign IDE MCP
clients or unrelated applications; record that host isolation is not exclusive.
Do not build, index or archive files concurrently with timed phases.

A specification declares `service`, private loopback `port`, `command`,
`artifactRoot`, `criticalArtifactFiles`, `seed`, `env`, `health` and `requests`.
Command/environment placeholders are `{data}` and `{port}`. Every request needs
a deterministic semantic assertion (`expectedValues`, `expectedLengths`,
`minimumLengths`, `minimumValues`, `expectedText` or `minimumLineCounts`). For
example, a seeded page should assert actual identities/counts, not just HTTP 200.
`seedFilesReplaceData` names copied text fixtures containing `__PERF_DATA__`.
Optional finite `soakSeconds` in 0..3600 adds a separate concurrency-four sustained phase.
Archive the exact runner beside completed reports before later harness changes; a
changed runner hash requires fresh paired measurements.

Keep failed receipts. Never overwrite or silently discard a failed run, treat a
pilot as a full repeat, mix changed fixture versions, or waive a nonzero shutdown
exit. `valid` requires semantic success, complete sampled metrics and verified
owned-process teardown. The process tracker checks PID creation times before
signalling, and never signals an unowned PID. Artifact inventories and critical
code hashes are checked before every run; full artifact content hashes are
captured by immutable preparation manifests.

Pair reports only with `pair_compatible`: same schema, workload, seed, runner,
profile and runtime knobs. Artifact hashes may differ intentionally. Report
medians and ranges across all three fresh repeats, per-operation latency,
successful throughput, CPU per successful request, peak RSS and idle/cooldown.
An unchanged artifact after run measures repeatability, not an optimization.

Limits: closed-loop HTTP, not an open-loop arrival-rate SLA test; RSS sums may
double-count shared mappings; 5 Hz sampling can miss brief peaks and short-lived
children. Disk logical bytes and allocated blocks are distinct. APFS shared
extents and filesystem snapshots are not deduplicated by inode accounting, so
neither `du` nor a noisy free-space delta proves uniquely reclaimed space.
