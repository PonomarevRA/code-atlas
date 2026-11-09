"""Print bounded GitHub workflow annotations from the Atlas Code Quality report."""

import json
from pathlib import Path, PurePosixPath
import sys


MAX_ANNOTATIONS = 10
MAX_REPORT_BYTES = 2 * 1024 * 1024


def escape(value: str, *, property_value: bool = False) -> str:
    """Encode delimiters before a string enters the GitHub workflow command."""
    encoded = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return encoded.replace(":", "%3A").replace(",", "%2C") if property_value else encoded


def format_annotation(finding: dict) -> str | None:
    """Accept only a valid repository-relative file and a positive line."""
    location = finding.get("location", {})
    path = location.get("path")
    line = location.get("lines", {}).get("begin")
    description = finding.get("description")
    if not isinstance(path, str) or not path or "\\" in path:
        return None
    if PurePosixPath(path).is_absolute() or any(part in ("", ".", "..") for part in path.split("/")):
        return None
    if type(line) is not int or line < 1 or not isinstance(description, str):
        return None
    level = "notice" if finding.get("severity") == "info" else "warning"
    return f"::{level} file={escape(path, property_value=True)},line={line},title=Atlas Health::{escape(description[:500])}"


def main(path: Path, check_diff: Path) -> None:
    """Emit at most ten annotations on paths with the same bytes on both revisions."""
    with path.open("rb") as report:
        data = report.read(MAX_REPORT_BYTES + 1)
    with check_diff.open("rb") as diff:
        changed_paths = diff.read(MAX_REPORT_BYTES + 1)
    if len(data) > MAX_REPORT_BYTES or len(changed_paths) > MAX_REPORT_BYTES:
        print("::notice title=Atlas Health::Inline annotations skipped. Download the full report artifact.")
        return
    differing = set(changed_paths.split(b"\0"))
    emitted = 0
    for finding in json.loads(data):
        annotation = format_annotation(finding)
        if annotation is None or finding["location"]["path"].encode("utf-8") in differing:
            continue
        print(annotation)
        emitted += 1
        if emitted == MAX_ANNOTATIONS:
            break


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
