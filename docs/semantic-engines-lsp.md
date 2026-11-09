# Optional local LSP engines

Atlas owns discovery and its adapters. Language engines are not added to the
application distribution. A declarative semantic mod chooses a fixed host
strategy; it cannot specify commands, imports or executable project hooks.

The shared `LspSession` transport opens one short-lived stdio process. It supports
UTF-16 definition locations, document synchronization and read-only configuration
requests. It rejects server commands and edits. A session has a 30-second default
deadline, 1 MiB message, 8 MiB cumulative output, 512-message and 128-queued-message
limits, and a sampled 1 GiB process/observed-child RSS budget. Input writes remain
cancellable even when the engine stops reading its pipe. The session terminates
owned processes when the request ends. These are process lifecycle limits, not
an OS sandbox; native Windows detached-child behavior is not qualified here.

## C++ / clangd

`semantic-cpp` activates for the existing C++ source profile. It claims no project
extensions and does not enable a C language profile. Manual Off preserves the
current Graphify map.

The application checks an explicit engine path first, otherwise PATH and the
standard Homebrew LLVM paths. A broken explicit choice never selects a different
engine. The supported initial clangd range is major versions 14 through 23.
Presence alone is insufficient: a bounded version probe and an existing
`compile_commands.json` with a unique captured compilation target are required.
Missing, unsupported or ambiguous contexts retain baseline analysis.

Analysis stages at most sixteen pinned source files into a private directory.
Compilation commands are read as bounded JSON data. Atlas copies a small parser
flag allowlist, rewrites project-contained include paths and rejects response
files, plugins, external include roots and unknown semantic flags. It never runs
the compiler command, CMake, a query driver or a build. Clangd runs with project
configuration, background indexing and clang-tidy disabled. Standard-library and
other unpinned headers are deliberately unavailable in this initial profile.

The engine resolves selected positions. Only a unique pinned definition at an
actual function declaration name becomes a confirmed static call target. A
same-named variable or function-pointer parameter stays a symbol reference with
uncertain invocation, rather than becoming a call to its containing method.
Ambiguous and missing targets remain candidates or unknown. Results describe a
partial selected scope; they never establish runtime dispatch or complete project
dependency coverage.

Before confirming an isolated clangd binding, Atlas proves a bounded pinned
include closure from the original source AST. Missing or macro-generated headers,
unproven include search locations and parser-budget exhaustion downgrade facts to
candidates. This prevents an omitted header's better overload from turning an
incomplete compiler context into a confirmed project edge. This conservative
proof does not scan the repository or rely on asynchronously arriving diagnostics.

Qualification used the already installed Homebrew clangd 19.1.7 on macOS x86_64:
real caller/header binding, function-pointer shadowing, immutable Graphify input,
effective map/MCP projection and Off restoration. Protocol fixtures separately
test framing, server command rejection, malformed output and stalled input. No
engine was installed or downloaded. Other engine versions and native platforms
remain conditional qualification boundaries.

Protocol reference: [LSP 3.17](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/).
Clangd context and query-driver behavior: [official configuration](https://clangd.llvm.org/config).

## Go / gopls

The Go, Rust, Swift and Java source-only profiles do not prove complete compiler,
project and reference closure. Their isolated LSP locations are always candidates,
including when the engine returns one target. They retain source anchors, but add
no confirmed call edges and report zero confirmed-call coverage. A future
closure-qualified adapter must establish that stronger evidence separately.

The independent `semantic-go` mod discovers gopls through PATH, the user's Go bin
directory and common local package locations. A compatible existing Go executable
and bounded `go.mod` identity are required. Merely finding Go is not enough.
Atlas stages captured sources and a synthetic module declaration, omitting
original requirements, replacements, toolchain directives and `go.work`.
CGO, package drivers, module downloads and automatic toolchain downloads are
disabled. External packages and interface dispatch remain partial.

The local qualification environment has no gopls/Go toolchain: `nativeQualified`
is false, not a native PASS. A real cross-file qualification test is retained and
runs only when a compatible engine/context exists. Missing/incompatible engines
preserve the baseline. Static metadata and fallback tests run without a toolchain.
Settings reference: [gopls settings](https://go.dev/gopls/settings).

## Rust / rust-analyzer

The independent Rust profile requires an existing bounded `rust-project.json`
export. `Cargo.toml` alone does not qualify. Captured crate roots and dependency
indices are rewritten into the private workspace; Cargo imports, build scripts,
procedural macros, sysroot discovery and rustup auto-installation are disabled.
Uncaptured external crates and standard-library types remain unknown. Discovery
uses PATH, Cargo's bin directory and common package locations, or the explicit
application setting. The local engine is absent, so `nativeQualified=false`;
the native cross-file test is conditional and metadata/fallback tests run locally.
Reference: [rust-analyzer configuration](https://rust-analyzer.github.io/book/configuration.html).

## Swift / SourceKit-LSP

The independent Swift profile accepts existing static `compile_commands.json`
with an explicit local SDK. It copies a small Swift parser-flag allowlist into a
private compilation database; plugin flags, response files and external module
flags are rejected. SwiftPM `Package.swift`, BSP configuration and build scripts
are never staged. SourceKit background indexing is disabled and the workspace
type is forced to compilationDatabase. The explicit SDK avoids `xcrun` discovery.
Unknown SourceKit versions stay incompatible because its settings schema changes.

The local Apple shim exits with the Xcode license prerequisite; Atlas does not
accept licenses or install tools. `nativeQualified=false`; compatible SourceKit
and SDK are required before the retained cross-file fixture can qualify.
Reference: [SourceKit configuration](https://github.com/swiftlang/sourcekit-lsp/blob/main/Documentation/Configuration%20File.md).

## Java / Eclipse JDT-LS

The independent Java profile requires an installed JDT-LS wrapper, a real JDK21+
and static source-only `.classpath` metadata. External jars, linked projects and
unknown classpath containers are rejected. Atlas writes its own `.project` and
`.classpath`, with empty build commands and annotation processing disabled.
Maven/Gradle import, wrappers and auto-build are disabled before initialization;
original Eclipse preferences, factories, `pom.xml` and Gradle scripts are omitted.

Compatibility requires the actual LSP handshake with definitions/UTF16 support
and a versioned JDT server identity. The local engine is absent, hence
`nativeQualified=false`. JDT engine/classpath closure is not qualified: any
returned source binding remains a candidate, with no confirmed dependency edge.
Reference: [JDT-LS](https://github.com/eclipse-jdtls/eclipse.jdt.ls).

## Kotlin / discovery-only fallback

The independent Kotlin mod currently discovers an explicit/standard local engine
and can run its bounded version operation in an empty private directory. It never
opens the analyzed Gradle/Maven project or executes classpath scripts. An existing
engine reports `incompatible: kotlin-static-context-unqualified`; an absent one
reports missing. There is currently **no qualified Kotlin semantic worker** and
`nativeQualified=false`. The baseline map is retained and unsupported coverage
does not mean PASS. A safe versioned static-export importer is a deferred extension;
Atlas does not guess the official export schema or substitute the legacy importer.
Reference: [official Kotlin LSP](https://github.com/Kotlin/kotlin-lsp).
