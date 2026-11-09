# Chunked Graphify extraction

New `ProjectStore` builds call `build_graph` with bounded extraction enabled. The
adapter retains its two positional arguments; `chunked=False` selects the previous
whole-project path for parity measurements. The chunk protocol is qualified for
Graphify 0.9.85 and rejects another provider version before extraction.

The same standalone resource worker runs in embedded and independent provider
interpreters. It intercepts the CLI's AST dispatch after Graphify has selected the
live corpus, partitions whole files under the original project root, and performs
extraction in batches of at most 32 files or a 2 MiB source target. An individual
larger file remains atomic and is reported explicitly. `chunk_max_files` and
`chunk_target_bytes` can tune scheduling without changing source identities.

Graphify's final chunk JSON loses raw calls and other resolver intermediates.
Atlas therefore uses the provider's original pre-resolution file cache, then runs
its complete global resolution sequence over the live corpus. It does not join
finished chunk graphs by name. Final `graphify-out/graph.json` remains the exact
provider output; scheduling/provenance is stored separately in `atlas-chunks.json`
and exposed as `metadata.chunkAnalysis`.

The complete resolver index still spans the project. This implementation does not
claim a one-chunk bound on global/native memory. JavaScript and TypeScript files
honor Graphify's cache-bypass policy and may be extracted again in the global pass.
The report distinguishes actual file-extractor calls from AST parse counts and
records extraction/global phase durations.

## Reuse between builds

`_provider-cache/atlas-provider-cache-v2/` beside snapshot output directories holds
only compressed, complete vendor extraction payloads. It is separate from snapshot
objects and the private `graphify-out/cache/`, which remains disposable after a
worker completes. The namespace includes the original root, provider version,
grammar/schema namespace, known resolver configuration and the live source path
set. Each entry uses the provider's expected file-content key. Only expected keys
for live, cache-eligible files are restored into a worker's private cache.

Persistent replay is deliberately limited to qualified file-local Python and C#
extractors. XAML reads code-behind/view-model contents and is therefore always
fresh across builds; JavaScript/TypeScript and other unqualified extractors are
also bypassed. A prior cache-protocol namespace is never restored. This whitelist
can expand only with dependency-aware invalidation and warm-versus-cold parity
tests for peer-file edits.

Defaults per managed provider-cache root are 256 MiB compressed, 2048 entries,
16 MiB expanded per entry and 8 MiB compressed per entry. Least-recently-used
entries expire after 24 hours; quotas evict only this module's versioned hash-named
gzip files. A nonblocking process-owned lock serializes retention updates. Cache
corruption, a busy cache or an oversized payload becomes a miss, not a partial map.
No old snapshots, raw graphs or exported paths are removed.

Only a successful global analysis whose source and configuration hashes still
match may export new replay entries. Known in-root resolver settings include
TypeScript/JavaScript configuration, package/workspace/build settings, ignore
rules and local JSONC `extends`/`references`. This coverage is explicit; it is not
a proof of an arbitrary external compiler environment. Source changes re-extract
the changed file, settings or corpus membership changes select a new namespace,
and the full global pass rebinds dependencies every time.

The 24-hour regenerable extraction tier is distinct from the 20-minute idle
working-context cache. Persistent snapshot objects follow snapshot references
rather than either cache lifetime.

## Mod stages

Member and attribute capture iterate file batches while retaining one shared
symbol/owner index and the original whole-snapshot resource budgets. Batch size
does not reset limits or change evidence ordering. Project discovery,
interface/delegate dispatch and global health calculations preserve their original
order and global scope. `atlasFeatures.stageScopes` describes this distinction.

## Qualification

`tests/test_graphify_chunks.py` compares complete node and edge fact multisets,
including call-site evidence, confidence and duplicates, for Python imports,
C# interface calls and TypeScript barrel exports. It also covers independent
runtimes, deleted/renamed callees, unchanged and one-file-changed rebuilds,
configuration invalidation/mutation and cache corruption/quotas/expiry/path limits.
These are bounded fixtures. Whole-repository load and installed-native
qualification remain separate measurements.
