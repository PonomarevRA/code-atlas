"""Validate bounded catalogue records before they reach either reader."""

import ast
import re
from datetime import date
from urllib.parse import urlsplit

SCHEMA_VERSION = "atlas-code-pattern/v1"
IDENTIFIER = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
MAX_RECORD_BYTES = 16000
MAX_RECORDS = 256
MAX_CATALOG_BYTES = 2 * 1024 * 1024
STATUSES = {"draft", "reviewed", "qualified", "deprecated"}
TEXT_FIELDS = ("id", "version", "title", "category", "summary", "problem", "moduleBoundary",
               "publicContract", "stateLifetime")
LIST_FIELDS = ("whenToUse", "whenNotToUse", "responsibilities", "dependencies", "errors",
               "migrationSteps", "limits", "checks", "alternatives", "codingGuidance", "keywords", "related")


class PatternCatalogError(ValueError):
    """
    Expose a fixed public error code/status without leaking filesystem paths.
    """

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message


def require(condition: bool, message: str) -> None:
    """
    Reject an invalid installed record with a bounded, path-free diagnostic.
    """
    if not condition:
        raise PatternCatalogError("invalid_catalog", message, 500)


def text(value, maximum: int = 1200) -> bool:
    """
    Recognize nonempty bounded text without executable terminal control characters.
    """
    return (isinstance(value, str) and 0 < len(value) <= maximum
            and all(ord(char) >= 32 or char in "\n\t" for char in value))


def strings(value, *, allow_empty: bool = False) -> bool:
    """
    Bound explanatory lists while allowing an empty list only when specified.
    """
    return (isinstance(value, list) and (allow_empty or bool(value)) and len(value) <= 12
            and all(text(item) for item in value))


def validate_record(record: dict) -> None:
    """Check a versioned pattern, examples and source attribution; never execute code.

    The caller separately checks serialized byte limits and cross-record references.
    A card's declared review status never proves consumer integration qualification.
    """
    require(isinstance(record, dict), "Pattern record must be an object")
    require(record.get("schemaVersion") == SCHEMA_VERSION, "Unsupported pattern schema")
    for field in TEXT_FIELDS:
        require(text(record.get(field)), f"Missing or invalid pattern field: {field}")
    require(bool(IDENTIFIER.fullmatch(record["id"])), "Invalid pattern identifier")
    require(len(record["version"]) <= 32 and bool(VERSION.fullmatch(record["version"])),
            "Invalid pattern version")
    require(bool(IDENTIFIER.fullmatch(record["category"])), "Invalid pattern category")
    require(len(record["title"]) <= 120 and len(record["summary"]) <= 300,
            "Pattern title or summary is too long")
    require(isinstance(record.get("status"), str) and record["status"] in STATUSES,
            "Invalid pattern lifecycle status")
    for field in LIST_FIELDS:
        require(strings(record.get(field), allow_empty=field == "related"),
                f"Missing or invalid pattern list: {field}")
    for field in ("example", "counterexample"):
        value = record.get(field)
        require(isinstance(value, dict), f"Missing pattern {field}")
        require(text(value.get("language"), 32) and text(value.get("code"), 2400)
                and text(value.get("explanation")), f"Invalid pattern {field}")
        require(value["language"] == "python", "Unsupported example language in schema v1")
        try:
            # Parsing checks syntax only. In particular, counterexamples are never run.
            ast.parse(value["code"], feature_version=(3, 12))
            ast.parse(value["code"], feature_version=(3, 13))
        except SyntaxError:
            raise PatternCatalogError("invalid_catalog", f"Invalid Python example syntax: {field}", 500) from None
    applicability = record.get("applicability")
    require(isinstance(applicability, dict), "Missing applicability")
    require(strings(applicability.get("languages")) and strings(applicability.get("versions"))
            and text(applicability.get("scope")), "Invalid applicability")
    require(all(IDENTIFIER.fullmatch(language) for language in applicability["languages"]),
            "Invalid applicability language")
    try:
        reviewed = date.fromisoformat(record.get("reviewedAt", ""))
    except (TypeError, ValueError):
        raise PatternCatalogError("invalid_catalog", "Invalid pattern source review date", 500) from None
    require(record["reviewedAt"] == reviewed.isoformat(), "Invalid pattern source review date")
    sources = record.get("sources")
    require(isinstance(sources, list) and 1 <= len(sources) <= 6, "Missing pattern sources")
    for source in sources:
        require(isinstance(source, dict) and text(source.get("title"), 200)
                and text(source.get("url"), 500) and text(source.get("supports")), "Invalid source")
        try:
            parsed = urlsplit(source["url"])
        except ValueError:
            raise PatternCatalogError("invalid_catalog", "Invalid source URL", 500) from None
        require(parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username
                and not parsed.password, "Source must be a credential-free HTTPS URL")
    expected = set(TEXT_FIELDS + LIST_FIELDS) | {
        "schemaVersion", "status", "example", "counterexample", "applicability", "reviewedAt", "sources"}
    require(set(record) == expected, "Unknown or missing pattern fields")
