"""Ограниченный сбор файлов для слоя входов; безопасное чтение остаётся общим."""

from collections import defaultdict
from pathlib import Path
import time

from .source_viewer import SourceError, read_node_source


def read_sources(project_path: Path, nodes: list[dict], projects: list[dict], snapshot_id: str,
                 *, max_files: int, max_total_bytes: int, max_scan_seconds: float) -> tuple[list, dict]:
    """Читает индексированные C# и соседние appsettings с общими лимитами обхода."""
    candidates = {}
    by_file = defaultdict(list)
    for node in nodes:
        filename = str(node.get("file") or node.get("source_file") or "").replace("\\", "/")
        if not filename or node.get("kind") == "external":
            continue
        by_file[filename].append(node)
        path = Path(filename)
        if path.suffix == ".cs" and not path.name.endswith((".Designer.cs", ".g.cs")) and "Migrations" not in path.parts:
            candidates[filename] = node
        if path.name.startswith("appsettings") and path.suffix == ".json":
            candidates[filename] = node
    # Только прямые файлы известных каталогов проектов; рекурсивного обхода нет.
    for project in projects:
        if not project.get("manifest"):
            continue
        try:
            directory = (project_path / project["path"]).resolve(strict=True)
            directory.relative_to(project_path.resolve())
            for index, path in enumerate(directory.iterdir()):
                if index >= 500:
                    break
                if path.name.startswith("appsettings") and path.suffix == ".json":
                    filename = path.relative_to(project_path.resolve()).as_posix()
                    candidates.setdefault(filename, {"file": filename, "codeProjectId": project["id"]})
        except (OSError, ValueError, RuntimeError):
            continue
    started = time.monotonic()
    sources = []
    coverage = {"candidateFiles": len(candidates), "readFiles": 0, "skippedFiles": 0, "limited": False}
    total_bytes = 0
    for index, (filename, node) in enumerate(sorted(candidates.items())):
        if index >= max_files or total_bytes >= max_total_bytes or time.monotonic() - started > max_scan_seconds:
            coverage["limited"] = True
            coverage["skippedFiles"] += len(candidates) - index
            break
        try:
            source = read_node_source(project_path, node, snapshot_id)
        except SourceError:
            coverage["skippedFiles"] += 1
            continue
        size = len(source["text"].encode("utf-8"))
        if total_bytes + size > max_total_bytes:
            coverage["limited"] = True
            coverage["skippedFiles"] += 1
            continue
        total_bytes += size
        source["codeProjectId"] = node.get("codeProjectId", "__unassigned__")
        source["nodes"] = by_file.get(filename, [])
        sources.append(source)
        coverage["readFiles"] += 1
    return sources, coverage

