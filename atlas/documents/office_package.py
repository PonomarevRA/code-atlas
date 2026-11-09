"""Bounded OOXML package inspection shared by the read-only Office providers."""

from __future__ import annotations

import io
import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


def provider_fingerprint(module_path: str, *distributions: str):
    """Include frozen/source adapters and backend versions without parser imports."""
    from .providers import runtime_fingerprint

    return runtime_fingerprint(distributions, module_path, "office_package.py")


class OfficePackage:
    """Validate package expansion and XML before a native reader sees input."""

    def __init__(self, path: Path, options: dict):
        """Open an immutable input; reject unsafe or over-budget package members."""
        if path.stat().st_size > int(options.get("max_source_bytes", 50 * 1024 * 1024)):
            raise ValueError("office_source_byte_limit")
        self.archive = zipfile.ZipFile(path)
        self.roots = {}
        try:
            entries = self.archive.infolist()
            if len(entries) > int(options.get("max_zip_entries", 10000)):
                raise ValueError("office_zip_entry_limit")
            expanded = 0
            names = set()
            for member in entries:
                name = member.filename
                if name in names or name.startswith("/") or ".." in name.split("/") or "\\" in name:
                    raise ValueError("office_unsafe_member")
                names.add(name)
                expanded += member.file_size
                if expanded > int(options.get("max_expanded_bytes", 250 * 1024 * 1024)):
                    raise ValueError("office_expanded_byte_limit")
                ratio = member.file_size / max(1, member.compress_size)
                if ratio > float(options.get("max_compression_ratio", 1000)):
                    raise ValueError("office_compression_ratio_limit")
                if member.flag_bits & 1:
                    raise ValueError("office_encrypted_member")
                if name.endswith((".xml", ".rels")):
                    payload = self.archive.read(member)
                    self.roots[name] = self._parse_xml(payload, options)
        except BaseException:
            self.archive.close()
            raise

    @staticmethod
    def _parse_xml(payload: bytes, options: dict):
        """Reject DTD/entities and enforce XML depth and element budgets."""
        # NUL removal also catches declarations encoded as UTF-16/32.
        declarations = payload.replace(b"\x00", b"").upper()
        if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
            raise ValueError("office_xml_entities_forbidden")
        depth = 0
        count = 0
        maximum = int(options.get("max_xml_elements", 1000000))
        for event, _ in ET.iterparse(io.BytesIO(payload), events=("start", "end")):
            depth += 1 if event == "start" else -1
            if event == "start":
                count += 1
            if depth > int(options.get("max_depth", 128)) or count > maximum:
                raise ValueError("office_xml_budget")
        return ET.fromstring(payload)

    def xml(self, name: str):
        """Return a previously validated package part, or None when absent."""
        return self.roots.get(name)

    def related_parts(self, part: str):
        """Resolve internal relationship targets without following external URLs."""
        folder, name = posixpath.split(part)
        root = self.xml(posixpath.join(folder, "_rels", name + ".rels"))
        result = {}
        if root is not None:
            for relation in root:
                if relation.get("TargetMode") == "External":
                    continue
                target = relation.get("Target", "")
                resolved = posixpath.normpath(posixpath.join(folder, target))
                if target.startswith("/"):
                    resolved = target.lstrip("/")
                if resolved.startswith("../"):
                    raise ValueError("office_unsafe_relationship")
                result[relation.get("Id")] = resolved
        return result

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.archive.close()
