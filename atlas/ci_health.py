"""Advisory, source-only Atlas code health for an immutable Git diff."""

import argparse
from bisect import bisect_left
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from . import ci_health_git as git
from .code_health import ANALYZER_VERSION, calculate_health
from .code_health_policy import policy
from .code_health_source import language_for, parse_health_source
from .readability_assessment import readability_policy


_MAX_FILES = 128
_MAX_TOTAL_BYTES = 16 * 1024 * 1024
_MAX_METHODS = 120
_MIN_SCORE_DROP = 5
_STATUS_LABELS = {"A": "added", "D": "deleted", "M": "modified", "R": "renamed", "T": "type-changed"}
_METRIC_KEYS = (
    "readability", "documentation", "maxNestingDepth", "branchCount", "statementCount",
    "health", "organization", "transparency",
)
_DOC_REGRESSION = "Documentation score decreased by {drop:g} points on an edited declaration."
_READABILITY_REGRESSION = "Local readability score decreased by {drop:g} points on an edited declaration."


def _parse(path, content, exists):
    """Return a trusted method set or a fixed reason for unavailable coverage."""
    if not exists:
        return [], None
    if b"\0" in content:
        return [], "binary-source"
    try:
        content.decode("utf-8")
    except UnicodeError:
        return [], "invalid-utf8"
    result = parse_health_source(path, content, source_kind="git-blob", max_methods=_MAX_METHODS,
                                 include_documentation_lines=True)
    if result["truncated"]:
        return [], "declaration-budget"
    if result["status"] != "complete":
        return [], "incomplete-parse"
    return [node for node in result["methods"] if node["kind"] != "deferred"], None


def _metric_values(node, health):
    """Project an allowlist of numeric facts; source strings never enter artifacts."""
    complexity = node["complexity"]
    values = {"readability": health["readabilityScore"], "documentation": health["documentationScore"],
              "health": health["score"], "organization": health["organizationScore"],
              "transparency": health["transparencyScore"]}
    for key in ("maxNestingDepth", "branchCount", "statementCount"):
        values[key] = complexity.get(key) if complexity["status"] == "complete" else None
    return values


def _changed_line(first, last, ranges, ends):
    """Find the first changed Git line inside an inclusive source range."""
    index = bisect_left(ends, first)
    if index < len(ranges) and ranges[index][0] <= last:
        return max(first, ranges[index][0])
    return None


def _anchors(node, ranges, ends):
    """Locate edited declaration and owned documentation independently."""
    if node is None:
        return None, None
    line = _changed_line(node["line"], node["endLine"], ranges, ends)
    for first, last in node["ownedDocumentationLines"]:
        doc_line = _changed_line(first, last, ranges, ends)
        if doc_line is not None:
            return line, doc_line
    return line, None


def _index(nodes):
    """Match only unique declaration identities; overlapping identities are unknown."""
    counts = Counter(node["healthIdentity"] for node in nodes)
    return ({node["healthIdentity"]: node for node in nodes if counts[node["healthIdentity"]] == 1},
            {identity for identity, count in counts.items() if count > 1})


def _finding(path, identity, line, rule, severity, message):
    """Build a location-specific advisory whose fingerprint ignores line shifts."""
    fingerprint = hashlib.sha256(f"atlas-health/v1\0{identity}\0{rule}".encode()).hexdigest()
    return {"description": message, "check_name": f"atlas-health.{rule}", "fingerprint": fingerprint,
            "severity": severity, "location": {"path": path, "lines": {"begin": line}}}


def _advisories(path, identity, line, documentation_line, documentation_rules, before, after, delta):
    """Emit new method warnings and measurable local regressions only on edited HEAD lines."""
    if after is None:
        return []
    previous = {item["rule"]: item["severity"] for item in before["findings"]} if before else {}
    findings = []
    seen = set()
    for item in after["findings"]:
        rule = item["rule"]
        severity = item["severity"]
        location = documentation_line if rule in documentation_rules else line
        if location is None or rule in seen or severity not in {"warning", "critical"}:
            continue
        old = previous.get(rule)
        if old == "critical" or old == severity:
            continue
        seen.add(rule)
        findings.append(_finding(path, identity, location, rule,
                                 "major" if severity == "critical" else "minor", item["message"]))
    for key, template, location in (("readability", _READABILITY_REGRESSION, line),
                                    ("documentation", _DOC_REGRESSION, documentation_line)):
        difference = delta[key]
        if location is not None and difference is not None and difference <= -_MIN_SCORE_DROP:
            findings.append(_finding(path, identity, location, f"{key}-regression", "minor",
                                     template.format(drop=-difference)))
    return findings


