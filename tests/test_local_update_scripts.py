"""Exercise repository update orchestration without replacing the user's application."""

import hashlib
import os
import plistlib
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX shell orchestration; Windows needs native CI")


def executable(path, text):
    """Create a local executable double with no dependency on global configuration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nset -eu\n" + text)
    path.chmod(0o755)
    return path


@pytest.fixture
def release(tmp_path):
    """Use real staged installers and fake ABI probes in an isolated, space-containing checkout."""
    root = tmp_path / "checkout with spaces"
    packaging = root / "packaging"
    packaging.mkdir(parents=True)
    for name in ("update-local.sh", "install-macos.sh", "install-linux.sh"):
        shutil.copy2(PACKAGING / name, packaging / name)
    # Native signing is a scoped double; the production installer keeps its fixed
    # trusted codesign path. Identity and filesystem staging remain real on macOS.
    staged_installer = packaging / "install-macos.sh"
    staged_installer.write_text(staged_installer.read_text().replace('/usr/bin/codesign', 'codesign'))
    tools = tmp_path / "tools"
    executable(tools / "uname", 'case "$1" in -s) echo "$TEST_SYSTEM" ;; -m) echo x86_64 ;; esac\n')
    executable(tools / "sysctl", "echo 1\n")
    executable(tools / "lipo", '[ "$2" = "${TEST_CPU:-arm64}" ]\n')
    executable(tools / "codesign", "exit 0\n")
    executable(tools / "file", 'echo "${TEST_FILE_DESCRIPTION:-ELF 64-bit LSB pie executable, ARM aarch64}"\n')
    source = tmp_path / "ready build.app"
    program = '''printf '%s|PATH=%s\n' "$*" "$PATH" >> "$TEST_CALLS"
case " $* " in
  *' --prepare-update '*) exit "${TEST_PREFLIGHT_EXIT:-0}" ;;
  *' --native-document-check '*) [ -z "$PATH" ]; exit "${TEST_CHECK_EXIT:-0}" ;;
esac
'''
    executable(source / "Contents/MacOS/CodeAtlas", program)
    executable(source / "CodeAtlas", program)
    destination = tmp_path / "installed app"
    destination.mkdir()
    (destination / "old-version").write_text("old application")
    for bundle in (source, destination):
        (bundle / "Contents").mkdir(exist_ok=True)
        with (bundle / "Contents/Info.plist").open('wb') as stream:
            plistlib.dump({'CFBundleIdentifier': 'local.code-atlas.desktop'}, stream)
    # The old fixture has legacy embedded data, so conditional retirement must
    # preserve its recovery copy. Unprotected old applications may be removed.
    (destination / ".atlas").mkdir()
    (destination / ".atlas/projects.json").write_text('{}')
    data = tmp_path / "separate data"
    data.mkdir()
    (data / "projects.json").write_bytes(b'{"kept":"byte for byte"}')
    (data / "immutable-document").write_bytes(b"captured document" * 50000)
    calls = tmp_path / "calls"
    environment = {**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"],
                   "HOME": str(tmp_path / "home"), "TEST_CALLS": str(calls), "TEST_SYSTEM": "Darwin"}
    environment.pop("ATLAS_BUILD_TARGET", None)
    environment.pop("ATLAS_BUILD_PYTHON", None)
    return root, source, destination, data, calls, environment


def run_update(release, *options, source=True):
    """Invoke from outside the checkout; explicit profiles cannot touch real installed data."""
    root, ready, destination, data, _, environment = release
    command = [shutil.which("sh"), str(root / "packaging/update-local.sh"),
               "--destination", str(destination), "--data-dir", str(data), "--no-start"]
    if source:
        command += ["--source", str(ready)]
    return subprocess.run(command + list(options), cwd=root.parent, env=environment,
                          capture_output=True, text=True, timeout=30)


def data_fingerprints(directory):
    """Track bytes, inode and mtime so a copy-back cannot masquerade as preservation."""
    fingerprints = {}
    for path in directory.rglob("*"):
        stat = path.stat()
        fingerprints[path.name] = (stat.st_ino, stat.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
    return fingerprints


def test_dry_run_prefers_arm_hardware_under_rosetta_without_mutation(release):
    """An x64 shell on ARM hardware selects ARM and performs no executable calls."""
    _, _, destination, data, calls, _ = release
    before = data_fingerprints(data)
    result = run_update(release, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "Target: darwin-arm64" in result.stdout
    assert (destination / "old-version").exists()
    assert not calls.exists()
    assert data_fingerprints(data) == before


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_ready_install_uses_existing_installer_and_preserves_data(release, system):
    """Real ditto/cp staging preserves external data and old app; ABI probes are doubles."""
    if system == "Darwin" and sys.platform != "darwin":
        pytest.skip("ditto is a macOS utility")
    root, _, destination, data, calls, environment = release
    environment["TEST_SYSTEM"] = system
    if system == "Linux":
        environment["TEST_FILE_DESCRIPTION"] = "ELF 64-bit LSB pie executable, x86-64"
    before = data_fingerprints(data)
    result = run_update(release)
    assert result.returncode == 0, result.stderr
    assert data_fingerprints(data) == before
    backups = list(root.parent.glob("installed app.previous-*"))
    assert len(backups) == 1
    assert (backups[0] / "old-version").read_text() == "old application"
    assert not (destination / "old-version").exists()
    log = calls.read_text()
    assert log.count("--prepare-update") == 2
    assert "--native-document-check|PATH=\n" in log
    assert log.index("--prepare-update") < log.index("--native-document-check") < log.index("--version")
    assert not list(root.parent.rglob("*.zip"))


@pytest.mark.parametrize("variable", ["TEST_PREFLIGHT_EXIT", "TEST_CHECK_EXIT"])
def test_failed_validation_never_replaces_installed_app(release, variable):
    """Busy/unsupported data or a broken frozen runtime stops before staging/rename."""
    root, _, destination, data, calls, environment = release
    environment[variable] = "23"
    before = data_fingerprints(data)
    result = run_update(release)
    assert result.returncode != 0
    assert (destination / "old-version").exists()
    assert data_fingerprints(data) == before
    assert not list(root.parent.glob("installed app.previous-*"))
    assert "--version" not in calls.read_text()


def test_wrong_binary_architecture_fails_before_accessing_data(release):
    """Actual architecture probe failure is fatal, regardless of artifact filenames."""
    _, _, destination, _, calls, environment = release
    environment["TEST_CPU"] = "x86_64"
    result = run_update(release)
    assert result.returncode != 0
    assert "not darwin-arm64" in result.stderr
    assert not calls.exists()
    assert (destination / "old-version").exists()


@pytest.mark.parametrize("options", [("--unknown",), ("--python",), ("--target", "windows-x64")])
def test_invalid_arguments_fail_without_installation(release, options):
    """Typos and foreign OS targets cannot fall through to default installation."""
    _, _, destination, _, calls, _ = release
    result = run_update(release, *options)
    assert result.returncode != 0
    assert not calls.exists()
    assert (destination / "old-version").exists()


def test_build_rejects_an_explicit_wrong_interpreter(release):
    """A requested but mismatching Python must not silently fall back to another one."""
    root, _, destination, _, calls, _ = release
    wrong = executable(root / "wrong-python", "exit 7\n")
    result = run_update(release, "--build", "--python", str(wrong), source=False)
    assert result.returncode != 0
    assert "Python does not match darwin-arm64" in result.stderr
    assert (destination / "old-version").exists()
    assert not calls.exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="Build fixture uses actual macOS staging utilities")
def test_build_uses_matching_interpreter_and_existing_builder(release):
    """Dispatches pip/build in repo cwd, then runs the same frozen/install gates."""
    root, ready, _, _, calls, environment = release
    shutil.copytree(ready, root / "dist/Code Atlas.app")
    build_calls = root.parent / "build-calls"
    environment["TEST_BUILD_CALLS"] = str(build_calls)
    executable(root / "build/arm64-runtime/.venv-arm64/bin/python", '''
printf '%s|%s\n' "$PWD" "$*" >> "$TEST_BUILD_CALLS"
exit 0
''')
    result = run_update(release, "--build", source=False)
    assert result.returncode == 0, result.stderr
    log = build_calls.read_text()
    assert "-m pip check" in log
    assert "packaging/build.py --target darwin-arm64" in log
    assert all(line.startswith(str(root) + "|") for line in log.splitlines())
    assert "--native-document-check" in calls.read_text()


@pytest.mark.parametrize("overlap", ["same", "nested-source", "data", "alias", "future-data"])
def test_overlapping_roots_are_rejected_before_any_commands(release, overlap):
    """Canonical paths catch aliases and nonexistent embedded data, including dot segments."""
    root, ready, destination, _, calls, _ = release
    options = []
    if overlap == "same":
        options = ["--destination", str(ready)]
    elif overlap == "nested-source":
        options = ["--destination", str(ready / "nested")]
    elif overlap == "data":
        options = ["--data-dir", str(destination / "embedded")]
    elif overlap == "alias":
        alias = root.parent / "alias"
        alias.symlink_to(destination, target_is_directory=True)
        options = ["--data-dir", str(alias / "embedded")]
    else:
        future = root.parent / "future"
        options = ["--destination", str(future), "--data-dir", str(future / "absent/../embedded")]
    result = run_update(release, *options)
    assert result.returncode != 0
    assert "Paths must be separate" in result.stderr
    assert not calls.exists()
    assert (destination / "old-version").exists()
