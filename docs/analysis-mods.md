# Language and test-library analysis mods

Atlas analysis mods are declarative packages in the existing application-owned
mod catalog. They are independent of Graphify. An analyzed repository never
activates a plugin, imports Python/Java code into Atlas, or runs Maven, Gradle or
tests. Bundled declarative analysis-only packages default to automatic selection
by project files during a map build. Explicit manual Off takes precedence; manual
activation pins the complete content fingerprint. Dependencies require their own
activation basis. Custom analysis-only packages can opt in with `autoActivate`;
visual and executable packages remain manual. See [automatic analysis](html-mod.md).

`ModManifest.analysis` is an optional extension to mod API v1:

```json
{
  "apiVersion": 1,
  "projectRules": [{
    "id": "java-projects",
    "language": "java",
    "extensions": [".java"],
    "manifestNames": ["pom.xml"],
    "manifestSuffixes": [],
    "sourceRootMarkers": ["src/main/java", "src/test/java"],
    "logicalFolders": true
  }],
  "buildRules": [{
    "id": "gradle-builds",
    "languages": ["java", "kotlin"],
    "manifestNames": ["build.gradle", "build.gradle.kts"],
    "manifestSuffixes": []
  }],
  "testFrameworks": [{
    "id": "junit5",
    "language": "java",
    "supportsComposed": true,
    "annotationRules": [{
      "name": "org.junit.jupiter.api.Test",
      "role": "test"
    }]
  }]
}
```

`apiVersion` is a strict integer; unknown properties, duplicate rule names,
traversal paths and executable/glob/regex declarations are rejected. Rules use
the host's supported language provider, never a package-provided parser.
Capabilities are deliberately separate: a language package declares structural
source roots, while a build-system package discovers its own project markers
and a library package assigns roles to resolved lexical annotations.
Overlapping active project extensions or annotation providers reject a new build;
package name ordering cannot silently overwrite an existing provider's facts.
Disable the conflicting provider before rebuilding. The previous published map
remains available.

Gradle is a separate `build-gradle` package with `analysis.buildRules`, selected
automatically by its declared markers unless manually disabled. It does not
require or activate `language-java`: the same provider can serve an independently
selected `language-kotlin` package.
A build rule is applied only alongside an active matching language project rule;
the build package alone does not infer source roots or logical folders. Bundled
Java and Kotlin language packages retain Maven's `pom.xml` marker and contain no
Gradle marker. Older custom `projectRules` with build markers still work when
they do not compete with another active provider.

`buildRules` contain only bounded `id`, `languages`, `manifestNames` and
`manifestSuffixes`. Exact-name, suffix and name/suffix collisions are rejected
per language, including collisions with legacy language-owned markers. Different
languages may use the same marker. A shared Java/Kotlin Gradle manifest produces
one build-project identity with both provider languages, not two competing
projects. Build descriptors retain the build mod's fingerprint; logical folder
descriptors retain the language mod's fingerprint. Changed package content needs
reactivation before rebuilding. No Gradle script, task or wrapper is executed.
If different providers for disjoint languages encounter the same physical
manifest, discovery reports partial coverage and omits that ambiguous build
boundary. Its real directory remains a logical area labelled “проект не определён”
with `boundaryStatus: ambiguous-build-providers`, without a claimed build owner.
This explicit folder boundary survives offline regrouping and blocks fallback to
an ancestor build; a closer valid descendant build still owns its own sources. This
also applies to legacy Java/Kotlin language rules sharing one Maven file; Atlas
does not select a provider by iteration order.

`active_analysis_rules(mods)` freezes active declarations, their `modId` and
`modFingerprint`, and one aggregate fingerprint for a build.
`assert_analysis_rules_current(mods, rules)` rejects publication if activation or
contents changed during analysis. Discovery alone never rebuilds a map. Changed
manually pinned packages need explicit reactivation and a subsequent map rebuild.
Auto-selected packages use their current validated version at the next build;
saved facts remain tied to the version that produced that snapshot.

## Project discovery

`capture_project_scopes(graph, root, rules)` examines ancestors of already indexed
source paths. A `pom.xml` or an enabled Gradle provider's file can therefore be found even when Graphify
did not create a raw node for it. Only regular marker files inside the repository
are read through the host's nofollow reader. A build marker is evidence of a
project boundary, not proof that all source sets or included builds were resolved.