def _compare(change, before_nodes, after_nodes, removed, added):
    """Compare intersecting methods in a file, including documentation-only changes."""
    left, left_ambiguous = _index(before_nodes)
    right, right_ambiguous = _index(after_nodes)
    ambiguous = left_ambiguous | right_ambiguous
    removed_ends = [end for _, end in removed]
    added_ends = [end for _, end in added]
    methods, findings = [], []
    for identity in sorted((left.keys() | right.keys()) - ambiguous):
        old, new = left.get(identity), right.get(identity)
        line, doc_line = _anchors(new, added, added_ends)
        old_line, old_doc_line = _anchors(old, removed, removed_ends)
        doc_change = (old is not None and new is not None
                      and old["documentationHealth"]["score"] != new["documentationHealth"]["score"])
        if not any((line, doc_line, old_line, old_doc_line, doc_change)):
            continue
        before_health = calculate_health(old, None) if old else None
        after_health = calculate_health(new, None) if new else None
        before = _metric_values(old, before_health) if old else None
        after = _metric_values(new, after_health) if new else None
        delta = {}
        for key in _METRIC_KEYS:
            first = before[key] if before else None
            second = after[key] if after else None
            delta[key] = round(second - first, 1) if first is not None and second is not None else None
        kind = "modified" if old and new else "added" if new else "removed"
        methods.append({
            "file": change.after_path or change.before_path, "beforeFile": change.before_path,
            "identity": identity, "kind": kind, "line": line or doc_line,
            "before": before, "after": after, "delta": delta,
            "beforeLine": old["line"] if old else None, "afterLine": new["line"] if new else None,
            "afterReadabilityStatus": (
                "not-applicable" if after_health and after_health["status"] == "not-applicable"
                else after_health["readability"]["status"] if after_health else None
            ),
        })
        documentation_rules = {item["rule"] for item in new["documentationHealth"]["findings"]} if new else set()
        # A removed-only documentation change has no honest HEAD annotation.
        documentation_line = doc_line if doc_line or old_doc_line else line
        findings.extend(_advisories(change.after_path, identity, line, documentation_line,
                                   documentation_rules, before_health, after_health, delta))
    return methods, findings, len(ambiguous)


def analyze(repo, base, head):
    """Run a bounded source-only analysis against exactly two Git commits."""
    base, head = git.commit(repo, base), git.commit(repo, head)
    files, methods, findings = [], [], []
    supported = considered = analyzed = used_bytes = 0
    partial = False
    for change in git.changes(repo, base, head):
        path = change.after_path or change.before_path
        row = {"file": path, "beforeFile": change.before_path, "status": _STATUS_LABELS[change.status],
               "analysis": "skipped", "reason": None}
        files.append(row)
        invalid_path = ((change.status != "A" and change.before_path is None)
                        or (change.status != "D" and change.after_path is None))
        if invalid_path:
            row["reason"] = "unsupported-path"
            partial = True
            continue
        language = language_for(path)
        before_language = language_for(change.before_path) if change.before_path else None
        if change.status == "R" and before_language != language:
            supported += bool(language or before_language)
            row["reason"] = "incompatible-rename-language"
            partial |= bool(language or before_language)
            continue
        if not language:
            row["reason"] = "unsupported-language"
            continue
        supported += 1
        if not change.regular or considered >= _MAX_FILES:
            row["reason"] = "non-regular-file" if not change.regular else "file-count-budget"
            partial = True
            continue
        considered += 1
        if change.before_blob and change.before_blob == change.after_blob:
            analyzed += 1
            row.update(analysis="complete", ambiguousIdentities=0, removedLines=0, addedLines=0)
            continue
        old_size = git.blob_size(repo, change.before_blob)
        new_size = git.blob_size(repo, change.after_blob)
        if max(old_size, new_size) > git.MAX_BLOB_BYTES or used_bytes + old_size + new_size > _MAX_TOTAL_BYTES:
            row["reason"] = "blob-size-budget" if max(old_size, new_size) > git.MAX_BLOB_BYTES else "total-size-budget"
            partial = True
            continue
        used_bytes += old_size + new_size
        before = git.read_blob(repo, change.before_blob)
        after = git.read_blob(repo, change.after_blob)
        removed, added = git.line_ranges(repo, change, before, after)
        old_nodes, old_error = _parse(path, before, change.before_blob)
        new_nodes, new_error = _parse(path, after, change.after_blob)
        if old_error or new_error:
            row["reason"] = old_error or new_error
            partial = True
            continue
        measured, warnings, ambiguous = _compare(change, old_nodes, new_nodes, removed, added)
        analyzed += 1
        row.update(analysis="partial" if ambiguous else "complete", ambiguousIdentities=ambiguous,
                   removedLines=sum(end - start + 1 for start, end in removed),
                   addedLines=sum(end - start + 1 for start, end in added))
        partial |= bool(ambiguous)
        methods.extend(measured)
        findings.extend(warnings)
    methods.sort(key=lambda item: (item["file"], item["line"] or item["beforeLine"] or 0, item["identity"]))
    findings.sort(key=lambda item: (item["location"]["path"], item["location"]["lines"]["begin"], item["check_name"]))
    readability_deltas = [item["delta"]["readability"] for item in methods if item["delta"]["readability"] is not None]
    documentation_deltas = [item["delta"]["documentation"] for item in methods if item["delta"]["documentation"] is not None]
    unknown = sum(item["afterReadabilityStatus"] == "unavailable" for item in methods)
    summary = {
        "changedFiles": len(files), "supportedFiles": supported, "analyzedFiles": analyzed,
        "skippedFiles": len(files) - analyzed, "changedMethods": len(methods),
        "comparedMethods": sum(item["kind"] == "modified" for item in methods),
        "addedMethods": sum(item["kind"] == "added" for item in methods),
        "removedMethods": sum(item["kind"] == "removed" for item in methods),
        "readabilityCompared": len(readability_deltas),
        "readabilityRegressions": sum(delta <= -_MIN_SCORE_DROP for delta in readability_deltas),
        "documentationCompared": len(documentation_deltas),
        "documentationRegressions": sum(delta <= -_MIN_SCORE_DROP for delta in documentation_deltas),
        "notApplicableMethods": sum(item["afterReadabilityStatus"] == "not-applicable" for item in methods),
        "unknownMethods": unknown, "findingCount": len(findings),
    }
    return {"schemaVersion": "atlas-ci-health-v1", "base": base, "head": head,
            "analyzerVersion": ANALYZER_VERSION, "policyVersion": policy()["version"],
            "readabilityPolicyVersion": readability_policy()["version"],
            "mode": "advisory", "status": "partial" if partial or unknown else "complete",
            "graphCoverage": "unavailable", "summary": summary, "files": files,
            "methods": methods, "findings": findings}


