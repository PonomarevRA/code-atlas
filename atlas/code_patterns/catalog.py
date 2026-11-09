"""Immutable offline pattern lookup with exact revision checks and bounded pages."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .schema import (IDENTIFIER, VERSION, SCHEMA_VERSION, MAX_RECORD_BYTES, MAX_RECORDS,
                     MAX_CATALOG_BYTES, STATUSES, PatternCatalogError, require, validate_record)


def canonical_bytes(value) -> bytes:
    """
    Serialize stable UTF-8 content independently of source indentation/key order.
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def content_hash(value) -> str:
    """
    Return a SHA-256 identity over validated record content or catalogue tuples.
    """
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _request_text(value, name: str, maximum: int = 500) -> str:
    """
    Validate a caller string without silently converting null, booleans or objects.
    """
    if not isinstance(value, str) or len(value) > maximum:
        raise PatternCatalogError("invalid_request", f"Invalid {name}")
    return value


class PatternCatalog:
    """Load an installed catalogue once; returned objects cannot mutate its contents.

    `root` is an application-owned resource directory, never a request-supplied path.
    Files changed after construction become visible only to a newly loaded instance.
    """

    def __init__(self, root: Path | None = None):
        self._records = self._load(root or Path(__file__).resolve().parents[2] / "resources" / "code-patterns")
        identities = [(key, item["version"], item["contentHash"]) for key, item in self._records.items()]
        self.catalog_version = content_hash(identities)

    @staticmethod
    def _load(root: Path) -> dict:
        """
        Read ordinary bounded JSON files and reject duplicates or dangling links.
        """
        records = {}
        total_bytes = 0
        try:
            require(root.is_dir() and not root.is_symlink(), "Pattern resource directory is unavailable")
            files = sorted(root.glob("*.json"))
            require(0 < len(files) <= MAX_RECORDS, "Pattern catalogue size is invalid")
            for path in files:
                require(path.is_file() and not path.is_symlink(), "Pattern resource must be a regular file")
                size = path.stat().st_size
                require(size <= MAX_RECORD_BYTES, "Pattern record exceeds byte limit")
                total_bytes += size
                require(total_bytes <= MAX_CATALOG_BYTES, "Pattern catalogue exceeds byte limit")
                with path.open("rb") as stream:
                    raw = stream.read(MAX_RECORD_BYTES + 1)
                require(len(raw) <= MAX_RECORD_BYTES, "Pattern record exceeds byte limit")
                record = json.loads(raw)
                validate_record(record)
                identifier = record["id"]
                require(path.stem == identifier, "Pattern filename differs from identifier")
                require(identifier not in records, "Duplicate pattern identifier")
                record["contentHash"] = content_hash(record)
                record["wikiPage"] = "Pattern-" + identifier
                records[identifier] = record
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise PatternCatalogError("invalid_catalog", "Pattern resources cannot be read", 500) from None
        for record in records.values():
            require(all(reference in records for reference in record["related"]), "Unknown related pattern")
        return records

    def _check_catalog(self, expected: str | None) -> None:
        """
        Fail explicitly if a continued lookup belongs to another catalogue edition.
        """
        if expected is None:
            return
        _request_text(expected, "catalog_version", 64)
        if expected != self.catalog_version:
            raise PatternCatalogError("catalog_changed", "Pattern catalogue changed; restart lookup", 409)

    @staticmethod
    def _summary(record: dict) -> dict:
        """
        Expose bounded discovery facts; detailed contracts require exact-card lookup.
        """
        keys = ("id", "version", "contentHash", "title", "category", "status", "summary", "wikiPage")
        result = {key: record[key] for key in keys}
        result["languages"] = list(record["applicability"]["languages"])
        return result

    def search(self, query: str = "", category: str = "", language: str = "", status: str = "",
               offset: int = 0, limit: int = 8, catalog_version: str | None = None) -> dict:
        """Find literal words in cards and return a stable, version-bound result page.

        This lexical search suggests candidates, not semantic applicability or quality.
        Pass `catalog_version` on continuation; mismatches fail instead of mixing editions.
        """
        self._check_catalog(catalog_version)
        query = _request_text(query, "query")
        category = _request_text(category, "category", 64)
        language = _request_text(language, "language", 64)
        status = _request_text(status, "status", 32)
        if status and status not in STATUSES:
            raise PatternCatalogError("invalid_request", "Invalid status filter")
        if type(offset) is not int or not 0 <= offset <= MAX_RECORDS:
            raise PatternCatalogError("invalid_request", "offset must be an integer between 0 and 256")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise PatternCatalogError("invalid_request", "limit must be an integer between 1 and 20")
        words = re.findall(r"[\w-]+", query.casefold())
        matches = []
        for record in self._records.values():
            if category and record["category"] != category:
                continue
            if language and language not in record["applicability"]["languages"]:
                continue
            if status and record["status"] != status:
                continue
            haystack = " ".join([record["id"], record["title"], record["summary"], record["problem"],
                                 *record["keywords"], *record["whenToUse"]]).casefold()
            if words and not all(word in haystack for word in words):
                continue
            matches.append(record)
        page = matches[offset:offset + limit]
        end = offset + len(page)
        return {"schemaVersion": SCHEMA_VERSION, "catalogVersion": self.catalog_version,
                "items": [self._summary(record) for record in page], "total": len(matches),
                "offset": offset, "nextOffset": end if end < len(matches) else None,
                "truncated": end < len(matches), "searchKind": "literal-words",
                "interpretation": "Candidates only; inspect applicability and counterexample before use."}

    def get(self, pattern_id: str, version: str | None = None, content_hash: str | None = None,
            catalog_version: str | None = None) -> dict:
        """
        Read one exact installed card; unavailable versions never fall back to current.
        """
        self._check_catalog(catalog_version)
        identifier = _request_text(pattern_id, "pattern_id", 64)
        if not IDENTIFIER.fullmatch(identifier):
            raise PatternCatalogError("invalid_request", "Invalid pattern identifier")
        if version is not None:
            _request_text(version, "version", 32)
            if not VERSION.fullmatch(version):
                raise PatternCatalogError("invalid_request", "Invalid pattern version")
        if content_hash is not None:
            _request_text(content_hash, "content_hash", 64)
            if not re.fullmatch(r"[a-f0-9]{64}", content_hash):
                raise PatternCatalogError("invalid_request", "Invalid content hash")
        record = self._records.get(identifier)
        if record is None:
            raise PatternCatalogError("pattern_not_found", "Pattern is not installed", 404)
        if version is not None and version != record["version"]:
            raise PatternCatalogError("version_not_found", "Pattern version is not installed", 404)
        if content_hash is not None and content_hash != record["contentHash"]:
            raise PatternCatalogError("content_changed", "Pattern content changed; read the current card", 409)
        return {"schemaVersion": SCHEMA_VERSION, "catalogVersion": self.catalog_version,
                "pattern": deepcopy(record), "consumerQualification": {
                    "status": "not-assessed", "reason": "Reading a pattern does not qualify a project integration."}}
