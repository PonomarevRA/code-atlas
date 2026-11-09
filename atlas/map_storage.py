"""Checked paths for managed maps; reads never create or migrate storage."""

from pathlib import Path
import re


def identifier(value: str, *, snapshot: bool = False) -> str:
    """Accept one portable ID component, with stricter UUID syntax for snapshots/views."""
    pattern = r"[a-f0-9]{32}" if snapshot else r"[A-Za-z0-9_-]{1,128}"
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise ValueError("Invalid map storage identifier")
    return value


def checked_path(root: Path, *parts: str, directory: bool = True) -> Path:
    """Reject links, escapes and conflicting existing objects without changing them.

    The explicit data root is trusted; every managed component below it is checked.
    Missing components are valid for callers that subsequently create new storage.
    """
    root = Path(root).resolve()
    path = root.joinpath(*parts)
    try:
        relative = path.relative_to(root)
        current = root
        for index, part in enumerate(relative.parts):
            if part in {"", ".", ".."}:
                raise ValueError("Invalid map storage component")
            current = current / part
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                raise ValueError("Map storage must not traverse symbolic links or junctions")
            if current.exists():
                expected_directory = directory or index < len(relative.parts) - 1
                if expected_directory and not current.is_dir():
                    raise ValueError("Map storage directory conflicts with an existing file")
                if not expected_directory and not current.is_file():
                    raise ValueError("Map storage file conflicts with an existing object")
        if not path.resolve().is_relative_to(root):
            raise ValueError("Map storage escapes the data directory")
    except OSError as error:
        raise ValueError("Map storage cannot be inspected") from error
    return path
