"""Отбор .NET API по официальному FrameworksIndex, а не номеру сборки."""
from pathlib import PurePosixPath
import re
import xml.etree.ElementTree as ET

from .reference_documents import MAX_FILE_BYTES, ReferenceError


def framework_members(package, framework: str) -> set[str] | None:
    """Читает ограниченный whitelist DocId из ZIP; не разрешает XML entities или symlinks."""
    if not framework:
        return None
    ending = "/xml/FrameworksIndex/" + framework + ".xml"
    matches = [entry for entry in package.infolist() if entry.filename.endswith(ending)]
    if len(matches) != 1:
        raise ReferenceError("Expected one official FrameworksIndex for " + framework)
    entry = matches[0]
    path = PurePosixPath(entry.filename)
    if path.is_absolute() or ".." in path.parts or "\\" in entry.filename or entry.external_attr >> 16 & 0o170000 == 0o120000:
        raise ReferenceError("Unsafe framework index entry")
    if entry.file_size > MAX_FILE_BYTES:
        raise ReferenceError("Framework index exceeds 8 MiB")
    try:
        raw = package.read(entry).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ReferenceError("Expected UTF-8 framework index") from None
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", raw, re.I):
        raise ReferenceError("Framework index entities are not supported")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ReferenceError("Invalid framework index") from None
    if root.tag != "Framework" or root.get("Name") != framework:
        raise ReferenceError("Framework index does not match selected framework")
    members = {element.get("Id") for element in root.iter() if element.tag in {"Type", "Member"} and element.get("Id")}
    if not members:
        raise ReferenceError("Empty framework index")
    return members