Nearest exact directory ownership wins. Co-located competing build markers are
left ambiguous instead of choosing one arbitrarily. Java's folder fallback finds
the common package directory and exposes its child folders, such as `assertions`,
`steps`, `tests` or `test_data`. Their kind is `logical-area` and confidence is
`folder-heuristic`; they are not claimed to be Gradle/Maven subprojects. A known
`buildManifest` and `buildProjectId` remain available separately. Source-root
markers are configurable relative path segments, so `src` is not mandatory.

The capture adds `codeProjectFacts` to copied source nodes and stores a descriptor
catalog and coverage in `metadata.projectDiscovery`. Raw node IDs, source paths
and original edges are preserved. `assign_code_projects(nodes)` reuses captured
facts offline, including on later grouping, MCP and entry-point requests. Old
snapshots retain the graph-manifest fallback. Directory scans are capped at 5,000
directories and 512 entries each; markers are capped at 2,000 files, 256 KiB per
file and 16 MiB total; each language rule considers at most 20,000 source files.
Coverage explicitly reports incomplete reads or limits.

Separate bundled language packages cover known project markers for C#, Java,
Python, JavaScript, TypeScript, Go, C++, Kotlin, PHP, Rust and Swift. These packages
declare project discovery only; they do not claim identical language-flow
capabilities or resolve every build-system configuration.

## Test annotations and data

Library rules declare exact fully qualified annotation names and one role:
`test`, `parameter-source`, `lifecycle`, `metadata`, `step` or `disabled`.
Optional `argument`, `sourceArgument`, `displayArgument` and `disabledArgument`
name syntactic arguments for the language adapter. Their expressions are retained
as source evidence; Atlas does not evaluate dataset factories or environment data.

