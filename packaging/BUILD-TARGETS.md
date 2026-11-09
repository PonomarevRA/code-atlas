# Native targets

Every artifact contains the Python runtime and document extensions for one OS and
one 64-bit CPU architecture. A separate Python installation is not required on the
target machine. Optional language analyzers keep their existing external-runtime
requirements; this change does not remove Python/.NET/Java integrations.

```sh
# Run with ARM64 Python on an Apple Silicon macOS build runner:
python packaging/build.py --target macos-arm64
# Run with x64 Python on an Intel or Rosetta macOS build runner:
python packaging/build.py --target macos-x64
# Equivalent native Linux and Windows examples:
python packaging/build.py --target linux-arm64
python packaging/build.py --target windows-x64
```

Aliases are normalized to `darwin`, `linux`, `win32` and `x86_64`, `arm64`.
`--target` is validated before compilation and cannot cross-compile or relabel a
binary. On Windows ARM, an emulated x64 Python produces `win32-x86_64`; an ARM64
artifact requires an ARM64 Python interpreter and compatible dependencies.
Without `--target`, the current Python process determines the target.
`ATLAS_BUILD_TARGET` provides the same selection for CI. The build manifest
records the normalized target alongside the actual interpreter/extension ABI.

The PyInstaller spec includes only `resources/lnav/<matching-target>` with a
matching certified manifest. A missing ARM pack leaves lnav unavailable; it does
not package an Intel executable as a substitute. Basic document readers and
structured/Drain3 logs are independent of lnav.

## CI configuration and validation boundary

GitHub Actions declares separate jobs for macOS Intel/ARM64, Linux x64/ARM64 and
Windows x64. Windows ARM64 is an explicit experimental `workflow_dispatch` option.
Jobs install the locked dependencies, build on the selected native interpreter,
and run the actual executable with Python removed from `PATH`. A dependency
without a usable ARM wheel/build environment causes a failed job, never an x64
fallback. Runner labels and Python architecture inputs follow the official
[runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
and [setup-python documentation](https://github.com/actions/setup-python).

GitLab declares equivalent native runner tags. Those tags require separately
provisioned runners; adding YAML does not create hardware. Windows ARM64 is
opt-in with `ENABLE_WINDOWS_ARM64=true` on a manually started pipeline.

These matrix jobs have been configured and YAML/target validation tested locally.
They have **not been executed here**. Availability of all locked native
libraries, the Qt/WebView stack and packaging hooks on every ARM runner remains
subject to those jobs. A local macOS x64 build is not evidence of native ARM,
Linux or Windows release success.

## Certified native resources after freezing

PyInstaller can change Mach-O load commands and signatures even for files listed
as data. It also separates macOS bundle code/data using symlinks. Those operations
invalidate the certified lnav closure, so `build.py` runs a postprocessing step:

- Verify the authoritative matching source pack with the runtime capability check.
- Restore its exact regular-file bytes/modes into the standalone `_internal` tree.
- Restore a complete macOS pack under `Contents/Resources/resources/lnav/<target>`.
  One directory link from `Contents/Frameworks/resources/lnav/<target>` resolves to
  this coherent pack. Individual files inside the resolved pack remain regular.
- Re-sign only the outer `.app`, then verify it with `codesign --verify --deep --strict`.
  Recursive re-signing is deliberately excluded because it would change the
  certified parser bytes again.

The Resources location matters: license directory names inherited from libraries
end in `.dylib`; inside Frameworks, codesign interprets those directories as
nested code bundles. Keeping the complete pack under Resources preserves vendor
paths and manifests while allowing strict outer-bundle verification. Runtime
hash, architecture and internal-symlink checks are unchanged.

A real local macOS x64 prototype passed exact-pack capability checks for both
standalone/app paths and outer signing with deep strict verification. This is
packaging evidence for that local target, not a substitute for the final frozen
runtime check or the unexecuted ARM/Windows/Linux matrix.

Release builds always pass `--clean` to PyInstaller; the user-facing flag remains
accepted for compatibility. Overlay copies deliberately preserve source mtimes.
A source edit made after Analysis but before an older PYZ was written can retain
a timestamp older than that archive. PyInstaller's incremental TOC/mtime checks
would then reuse old code even after a later Analysis. Mandatory clean freezing
avoids this ambiguity. A regression builds a real small PYZ twice through the
ordinary release command, changes source bytes while preserving mtime, and checks
that the second archive contains the new source.

For data-only macOS resource layouts, PyInstaller may already provide one
`Contents/Frameworks/resources → ../Resources/resources` directory alias. The
poststep accepts only that exact resolved destination, validates both managed
parent chains, and reuses the alias. It does not create another link at the pack
leaf, which would otherwise point back into itself. Arbitrary resource-directory
aliases remain rejected before any build-output copy.
