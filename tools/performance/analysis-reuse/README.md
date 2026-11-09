# Existing analysis reuse audit

Run from the repository root:

```sh
.venv/bin/python tools/performance/analysis-reuse/run.py
.venv/bin/python -m pytest -q tests/test_analysis_contexts.py tests/test_analysis_context_api.py
```

This probe reuses `AnalysisContexts`. It does not add another cache or run code from an analysed project. Controlled immutable values establish cold/reused equivalence, actual factory counts, source/provider changes, snapshot/settings rejection, idle expiry, oversized non-retention, concurrent single-flight and late-publication fencing after ownership release.

Source contract: `atlas/analysis_section_service.py:cached_parser` keys syntax by filename, exact `sourceByteHash` and `parser_identity`; context ownership additionally checks project/snapshot/settings. Source bytes and snapshot provenance are rechecked by `method_flow_chunks.py` and the surrounding API before reuse. Multi-file descriptions are recomputed and fenced separately; a single-file hash is insufficient for them.

The generic manager trusts the caller's key and values' immutability; it does not infer source identity or prevent mutation of arbitrary cached objects. `parser_identity` is process-resident and includes parser/runtime versions; installing a new parser into a running process does not automatically invalidate it. The application must restart or explicitly change its identity policy.

Retiring ownership prevents late publication but does not interrupt the factory's computation. A transport timeout alone is not proof of cancellation. Byte/AST charges describe retained source/object budgets, not native RSS. Factory counts on tiny fixtures are not an application speedup benchmark; measured production workload performance remains separate.
