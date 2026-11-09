"""Общая детерминированная раскладка сохранённого SVG/PNG без браузерного layout."""

import math
import textwrap
import unicodedata


def name_hash(value: str) -> int:
    """Повторяет FNV-1a палитры web/colors.js для одинаковых цветов в PNG и SVG."""
    result = 2166136261
    for char in unicodedata.normalize("NFC", str(value)).strip().lower():
        result = ((result ^ ord(char)) * 16777619) & 0xffffffff
    return result


def project_color(project: dict) -> dict:
    """Возвращает CSS-палитру проекта; нераспределённые символы остаются серыми."""
    neutral = project["id"] == "__unassigned__"
    hue = 210 if neutral else name_hash(project["label"]) % 360
    return {"hue": hue, "fill": f"hsl({hue} {8 if neutral else 48}% 94%)",
            "stroke": f"hsl({hue} {9 if neutral else 48}% 43%)", "text": f"hsl({hue} {12 if neutral else 48}% 24%)"}


def node_color(node: dict, projects: dict) -> str:
    """Сохраняет оттенок владельца с небольшой вариацией по исходному имени узла."""
    hashed = name_hash(node.get("label") or node.get("file") or node["id"])
    lightness = 36 + (hashed >> 8) % 12
    if node["codeProjectId"] == "__unassigned__":
        return f"hsl(210 {5 + hashed % 8}% {lightness}%)"
    hue = (projects[node["codeProjectId"]]["color"]["hue"] + hashed % 37 - 18) % 360
    return f"hsl({hue} {48 + (hashed >> 16) % 18}% {lightness}%)"


def _lines(text: str, width: int) -> list[str]:
    """Переносит явные строки без скрытого удаления пробелов/длинных слов в заметке."""
    result = []
    for line in text.splitlines() or [""]:
        result.extend(textwrap.wrap(line, width=width, replace_whitespace=False) or [""])
    return result


def build_layout(nodes: list[dict], projects: list[dict], annotations: list[dict]) -> dict:
    """Пакует проектные блоки и заметки; все координаты задают левый верхний угол."""
    positions, boxes = {}, []
    left, top, row_height = 40, 100, 0
    maximum_right = 40
    for project in projects:
        members = [node for node in nodes if node["codeProjectId"] == project["id"]]
        columns = min(4, len(members))
        width = columns * 260 + 20
        height = math.ceil(len(members) / columns) * 104 + 60
        if left > 40 and left + width > 1800:
            top += row_height + 32
            left, row_height = 40, 0
        boxes.append({"id": project["id"], "label": project["label"], "x": left, "y": top,
                      "width": width, "height": height, "color": project["color"]})
        for index, node in enumerate(members):
            positions[node["id"]] = {"x": left + 20 + index % columns * 260,
                "y": top + 48 + index // columns * 104, "width": 240, "height": 80,
                "displayLines": _lines(str(node.get("label") or node["id"]), 26)[:3],
                "textTruncated": len(_lines(str(node.get("label") or node["id"]), 26)) > 3}
        left += width + 32
        maximum_right = max(maximum_right, left)
        row_height = max(row_height, height)
    bottom = top + row_height + 40
    note_positions = {}
    note_left, note_top = maximum_right + 24, 100
    note_right = maximum_right
    for note in annotations:
        title_lines = _lines(note["title"], 38)
        body_lines = _lines(note["text"], 44)
        clipped_title, clipped_body = title_lines[:3], body_lines[:21]
        height = min(500, 64 + 18 * (len(clipped_title) + len(clipped_body)))
        if note_top + height > 7000:
            note_left += 384
            note_top = 100
        note_positions[note["id"]] = {"x": note_left, "y": note_top, "width": 360, "height": height,
            "titleLines": clipped_title, "displayLines": clipped_body,
            "textTruncated": len(title_lines) > 3 or len(body_lines) > 21}
        note_top += height + 24
        note_right = max(note_right, note_left + 360)
        bottom = max(bottom, note_top + 40)
    width, height = int(max(maximum_right, note_right) + 40), int(bottom)
    if width > 8192 or height > 8192 or width * height > 32_000_000:
        raise ValueError("Выбранный вид превышает допустимый размер изображения; сократите узлы или заметки.")
    return {"width": width, "height": height, "nodes": positions, "projects": boxes, "annotations": note_positions}
