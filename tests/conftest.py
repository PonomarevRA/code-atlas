"""Изолированные исходники для проверки индексации без запуска кода проекта."""

from pathlib import Path

import pytest


@pytest.fixture
def source_project(tmp_path: Path) -> Path:
    """Создаёт Python и C# проект с вызовами и маркером запрета исполнения."""
    project = tmp_path / "example project"
    project.mkdir()
    (project / "service.py").write_text(
        '"""Small source fixture; importing it is deliberately forbidden."""\n'
        'raise RuntimeError("Source code must never execute during indexing")\n\n'
        'def normalize(value):\n    """Normalize a number."""\n    return value * 2\n\n'
        'def calculate(value):\n    """Calculate the result."""\n    return normalize(value)\n',
        encoding="utf-8",
    )
    (project / "Calculator.cs").write_text(
        'namespace Example;\n'
        'public class Calculator {\n'
        '    public int Normalize(int value) => value * 2;\n'
        '    public int Calculate(int value) => Normalize(value);\n'
        '}\n',
        encoding="utf-8",
    )
    return project


def source_snapshot(project: Path) -> dict[str, bytes]:
    """Сохраняет дерево и содержимое, чтобы выявить изменения исходного проекта."""
    snapshot = {}
    for path in sorted(project.rglob("*")):
        if path.is_file():
            snapshot[str(path.relative_to(project))] = path.read_bytes()
    return snapshot