Separate packages declare JUnit 5, JUnit 4, TestNG, xUnit, NUnit, MSTest and Allure.
The Java provider interprets supported Java annotations. The C# bridge reuses
its bounded source inventory and applies only active xUnit/NUnit/MSTest rules.
Inline data stays lexical; dynamic providers remain unresolved references.
A declaration in a package is not
a promise that every feature of that library has been implemented. Custom
composed annotations, inheritance and provider methods must carry resolution
status from the language adapter. An unresolved simple name cannot establish a
test by pretending to be a resolved JUnit annotation.
NUnit's `TestCase` and `TestCaseSource` are both test declarations, even without
a separate `Test` attribute. The latter's provider still remains a distinct data
reference; its rows are not evaluated. See the [NUnit TestCaseSource contract](https://docs.nunit.org/articles/nunit/writing-tests/attributes/testcasesource.html).

Allure is metadata/step support only. `@Step`, `@Feature` or `@Story` cannot by
themselves create a runnable test entry point. Test entry points navigate to real
method IDs using the common entry-path interface. Annotations and lifecycle/data
dependencies remain separately labelled lexical facts, not extra calls inserted
into a runtime trace. Missing reports keep execution and per-test coverage unknown.

The initial entry catalogue omits annotation, fixture and data-row trees. Choosing
a testcase fetches full `testFacts` for its real graph node through the bounded
`graph-node` endpoint. If details exceed the response budget, the catalogue record,
source and entry-path actions remain available with an explicit incomplete label.
The lightweight map projection also omits these evidence trees; the immutable
full snapshot and MCP graph-node reads retain the captured facts.

For `example-java-tests`, enable `language-java`, then `framework-junit5`
and `framework-allure`, and rebuild once. The test catalogue automatically becomes
the starting view when the reader has not chosen a different discovery mode.
Changing a protocol filter remains an explicit user choice.

## Java test call candidates

With an active Java test-framework provider, `java-field-calls-v1` adds candidate
calls for simple field receivers such as `apiSteps.call(...)`. It follows unique
captured superclass fields and exact source types, methods and argument counts.
Shadowed receivers, pattern variables, ambiguous overloads, interfaces and stale
caller/callee/inheritance source stamps stop binding. These edges carry
`INFERRED`, source locations and `runtimeSelectionKnown=false`; declared types
are sufficient for navigation, not proof of the implementation chosen at runtime.
The existing common path renderer uses the candidate edges without a Java-only UI.

## MCP views of captured mod facts

Python uses logical folders when explicit subproject boundaries are unavailable. FastAPI HTTP and FastMCP tool/resource/prompt registrations are separate opt-in library providers, captured once with the map. The versioned `analysis.entrypointProviders` declarations select bounded host strategies; UI and MCP share handler identities and source provenance. See [Python navigation rules](python-navigation-rules.md) for folder fallback, activation and unsupported boundaries.

`search_nodes`, `get_node`, `get_neighbors` and `describe_node` expose the shared `analysisFacts` projection and the snapshot's `analysisContext`. Search also matches captured test labels, tags, annotation names, provider references and build manifests. Descriptions preserve bounded `testFacts`/`codeProjectFacts` independently from working-tree control flow; runtime evidence stays unknown. Existing raw IDs and `get_node.node` are unchanged. See [MCP facts](mod-mcp-facts.md) for boundaries, continuation and truncation rules.

## Объявленные наборы тестовых данных

JUnit-мод выбирает версионированную host strategy через `datasetStrategy`. Именованные входы, ограничения CSV/enum и общий UI/MCP reader описаны в [контракте тестовых данных](test-data-contract.md). После изменения правил требуется явное обновление карты.

## Optional local semantic providers

`analysis.semanticProviders` is an additive v1 capability. Existing manifests and
saved source snapshots need no migration. A semantic-only package has no
`projectRules`: the structural language mod remains its separate owner. For
example, an application-owned Python package can declare:

```json
{
  "apiVersion": 1,
  "id": "semantic-python",
  "name": "Python semantics",
  "version": "1.0.0",
  "autoActivate": {"extensions": [".py"], "manifestNames": []},
  "analysis": {
    "apiVersion": 1,
    "semanticProviders": [{
      "id": "jedi",
      "language": "python",
      "strategy": "jedi-local-v1",
      "capabilities": ["definition", "references"]
    }]
  }
}
```

The strategy selects code owned by the Atlas host. A manifest cannot supply an
executable path, arguments, imports, install commands or arbitrary options. It
cannot execute project configuration, build hooks or dependency restoration.
Allowed strategy/language pairs are `roslyn-local-v1` (C#),
`typescript-local-v1` (JavaScript or TypeScript), `jedi-local-v1` (Python),
`jdt-local-v1` (Java), `gopls-local-v1` (Go), `clangd-local-v1` (C++),
`kotlin-local-v1` (Kotlin), `phpactor-local-v1` (PHP),
`rust-analyzer-local-v1` (Rust), and `sourcekit-local-v1` (Swift). A declaration
reserves the contract; an adapter must still demonstrate actual engine support.

Capabilities are `definition`, `references`, `types`, and `calls`. These are
requested capabilities, not proof that the installed engine supports them.
JavaScript and TypeScript are independent profiles and activation decisions;
they can reuse one host backend without requiring the other language mod.
Manual Off and disable-all take precedence. Dependencies retain the normal mod
rules and must have their own activation basis. Semantic packages should use
explicit `autoActivate` selectors so discovery does not need a language-mod
activation or duplicate project ownership. Two selected semantic providers for
the same language fail explicitly. Atlas never chooses an engine by package name
ordering; select one provider or disable the conflict.

`active_semantic_rules(mods, inventory=...)` freezes these declarations,
activation provenance and fingerprints independently of
`active_analysis_rules`. `semantic_settings_fingerprint` tracks optional
settings separately. A semantic-only mod's discovery, activation, Off, or content
revision does not invalidate the Graphify/source map or request a baseline
rebuild. `assert_semantic_rules_current` rejects publication of an optional result
if selection changed while it ran. Mixed packages hash only their structural
rules for the source map once they declare the separate capability; normal
manual full-package pin/reactivation requirements still apply. Keeping semantic
and structural declarations in separate packages avoids that coupling entirely.

### Shared static evidence contract

`atlas.semantic_models` contains engine-independent dataclasses and strict
persisted-data readers. `SemanticContext` identifies `snapshot_id`,
`workspace_id`, local `workspace_root`, `target_id`, `config_fingerprint`, optional
`provider_fingerprint`, and `source_hashes`. Paths in `source_hashes` are normalized
repository-relative paths with exact lowercase SHA-256 values. Target identity
must distinguish environments and compilation targets: co-located Java/Kotlin
or JavaScript/C# projects do not become one compiler context. The local root and
engine installation paths are omitted from exportable/public summaries.

`SourceSpan` uses zero-based, half-open line/column coordinates. Its explicit
`coordinate_kind` is `utf-16` (code units), `utf-8` (bytes), or `codepoints`.
Adapters normalize their engine positions; graph matching must convert against
the pinned source text, including non-ASCII characters. Reversed, negative,
boolean and traversal coordinates are rejected. Matching requires a unique
captured declaration identity/span and current source hash; a label match alone
cannot establish a graph binding. Local filesystem readers additionally enforce
workspace containment and their symlink policy.

`SemanticSymbol` stores a bounded symbol identity, kind, label, optional source
span, external URI and optional `graph_node_id`. External symbols remain
references; Atlas does not import entire dependency libraries into the graph.
A semantic-only graph node, if materialized, must use an explicit namespace and
must not replace any original Graphify ID.

`SemanticFact` describes a `call`, `reference`, `type`, or `definition` with a
source, optional target and callsite, evidence text and confidence:
`unknown`, `candidate`, or `confirmed-static`. Confirmed static evidence requires
a target. It establishes a static binding, never execution or runtime dispatch.
Unresolved facts keep their unknown targets; candidate edges stay candidates.
Conflicting evidence is retained with its provider rather than overwriting raw
Graphify facts. Deduplication uses source callsite and target identity, not names.

`SemanticResult` pins the context, provider and engine fingerprints, bounded
facts, per-capability `SemanticCoverage`, diagnostics and enrichment status.
Coverage has a status (`complete`, `partial`, `unknown`, `unsupported`), analyzed
count, nullable total and reasons. `complete` requires an exact total; a complete
result requires nonempty complete capability coverage. Unknown dependencies
remain unknown. They do not remove available size, nesting or documentation
measurements and cannot receive an invented healthy score. Missing readability
recommendations are unavailable evidence and do not mean PASS.

### Discovery and Graphify integration

`SemanticAvailability` is distinct from enrichment status. Its discovery states
are `unchecked`, `checking`, `available`, `missing`, `incompatible`, `failed`, and
`disabled`; it carries a reason, optional engine identity/version/capabilities and
UTC epoch `checked_at`/`next_check_at`. A compatible engine without the necessary
project references can have only partial capabilities. A runtime executable or
SDK alone does not establish a ready semantic adapter.

The runtime checks positive and negative discovery results on active map use,
with rolling 24-hour expiration and an explicit manual refresh. No idle timer
scans the machine. A discovery cache is host-local and keyed by workspace,
target, provider and configuration; it does not imply that a result applies to a
new snapshot. Source hashes and snapshot identity fence every applied result.
Timeout, cancellation, missing or incompatible tools retain baseline access.
No engine, runtime, SDK, packages or build outputs are downloaded automatically.

The integration point is after Graphify normalization and base Atlas enrichment.
Raw vendor data, published snapshots and original IDs remain immutable. A
validated optional overlay produces one effective graph for UI, HTTP, MCP and
health consumers. Rebuilding Graphify, replacing its extractor per language or
patching the vendor is unnecessary. Removing or invalidating the overlay returns
the baseline. Cross-language HTTP/OpenAPI bindings require their own contract
adapter and cannot be inferred solely because two language engines are present.

### Application-owned discovery and local settings

Atlas discovers optional engines inside the application; no agent or MCP client
is required. Active map use reuses the host-local positive or negative discovery
observation for a rolling 24 hours, then schedules a bounded background check.
Opening a selected source description can schedule a bounded query for that
captured declaration. Neither action waits for a full project analysis or
installs dependencies. Missing tools leave the existing map available.

The Mods page provides automatic discovery, explicit local path overrides, and a
manual compatibility check against a published map. Paths are host settings,
never executable manifest instructions or exported map content. Empty settings
restore automatic discovery. An invalid explicit path reports missing or
incompatible; it does not silently select a different automatic tool. Saving
semantic-only settings does not require rebuilding the baseline map.

The map's `semantic-status.json` records availability observations separately
from applied enrichment. `available` means that a compatible local engine was
found; it does not mean that definitions or calls were applied to this snapshot.
Exported observations may belong to another host and are labelled as persisted
observations. Applied facts remain static evidence and cannot establish runtime
execution, healthy metrics or a PASS recommendation.

The current overlay represents the last bounded selected scope. A later selected
scope replaces that overlay rather than accumulating earlier queries. The scope
and incomplete coverage remain explicit; unmatched dependencies stay unknown.
Captured health scores are unchanged until the corresponding metrics are
actually reevaluated. The optional layer does not rewrite published snapshot
facts or silently recalculate their historical scores.