def _markdown(report):
    """Render aggregate values only, so untrusted project names never reach markup."""
    counts = report["summary"]
    return (
        "## Atlas health · advisory\n\n"
        f"Compared `{report['base'][:12]}` → `{report['head'][:12]}`. "
        f"Source analysis coverage: **{report['status']}**.\n\n"
        f"- Files analyzed: {counts['analyzedFiles']} / {counts['changedFiles']} changed "
        f"({counts['supportedFiles']} supported).\n"
        f"- Edited methods: {counts['changedMethods']}, paired before and after: {counts['comparedMethods']}.\n"
        f"- Readability comparisons: {counts['readabilityCompared']}; "
        f"regressions of at least {_MIN_SCORE_DROP} points: {counts['readabilityRegressions']}.\n"
        f"- Documentation comparisons: {counts['documentationCompared']}; "
        f"regressions of at least {_MIN_SCORE_DROP} points: {counts['documentationRegressions']}.\n"
        f"- Unknown local readability on HEAD: {counts['unknownMethods']}. "
        f"Inline findings: {counts['findingCount']}.\n"
        "- Graph-dependent H/O/T: unavailable in this source-only job.\n"
    )


def _write(output, report):
    """Write the platform-independent result, GitLab findings and human summary."""
    if output.is_symlink():
        raise ValueError("Output directory must not be a symbolic link")
    output.mkdir(parents=True, exist_ok=True)
    payloads = (
        ("atlas-health.json", json.dumps(report, indent=2) + "\n"),
        ("gl-code-quality-report.json", json.dumps(report["findings"], indent=2) + "\n"),
        ("atlas-health.md", _markdown(report)),
    )
    for name, _ in payloads:
        path = output / name
        if path.is_symlink():
            raise ValueError("Output files must not be symbolic links")
    with TemporaryDirectory(prefix=".atlas-health-", dir=output) as staging:
        staged = Path(staging)
        for name, payload in payloads:
            (staged / name).write_text(payload, encoding="utf-8")
        for name, _ in payloads:
            os.replace(staged / name, output / name)


def main(argv=None):
    """Return zero for advisory results and two for an input or execution error."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = analyze(args.repo, args.base, args.head)
        _write(args.output, report)
    except git.GitInputError as error:
        print(f"atlas-health: {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("atlas-health: analysis or report output failed", file=sys.stderr)
        return 2
    print(f"atlas-health: {report['status']}, {report['summary']['findingCount']} advisory findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
