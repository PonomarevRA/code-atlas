"""Ограниченный CSV-reader с явным диалектом и координатами логических строк."""

import csv
import codecs

from .data_models import MAX_RECORD_BYTES, require


class LimitedLines:
    """Ограничивает физическую строку и весь аргумент csv.reader до материализации."""

    def __init__(self, stream, encoding):
        """Начинает учёт байт очередной логической записи."""
        self.stream, self.encoding, self.bytes = stream, encoding, 0
        self.first = True

    def __iter__(self):
        """Передаёт строки стандартному reader без загрузки файла целиком."""
        return self

    def __next__(self):
        """CSV-reader может запросить несколько строк для одного quoted-поля."""
        value = self.stream.readline(MAX_RECORD_BYTES + 1)
        if not value:
            raise StopIteration
        self.bytes += len(value)
        require(self.bytes <= MAX_RECORD_BYTES, "RECORD_LIMIT", "CSV-запись превышает 1 MiB.")
        text = value.decode(self.encoding if self.first else self.encoding.replace("-sig", ""))
        self.first = False
        return text


def import_csv(path, file, writer, options):
    """Сохраняет все колонки как строки; ширина и дубли заголовков проверяются."""
    with path.open("rb") as stream:
        sample = codecs.getincrementaldecoder(options["encoding"])(errors="strict").decode(stream.read(8192), final=False)
        stream.seek(0)
        delimiter = options["delimiter"]
        if delimiter == "auto":
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
            except csv.Error:
                delimiter = ","
        lines = LimitedLines(stream, options["encoding"])
        reader = csv.reader(lines, delimiter=delimiter, strict=True)
        previous_limit = csv.field_size_limit(MAX_RECORD_BYTES)
        try:
            headers = next(reader, None)
            require(headers is not None and 0 < len(headers) <= 1000, "CSV_HEADERS", "CSV должен содержать строку заголовков (до 1000 колонок).")
            require(all(headers) and len(headers) == len(set(headers)), "CSV_HEADERS", "Пустые или повторяющиеся CSV-заголовки; исправьте источник.")
            cid = writer.collection(file, "csv", file["path"])
            writer.collections[cid]["dialect"] = {"delimiter": delimiter, "encoding": options["encoding"]}
            lines.bytes = 0
            previous_line = reader.line_num
            for row in reader:
                if not row:
                    lines.bytes = 0
                    previous_line = reader.line_num
                    continue
                require(len(row) == len(headers), "CSV_WIDTH", f"CSV {file['path']}, строка {reader.line_num}: неверное число колонок.")
                writer.add(cid, dict(zip(headers, row)), {"file": file["path"], "sourceHash": file["sha256"],
                           "line": previous_line + 1, "endLine": reader.line_num, "sourceKind": "snapshot-captured-source"})
                previous_line, lines.bytes = reader.line_num, 0
        finally:
            csv.field_size_limit(previous_limit)
