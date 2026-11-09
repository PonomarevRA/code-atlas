"""Bounded UTF-8 JUnit-compatible testcase import with immutable reported evidence."""

from collections import Counter
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import PurePosixPath
import re
from xml.parsers import expat

MAX_REPORT_BYTES = 1024 * 1024
MAX_CASES = 512
MAX_NODES = 10000
MAX_DEPTH = 32
MAX_DETAIL_CHARS = 2048
MAX_ALL_DETAIL_CHARS = 32768


class FeedbackError(ValueError):
    """Carry a bounded public error without echoing XML or caller paths."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def identity(value, name: str, *, optional=False) -> str | None:
    """
    Validate a small identity; declarations remain caller assertions, not proof.
    """
    if optional and value is None:
        return None
    if (not isinstance(value, str) or not 1 <= len(value) <= 128 or not value.strip()
            or any(ord(c) < 32 for c in value)):
        raise FeedbackError("invalid_identity", f"Invalid {name}")
    return value


def safe_path(value: str) -> tuple[str | None, str]:
    """
    Accept only normalized project-relative paths; never resolve or open a file.
    """
    if not value:
        return None, "missing"
    if len(value) > 512 or ":" in value or any(ord(c) < 32 for c in value):
        return None, "invalid"
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() == ".":
        return None, "invalid"
    return path.as_posix(), "relative"


def diagnostic(text: str, limit: int) -> tuple[str, bool]:
    """
    Bound diagnostic text and mask common credentials; this is not secret proof.
    """
    clipped = text[:limit]
    clipped = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[redacted]", clipped)
    clipped = re.sub(r"(?i)((?:password|api[_-]?key|access[_-]?token)\s*[:=]\s*)\S+",
                     r"\1[redacted]", clipped)
    # Masking a short credential can expand it; limits apply to retained output,
    # not merely the untrusted input prefix passed to the masker.
    return clipped[:limit], len(text) > limit or len(clipped) > limit


@dataclass(frozen=True)
class TestCase:
    """One reported outcome; ambiguity affects linkage, not the observed XML status."""
    index: int
    name: str
    class_name: str
    file: str | None
    path_status: str
    line: int | None
    status: str
    details: str
    details_truncated: bool
    ambiguous: bool = False
    outcome_note: str | None = None

    def to_dict(self) -> dict:
        """
        Return an independent JSON-safe view without mutable retained state.
        """
        return {"index": self.index, "name": self.name, "className": self.class_name,
                "file": self.file, "pathStatus": self.path_status, "line": self.line,
                "status": self.status, "details": self.details,
                "detailsTruncated": self.details_truncated, "ambiguous": self.ambiguous,
                "outcomeNote": self.outcome_note}


@dataclass(frozen=True)
class JUnitReport:
    """Immutable imported report; snapshot/build bindings are unverified declarations."""
    report_id: str
    report_hash: str
    snapshot_id: str | None
    build_id: str | None
    cases: tuple[TestCase, ...]

    def summary(self) -> dict:
        """
        Count actual testcase elements; absent results are never inferred as passing.
        """
        counts = Counter(case.status for case in self.cases)
        return {name: counts[name] for name in ("passed", "failure", "error", "skipped", "unknown")}

    def to_dict(self) -> dict:
        """
        Serialize imported evidence without converting its provenance into verification.
        """
        return {"schemaVersion": "atlas-junit-report/v1", "reportId": self.report_id,
                "reportHash": self.report_hash, "snapshotId": self.snapshot_id, "buildId": self.build_id,
                "identityProvenance": "caller-supplied-report-binding", "executionVerified": False,
                "sourceKind": "imported-junit-xml", "summary": self.summary(),
                "resultsPresent": bool(self.cases),
                "cases": [case.to_dict() for case in self.cases],
                "limits": {"maxInputBytes": MAX_REPORT_BYTES, "maxCases": MAX_CASES}}


class _JUnitReader:
    """Own one bounded parse, retaining only testcase identities and failure details."""

    def __init__(self):
        self.depth = self.nodes = self.detail_count = 0
        self.case = None
        self.case_depth = self.detail_depth = None
        self.details = []
        self.statuses = []
        self.cases = []
        self.root = None
        self.truncated = False
        self.case_detail_count = 0
        self.output_detail_count = 0
        self.suppressed_depth = None
        self.elements = []

    def start(self, raw_name, attributes):
        """
        Enforce structural limits before accumulating testcase evidence.
        """
        name = raw_name.rsplit("}", 1)[-1]
        self.depth += 1
        self.nodes += 1
        if self.depth > MAX_DEPTH or self.nodes > MAX_NODES or len(attributes) > 32:
            raise FeedbackError("report_limit", "JUnit structure exceeds limits", 413)
        if self.root is None:
            self.root = name
            if name not in {"testsuites", "testsuite"}:
                raise FeedbackError("unsupported_report", "Expected testsuite or testsuites root")
        if name == "testcase":
            if (not self.elements or self.elements[-1] != "testsuite"
                    or any(parent not in {"testsuites", "testsuite"} for parent in self.elements)):
                raise FeedbackError("unsupported_report", "Testcase must be a direct child of testsuite containers")
        self.elements.append(name)
        if name in {"system-out", "system-err"} and self.suppressed_depth is None:
            self.suppressed_depth = self.depth
        if name == "testcase":
            if self.case is not None or len(self.cases) >= MAX_CASES:
                raise FeedbackError("report_limit", "Too many or nested testcases", 413)
            if any(len(value) > 512 for value in attributes.values()):
                raise FeedbackError("report_limit", "Testcase identity exceeds limits", 413)
            self.case, self.case_depth = attributes, self.depth
            self.details, self.statuses, self.truncated, self.case_detail_count = [], [], False, 0
        elif self.case is not None and self.depth == self.case_depth + 1 and name in {"failure", "error", "skipped"}:
            self.statuses.append(name)
            self.detail_depth = self.depth
            self.characters(attributes.get("message", "") + "\n")

    def characters(self, value):
        """
        Retain only bounded outcome diagnostics, never system-out/system-err logs.
        """
        if self.detail_depth is None or self.suppressed_depth is not None:
            return
        room = max(0, min(MAX_DETAIL_CHARS - self.case_detail_count, MAX_ALL_DETAIL_CHARS - self.detail_count))
        kept = min(len(value), room)
        if kept:
            self.details.append(value[:kept])
        self.detail_count += kept
        self.case_detail_count += kept
        self.truncated |= len(value) > room

    def end(self, raw_name):
        """
        Finish one testcase and preserve conflicting outcome declarations as unknown.
        """
        if self.depth == self.suppressed_depth:
            self.suppressed_depth = None
        if self.case is not None and self.depth == self.case_depth:
            attrs = self.case
            file, path_status = safe_path(attrs.get("file", ""))
            line_text = attrs.get("line", "")
            line = int(line_text) if line_text.isascii() and line_text.isdigit() and len(line_text) <= 9 else None
            if line == 0:
                line = None
            statuses = set(self.statuses)
            status = next(iter(statuses)) if len(statuses) == 1 else "unknown" if statuses else "passed"
            # Extended producer profiles are not silently interpreted as classic
            # JUnit success; e.g. GoogleTest's status=notrun is not a passing run.
            outcome_note = None
            if "status" in attrs or "result" in attrs:
                status = "unknown"
                outcome_note = "unsupported-outcome-attributes"
            remaining_output = max(0, MAX_ALL_DETAIL_CHARS - self.output_detail_count)
            details, clipped = diagnostic("".join(self.details), min(MAX_DETAIL_CHARS, remaining_output))
            self.output_detail_count += len(details)
            self.cases.append(TestCase(len(self.cases), attrs.get("name", ""), attrs.get("classname", ""),
                                       file, path_status, line, status, details, self.truncated or clipped,
                                       outcome_note=outcome_note))
            self.case = self.case_depth = self.detail_depth = None
        elif self.depth == self.detail_depth:
            self.detail_depth = None
        self.depth -= 1
        self.elements.pop()


def parse_junit_report(data: bytes, report_id: str, snapshot_id: str | None = None,
                       build_id: str | None = None) -> JUnitReport:
    """
    Import a bounded UTF-8 JUnit testcase subset; never run tests or read source files.

    DTD/entity declarations and other encodings are rejected. Testcases must be
    direct children of suite containers, not embedded log/property XML. Classic
    absence of outcome children is reported passing; extended status/result
    attributes are unsupported and explicitly unknown. Runtime success is merely
    reported evidence; caller snapshot/build declarations are unverified.
    """
    report_id = identity(report_id, "report_id")
    snapshot_id = identity(snapshot_id, "snapshot_id", optional=True)
    build_id = identity(build_id, "build_id", optional=True)
    if not isinstance(data, bytes):
        raise FeedbackError("invalid_report", "Report data must be bytes")
    if len(data) > MAX_REPORT_BYTES:
        raise FeedbackError("report_limit", "Report exceeds byte limit", 413)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        raise FeedbackError("unsupported_encoding", "Only UTF-8 JUnit reports are supported") from None
    if "\x00" in text:
        raise FeedbackError("unsupported_encoding", "NUL and UTF-16 reports are not supported")
    reader = _JUnitReader()
    parser = expat.ParserCreate(namespace_separator="}")

    def reject(*args):
        """Reject declarations before Expat can expand attacker-supplied entities."""
        raise FeedbackError("unsafe_xml", "DTD and entity declarations are not supported")

    def xml_declaration(version, encoding, standalone):
        """Accept only the explicitly supported encoding profile."""
        if encoding and encoding.lower().replace("-", "") not in {"utf8", "usascii", "ascii"}:
            raise FeedbackError("unsupported_encoding", "Only UTF-8 JUnit reports are supported")

    parser.StartElementHandler, parser.EndElementHandler = reader.start, reader.end
    parser.CharacterDataHandler = reader.characters
    parser.StartDoctypeDeclHandler = parser.EntityDeclHandler = parser.ExternalEntityRefHandler = reject
    parser.XmlDeclHandler = xml_declaration
    try:
        # One bounded input prevents incomplete-token reparsing across tiny chunks.
        parser.Parse(text, True)
    except expat.ExpatError:
        raise FeedbackError("invalid_xml", "Malformed JUnit XML") from None
    names = Counter((case.class_name, case.name) for case in reader.cases)
    cases = tuple(replace(case, ambiguous=not case.name or names[(case.class_name, case.name)] > 1)
                  for case in reader.cases)
    return JUnitReport(report_id, sha256(data).hexdigest(), snapshot_id, build_id, cases)
