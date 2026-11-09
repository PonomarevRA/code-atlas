"""Exercise the advisory CI command using isolated, real Git histories."""

import json
from pathlib import Path
import runpy
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SIMPLE = b"""def score(amount):
    return remote.quote(amount)


def unchanged(amount):
    return amount + 1
"""
NESTED = b"""def score(amount):
    if amount > 0:
        if amount > 5:
            for index in range(amount):
                if index > 3:
                    return remote.quote(index)
    return remote.quote(amount)


def unchanged(amount):
    return amount + 1
"""


def git(repo, *arguments):
    """Use an isolated repository and return the selected Git output."""
    return subprocess.check_output(
        ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", *arguments],
        text=True,
    ).strip()


def commit(repo, description):
    """Capture the current fixture tree and return its immutable revision."""
    git(repo, "add", "-A")
    git(
        repo, "-c", "user.name=Atlas fixture", "-c", "user.email=atlas@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", description,
    )
    return git(repo, "rev-parse", "HEAD")


def run_pilot(repo, base, head, output):
    """Invoke the documented CLI from the trusted Atlas checkout."""
    return subprocess.run(
        [sys.executable, "-B", "-m", "atlas.ci_health", "--repo", str(repo),
         "--base", base, "--head", head, "--output", str(output)],
        cwd=ROOT, capture_output=True, text=True, timeout=25, check=False,
    )


