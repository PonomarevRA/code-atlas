"""Подготовленные владельцы и пути одного запроса архитектуры, без кэша исходников."""

import os
from pathlib import Path

from .project_scopes import assign_code_projects


def absolute_path(root: Path, filename: str) -> str:
    """Нормализует путь лексически, не читая внешний ProjectReference с диска."""
    return os.path.normpath(str(root / filename.replace("\\", "/")))


class ProjectScopeContext:
    """Переиспользует принадлежность узлов и пути только внутри одного снимка запроса."""

    def __init__(self, root: Path, graph: dict):
        """Готовит владельцев по копиям узлов; файлы исходников здесь не читаются."""
        self.root = root
        self.resolved_root = root.resolve()
        self.nodes = [dict(node) for node in graph.get("nodes", [])]
        self.projects = [project for project in assign_code_projects(self.nodes) if project.get("manifest")]
        self.owners = {node["id"]: node for node in self.nodes}
        self.project_ids = {project["id"] for project in self.projects}
        self.manifests = {absolute_path(root, project["manifest"]): project["id"] for project in self.projects}
        self._evidence_files = {}

    def evidence_file(self, filename: str) -> str:
        """Вычисляет безопасный относительный путь один раз на уникальный исходный файл."""
        if filename not in self._evidence_files:
            try:
                relative = Path(absolute_path(self.root, filename)).relative_to(self.resolved_root).as_posix()
            except ValueError:
                relative = "snapshot"
            self._evidence_files[filename] = relative
        return self._evidence_files[filename]
