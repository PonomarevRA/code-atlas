# Bundled lnav parser packs

Runtime uses only a hash-verified platform directory with a certified manifest.
There is no PATH fallback, runtime installation, or download.

Upstream 0.14.1 source archive SHA256: `870c896a6973e1e973007be1d66bba3aef23b2aa68411fb5cc85bb7dd21a7fb0`.
Apply `parser-only-0.14.1.patch` before building with `--without-libcurl --without-cargo`.
The patch blocks external converters in secure mode, disables networking-only
helpers when libcurl is absent, and allows this upstream configuration to build.
The runtime also isolates HOME/config/temp and uses only fixed SQL.
This is a parser-only execution profile with resource monitoring, not an OS sandbox.

Prepare an optimized native build on its target OS/architecture, then run
`packaging/lnav_pack.py prepare-macos --binary ... --source-root ... --source-tarball ...`.
Only a pack passing real parser certification is enabled. Stock official binary
archives may be retrieved by the explicit build-time `download` command but are
not certified by that action: their converter behavior differs from the patched build.

## ARM and x64 builds

Each OS/architecture has its own certified directory: `darwin-arm64`,
`darwin-x86_64`, `linux-arm64`, `linux-x86_64`, `win32-arm64`, or `win32-x86_64`.
`aarch64` normalizes to `arm64`; `amd64` normalizes to `x86_64`.
The runtime checks the real executable and native library headers; a matching
manifest name cannot enable a foreign-architecture binary. Mach-O universal
tables are checked against the actual thin slice headers.

On macOS, use `prepare-macos --architecture arm64 ...` on an arm64 Python runner
with arm64 dependencies, or `--architecture x86_64` with x86_64 dependencies.
Every bundled dylib must contain the selected slice; universal dependencies are
accepted when that slice exists. Certification executes a real fixture on the
matching runner. An x86_64 Python process under Rosetta certifies x86_64, even
on Apple Silicon hardware. An ARM-capable compiler alone does not supply the
ARM dependency closure or prove an ARM runtime. Unsupported packs stay unavailable.

## Repeatable native ARM build

The published `darwin-arm64` pack is compiled from the patched upstream source,
with repo-local libunistring 1.3, PCRE2 10.49 and libarchive 3.8.10. Each source URL,
primary checksum source and SHA256 is pinned in `packaging/build_lnav_arm64.py`.
The pack carries those notices/provenance and the actual `LC_BUILD_VERSION` of
the executable and all three bundled dylibs. Their declared minimum is macOS 13.0;
this describes the parser pack, not every dependency of the complete Atlas app.

Run from a native ARM Python build environment with Atlas build dependencies:

```sh
build/arm64-runtime/.venv-arm64/bin/python packaging/build_lnav_arm64.py --jobs 8 --minimum-macos 13.0
```

The command discovers the installed SDK/toolchain (`--sdk` and `--toolchain-bin`
can select them explicitly), uses `-O2 -arch arm64`, and installs build dependencies
only under `build/arm64-lnav-deps`. It leaves global Homebrew/Python and installed
Atlas applications unchanged. Dependencies share the total worker budget; the
main parser build uses `--jobs`. Source builds/downloads happen only here.
End-user installations receive the compiled pack and require no Python/compiler.

Existing output is preserved. Verify it without compiling:

```sh
build/arm64-runtime/.venv-arm64/bin/python packaging/build_lnav_arm64.py --verify-only --minimum-macos 13.0
```

For a deliberate rebuild, select a fresh `--output-root build/new-lnav-packs`.
The build performs real native parser certification before publishing that pack.