@pytest.fixture
def history(tmp_path):
    """Provide a small repository and its first commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "sample.py").write_bytes(SIMPLE)
    return repo, commit(repo, "base")


def test_advisory_findings_use_changed_lines_and_immutable_blobs(history, tmp_path):
    """A regression is advisory and only emits paths and lines from the selected commit."""
    repo, base = history
    (repo / "sample.py").write_bytes(NESTED)
    (repo / "README.md").write_text("Unsupported report-only fixture.\n")
    head = commit(repo, "nest method")
    (repo / "sample.py").write_text("UNCOMMITTED_SOURCE_CANARY\n")
    output = tmp_path / "report"
    result = run_pilot(repo, base, head, output)
    assert result.returncode == 0, result.stderr

    report = json.loads((output / "atlas-health.json").read_bytes())
    assert report["base"] == base and report["head"] == head
    assert report["summary"]["changedFiles"] == 2
    assert report["summary"]["changedMethods"] == 1
    assert len(report["methods"]) == 1
    method = report["methods"][0]
    assert method["kind"] == "modified"
    assert method["before"]["readability"] > method["after"]["readability"]
    assert method["delta"]["readability"] < 0
    for side in ("before", "after"):
        assert all(method[side][metric] is None for metric in ("health", "organization", "transparency"))

    findings = json.loads((output / "gl-code-quality-report.json").read_bytes())
    assert findings
    for item in findings:
        assert item["location"]["path"] == "sample.py"
        assert 2 <= item["location"]["lines"]["begin"] <= 6
        assert item["severity"] in {"info", "minor", "major", "critical", "blocker"}
        assert item["fingerprint"]
    assert (output / "atlas-health.md").is_file()
    for artifact in output.iterdir():
        assert b"UNCOMMITTED_SOURCE_CANARY" not in artifact.read_bytes()
        assert b"remote.quote" not in artifact.read_bytes()
    assert (repo / "sample.py").read_text() == "UNCOMMITTED_SOURCE_CANARY\n"

    repeated = tmp_path / "repeated"
    result = run_pilot(repo, base, head, repeated)
    assert result.returncode == 0, result.stderr
    assert {path.name: path.read_bytes() for path in repeated.iterdir()} == {
        path.name: path.read_bytes() for path in output.iterdir()
    }


def test_deleted_methods_have_no_head_annotation(history, tmp_path):
    """Removing a method is recorded in the report without inventing a HEAD line."""
    repo, base = history
    (repo / "sample.py").unlink()
    head = commit(repo, "remove methods")
    output = tmp_path / "report"
    result = run_pilot(repo, base, head, output)
    assert result.returncode == 0, result.stderr
    report = json.loads((output / "atlas-health.json").read_bytes())
    assert report["summary"]["changedMethods"] == 2
    assert all(item["kind"] == "removed" and item["line"] is None for item in report["methods"])
    assert json.loads((output / "gl-code-quality-report.json").read_bytes()) == []


def test_invalid_revision_does_not_look_like_a_clean_review(history, tmp_path):
    """An unavailable exact base fails explicitly so CI cannot report a false clean result."""
    repo, head = history
    result = run_pilot(repo, "a" * len(head), head, tmp_path / "report")
    assert result.returncode == 2
    assert not (tmp_path / "report" / "gl-code-quality-report.json").exists()


def test_fingerprints_survive_unrelated_line_shifts(history, tmp_path):
    """A repeated finding retains its fingerprint when both revisions gain a header."""
    repo, base = history
    source = repo / "sample.py"
    source.write_bytes(NESTED)
    head = commit(repo, "regression")
    first = tmp_path / "first"
    assert run_pilot(repo, base, head, first).returncode == 0

    git(repo, "checkout", "-q", "--detach", base)
    prefix = b"# Unrelated header\n\n"
    source.write_bytes(prefix + SIMPLE)
    shifted_base = commit(repo, "header")
    source.write_bytes(prefix + NESTED)
    shifted_head = commit(repo, "same regression")
    shifted = tmp_path / "shifted"
    assert run_pilot(repo, shifted_base, shifted_head, shifted).returncode == 0
    before = json.loads((first / "gl-code-quality-report.json").read_bytes())
    after = json.loads((shifted / "gl-code-quality-report.json").read_bytes())
    assert before
    assert [item["fingerprint"] for item in before] == [item["fingerprint"] for item in after]
    assert [item["location"]["lines"]["begin"] + 2 for item in before] == [
        item["location"]["lines"]["begin"] for item in after
    ]


def test_github_annotation_escapes_workflow_commands():
    """Untrusted text and path delimiters cannot produce a second workflow command."""
    annotations = ROOT / "plugins/atlas-health/github/annotations.py"
    format_annotation = runpy.run_path(str(annotations))["format_annotation"]
    row = {
        "location": {"path": "src/a,b:c%.py", "lines": {"begin": 3}},
        "description": "Review\r\n::error::untrusted",
        "severity": "minor",
    }
    command = format_annotation(row)
    assert "\n" not in command and "\r" not in command
    assert "file=src/a%2Cb%3Ac%25.py,line=3" in command
    assert "Review%0D%0A::error::untrusted" in command
    row["location"]["path"] = "../outside.py"
    assert format_annotation(row) is None


def test_github_annotations_skip_files_different_on_check_revision(tmp_path, capsys):
    """A synthetic merge cannot receive source-branch line positions for different files."""
    annotations = ROOT / "plugins/atlas-health/github/annotations.py"
    emit = runpy.run_path(str(annotations))["main"]
    rows = [
        {"location": {"path": filename, "lines": {"begin": 3}}, "description": "Review", "severity": "minor"}
        for filename in ("changed-on-merge.py", "same.py")
    ]
    report = tmp_path / "code-quality.json"
    report.write_text(json.dumps(rows))
    diff = tmp_path / "check-diff"
    diff.write_bytes(b"changed-on-merge.py\0")
    emit(report, diff)
    output = capsys.readouterr().out
    assert "changed-on-merge.py" not in output
    assert "::warning file=same.py,line=3" in output


def test_changed_owned_documentation_has_its_own_head_line(history, tmp_path):
    """A documentation-only regression points at the changed C# XML comment."""
    repo, _ = history
    source = repo / "Sample.cs"
    source.write_text(
        "class Sample {\n"
        "    /// <summary>Computes the result.</summary>\n"
        "    int Compute() { return 1; }\n"
        "}\n"
    )
    base = commit(repo, "document C# method")
    source.write_text(source.read_text().replace("Computes the result.", "TODO"))
    head = commit(repo, "regress owned documentation")
    output = tmp_path / "report"
    result = run_pilot(repo, base, head, output)
    assert result.returncode == 0, result.stderr
    findings = json.loads((output / "gl-code-quality-report.json").read_bytes())
    regression = next(item for item in findings if item["check_name"] == "atlas-health.documentation-regression")
    assert regression["location"] == {"path": "Sample.cs", "lines": {"begin": 2}}
