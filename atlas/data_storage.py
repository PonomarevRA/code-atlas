"""Пакетная запись и индекс ключей; миллионы записей не становятся узлами графа."""

from decimal import Decimal
from hashlib import sha256
import json
import sqlite3

from .data_models import MAX_RECORD_BYTES, require


class CollectionReference:
    """Внутренняя ссылка отличается от любого объекта пользовательского JSON."""

    def __init__(self, collection):
        """Сохраняет ID отдельной коллекции без резервирования пользовательских ключей."""
        self.collection = collection


def json_value(value):
    """Сериализует точные Decimal и внутренние ссылки, сохраняя исходные объекты."""
    if isinstance(value, CollectionReference):
        return {"$atlasCollection": value.collection}
    return str(value)


def encode(value) -> str:
    """Decimal хранится точной строкой; тип фиксируется отдельно в профиле/индексе."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=json_value, allow_nan=False)


def identity(*parts) -> str:
    """Детерминированный локальный ID без использования входных имён в пути хранения."""
    return sha256(encode(parts).encode()).hexdigest()[:24]


def typed(value) -> tuple[str, str]:
    """Не смешивает null, строки, bool и числа; точность ключа не зависит от float."""
    if value is None:
        return "null", ""
    if isinstance(value, bool):
        return "boolean", "true" if value else "false"
    if isinstance(value, (int, Decimal)):
        number = Decimal(value)
        sign, digits, exponent = number.as_tuple()
        digits = list(digits)
        while len(digits) > 1 and digits[-1] == 0:
            digits.pop()
            exponent += 1
        if not any(digits):
            return "number", "0"
        return "number", f"{sign}:{''.join(map(str, digits))}:{exponent}"
    return "string", str(value)


def pointer_token(value: str) -> str:
    """Экранирует токен JSON Pointer; точки в имени поля остаются буквальными."""
    return value.replace("~", "~0").replace("/", "~1")


def scalars(value: dict, prefix=""):
    """Разворачивает только вложенные объекты; массивы живут в отдельных коллекциях."""
    for name, item in value.items():
        field = prefix + "/" + pointer_token(name)
        if isinstance(item, CollectionReference):
            continue
        if isinstance(item, dict):
            yield from scalars(item, field)
        elif not isinstance(item, list):
            yield field, item


def references(value: dict, prefix=""):
    """Отмечает реальные ссылки отдельно от похожих пользовательских JSON-ключей."""
    for name, item in value.items():
        field = prefix + "/" + pointer_token(name)
        if isinstance(item, CollectionReference):
            yield field, item.collection
        elif isinstance(item, dict):
            yield from references(item, field)


class RecordWriter:
    """Владеет только staging SQLite и ограниченными метаданными схемы."""

    def __init__(self, path, map_id: str, cancelled, progress, index_fields: list[str]):
        """Создаёт индекс; транзакции публикуются лишь внешним владельцем снимка."""
        self.connection = sqlite3.connect(path)
        self.connection.executescript("""
            PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-32768;
            CREATE TABLE records(id TEXT PRIMARY KEY, collection TEXT, ordinal INTEGER, payload TEXT, provenance TEXT, parent TEXT);
            CREATE INDEX record_page ON records(collection,ordinal);
            CREATE INDEX record_parent ON records(parent);
            CREATE TABLE cells(record TEXT, collection TEXT, field TEXT, type TEXT, value TEXT, raw TEXT);
            CREATE INDEX cell_key ON cells(collection,field,type,value,record);
            CREATE INDEX cell_raw ON cells(collection,field,raw,record);
            CREATE INDEX cell_record ON cells(record,field);
            CREATE TABLE scenarios(id TEXT PRIMARY KEY,payload TEXT,name TEXT,tags TEXT);
        """)
        self.map_id, self.cancelled, self.progress = map_id, cancelled, progress
        self.index_fields = index_fields
        self.collections, self.fields, self.indexed = {}, {}, set()
        self.count = 0
        self.page_size = self.connection.execute("PRAGMA page_size").fetchone()[0]

    def collection(self, file: dict, pattern, label: str, kind="collection", owner=None) -> str:
        """Регистрирует компактный узел коллекции с координатами, без её записей."""
        cid = "data:" + self.map_id + ":collection:" + identity(file["path"], pattern)
        require(len(label.encode()) <= 8192 and len(encode(pattern).encode()) <= 8192,
                "SCHEMA_LIMIT", "Путь коллекции превышает 8 KiB; сократите вложенность или имена ключей.")
        if cid not in self.collections:
            require(len(self.collections) < 2000, "SCHEMA_LIMIT", "Слишком много коллекций; разделите карту.")
            self.collections[cid] = {"id": cid, "label": label, "file": file["path"], "kind": kind,
                                     "count": 0, "fields": [], "owner": owner, "sourceHash": file["sha256"]}
        return cid

    def reserve(self, collection: str) -> tuple[str, int]:
        """Назначает ID до вложенных массивов, чтобы дочерние записи знали владельца."""
        item = self.collections[collection]
        item["count"] += 1
        ordinal = item["count"]
        return "data:" + self.map_id + ":record:" + identity(collection, item["sourceHash"], ordinal), ordinal

    def add(self, collection: str, values: dict, source: dict, parent=None, reserved=None) -> str:
        """Сохраняет запись и ограниченный набор scalar-ключей с явным типом."""
        require(not self.cancelled(), "CANCELLED", "Импорт отменён.", 409)
        record_id, ordinal = reserved or self.reserve(collection)
        payload = encode(values)
        require(len(payload.encode()) <= MAX_RECORD_BYTES, "RECORD_LIMIT", "Одна запись превышает 1 MiB; разделите данные.")
        scalar_values = list(scalars(values))
        require(len(scalar_values) <= 1000, "FIELD_LIMIT", "Одна запись содержит больше 1000 полей.")
        types = {name: typed(value)[0] for name, value in scalar_values}
        source = {**source, "valueTypes": types, "collectionReferences": dict(references(values))}
        self.connection.execute("INSERT INTO records VALUES(?,?,?,?,?,?)",
                                (record_id, collection, ordinal, payload, encode(source), parent))
        for name, value in scalar_values:
            require(len(name) <= 512, "FIELD_LIMIT", "Путь поля превышает 512 символов.")
            kind, key = typed(value)
            token = (collection, name)
            if token not in self.fields:
                require(len(self.fields) < 5000, "SCHEMA_LIMIT", "Схема превышает 5000 полей; разделите карту.")
                chosen = (not self.index_fields or name in self.index_fields) and len(self.indexed) < 64
                if chosen:
                    self.indexed.add(token)
                field = {"name": name, "types": [], "present": 0, "nulls": 0, "indexed": chosen}
                self.fields[token] = field
                self.collections[collection]["fields"].append(field)
            field = self.fields[token]
            if kind not in field["types"]:
                field["types"].append(kind)
            field["present"] += 1
            field["nulls"] += value is None
            if field["indexed"]:
                self.connection.execute("INSERT INTO cells VALUES(?,?,?,?,?,?)", (record_id, collection, name, kind, key, str(value)))
        self.count += 1
        if self.count % 1000 == 0:
            self.connection.commit()
            self.progress(records=self.count, phase="parse")
            require(self.connection.execute("PRAGMA page_count").fetchone()[0] * self.page_size <= 6_000_000_000,
                    "DISK_LIMIT", "Индекс превысил квоту 6 ГБ; уменьшите число полей или вход.")
        return record_id

    def scenario(self, value: dict) -> None:
        """Сохраняет один шаблон Gherkin; варианты Examples не материализуются."""
        self.connection.execute("INSERT INTO scenarios VALUES(?,?,?,?)",
                                (value["id"], encode(value), value["name"], encode(value["tags"])))

    def close(self, commit=True) -> None:
        """Успешную запись проверяет/фиксирует; при отказе только освобождает БД."""
        try:
            if commit:
                require(self.connection.execute("PRAGMA page_count").fetchone()[0] * self.page_size <= 6_000_000_000,
                        "DISK_LIMIT", "Индекс превысил квоту 6 ГБ.")
                self.connection.commit()
        finally:
            self.connection.close()
