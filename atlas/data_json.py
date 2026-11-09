"""Событийный JSON: массивы хранятся на диске, а не собираются в памяти."""

import ijson

from .data_models import MAX_RECORD_BYTES, require
from .data_storage import CollectionReference, pointer_token


class GuardedJSON:
    """Ограничивает токен и глубину перед передачей очередного блока в ijson."""

    def __init__(self, stream, cancelled):
        """Лексическое состояние сохраняется между блоками, включая escape."""
        self.stream, self.cancelled = stream, cancelled
        self.quoted, self.escaped, self.token, self.depth = False, False, 0, 0
        self.boundary = 0

    def read(self, size=-1):
        """Не позволяет парсеру запросить весь файл или собрать гигантскую строку."""
        require(not self.cancelled(), "CANCELLED", "Импорт отменён.", 409)
        block = self.stream.read(min(size, 65536) if size >= 0 else 65536)
        self.boundary = 0
        for position, byte in enumerate(block):
            if self.quoted:
                self.token += 1
                if self.escaped:
                    self.escaped = False
                elif byte == 92:
                    self.escaped = True
                elif byte == 34:
                    self.quoted, self.token = False, 0
                    self.boundary = position + 1
            elif byte == 34:
                self.quoted, self.token = True, 0
            elif byte in (123, 91):
                self.depth += 1
                self.token = 0
                self.boundary = position + 1
            elif byte in (125, 93):
                self.depth -= 1
                self.token = 0
                self.boundary = position + 1
            elif byte in (32, 9, 10, 13, 44, 58):
                self.token = 0
                self.boundary = position + 1
            else:
                self.token += 1
            if self.token > MAX_RECORD_BYTES:
                require(False, "TOKEN_LIMIT", "JSON-токен превышает 1 MiB.")
            if self.depth > 64:
                require(False, "DEPTH_LIMIT", "Вложенность JSON превышает 64 уровня.")
        return block


def json_events(stream, multiple):
    """Явно закрывает coroutine-парсер даже при лимите или отмене чтения."""
    pending = ijson.sendable_list()
    parser = ijson.basic_parse_coro(pending, multiple_values=multiple, use_float=False)
    closed = False
    buffered = b""
    try:
        while block := stream.read(65536):
            boundary = len(buffered) + stream.boundary if stream.boundary else 0
            buffered += block
            if boundary:
                # Pure-Python ijson удерживает прошлый буфер, если каждый блок
                # заканчивается внутри строки. Завершённые токены ограничивают
                # и его память, и стоимость повторного копирования длинного файла.
                # Завершающий пробел не меняет JSON и даёт Lexer явную границу
                # также после ':'/',' — иначе резервный backend ждёт ещё блок.
                parser.send(buffered[:boundary] + b" ")
                buffered = buffered[boundary:]
                yield from pending
                pending.clear()
        if buffered:
            parser.send(buffered)
            yield from pending
            pending.clear()
        parser.close()
        closed = True
        yield from pending
    finally:
        if not closed:
            try:
                parser.close()
            except Exception:
                # Ошибка незавершённого токена не подменяет исходный предел/отмену.
                pass


def import_json(path, file, writer):
    """Сохраняет каждый элемент массивов с Pointer и ссылкой на родительскую запись."""
    stack = []
    root_number = 0
    multiple = path.suffix.lower() in {".jsonl", ".ndjson"}

    def position():
        """Возвращает фактический Pointer и типизированный шаблон коллекции."""
        nonlocal root_number
        if not stack:
            pointer = f"/{root_number}" if multiple else ""
            root_number += 1
            return pointer, (), None, None
        parent = stack[-1]
        key = parent["key"] if parent["kind"] == "map" else parent["next"]
        pointer = parent["pointer"] + "/" + pointer_token(str(key))
        pattern = parent["pattern"] + ((key if parent["kind"] == "map" else None),)
        return pointer, pattern, parent, key

    def attach(parent, key, value, amount):
        """В объекте остаются scalar-поля/вложенные объекты и ссылки на коллекции."""
        if parent is None:
            return
        if parent["kind"] == "array":
            parent["next"] += 1
        else:
            parent["value"][key] = value
            parent["bytes"] += amount
            require(parent["bytes"] <= MAX_RECORD_BYTES, "RECORD_LIMIT", "Объект JSON превышает 1 MiB без вложенных массивов.")

    with path.open("rb") as stream:
        events = json_events(GuardedJSON(stream, writer.cancelled), multiple)
        for event, value in events:
            if event == "map_key":
                frame = stack[-1]
                require(value not in frame["keys"], "JSON_DUPLICATE", "Повторяющийся ключ JSON.")
                frame["keys"].add(value)
                require(len(frame["keys"]) <= 1000, "FIELD_LIMIT", "Объект JSON содержит больше 1000 полей.")
                frame["key"] = value
                frame["bytes"] += len(value.encode())
            elif event in {"start_map", "start_array"}:
                pointer, pattern, parent, key = position()
                kind = "map" if event == "start_map" else "array"
                cid, reserved = None, None
                owner = parent["record"] if parent else None
                if kind == "array":
                    cid = writer.collection(file, pattern, file["path"] + ":" + (pointer or "/"))
                    if parent and parent["kind"] == "array":
                        reserved = writer.reserve(parent["collection"])
                elif parent is None or parent["kind"] == "array":
                    cid = parent["collection"] if parent else writer.collection(file, "root", file["path"] + ":/")
                    reserved = writer.reserve(cid)
                stack.append({"kind": kind, "pointer": pointer, "pattern": pattern, "parent": parent, "keyInParent": key,
                              "collection": cid, "reserved": reserved, "record": reserved[0] if reserved else owner,
                              "owner": owner, "key": None, "keys": set(), "next": 0, "value": {}, "bytes": 0})
            elif event in {"end_map", "end_array"}:
                frame = stack.pop()
                parent, key = frame["parent"], frame["keyInParent"]
                if frame["kind"] == "array":
                    reference = CollectionReference(frame["collection"])
                    if frame["reserved"]:
                        writer.add(parent["collection"], {"value": reference}, {"file": file["path"], "sourceHash": file["sha256"],
                                   "pointer": frame["pointer"], "sourceKind": "snapshot-captured-source"}, frame["owner"], frame["reserved"])
                    attach(parent, key, reference, 100)
                elif frame["reserved"]:
                    writer.add(frame["collection"], frame["value"], {"file": file["path"], "sourceHash": file["sha256"],
                               "pointer": frame["pointer"], "sourceKind": "snapshot-captured-source"}, frame["owner"], frame["reserved"])
                    attach(parent, key, None, 0)
                else:
                    attach(parent, key, frame["value"], frame["bytes"])
            else:
                pointer, pattern, parent, key = position()
                if parent is None or parent["kind"] == "array":
                    cid = parent["collection"] if parent else writer.collection(file, "root", file["path"] + ":/")
                    writer.add(cid, {"value": value}, {"file": file["path"], "sourceHash": file["sha256"],
                               "pointer": pointer, "sourceKind": "snapshot-captured-source"}, parent["record"] if parent else None)
                    attach(parent, key, None, 0)
                else:
                    attach(parent, key, value, len(str(value).encode()) + 16)
    require(root_number > 0 and not stack, "JSON_EMPTY", "Пустой или незавершённый JSON.")
