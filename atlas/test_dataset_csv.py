"""A bounded JUnit CsvSource literal subset with explicit empty/null distinctions."""
from .java_annotations import literal_string, text
from .test_dataset_values import java_character

MAX_CSV_CHARS = 8_000
MAX_CSV_COLUMNS = 40
SUPPORTED_OPTIONS = {"value", "delimiter", "delimiterString", "quoteCharacter", "emptyValue",
                     "nullValues", "ignoreLeadingAndTrailingWhitespace"}


def csv_options(annotation: dict, argument_nodes) -> tuple[dict | None, str | None]:
    """Read literal supported options; unknown semantics keep the entire source unresolved."""
    unit, ast = annotation["_unit"], annotation["_ast"]
    arguments = ast.child_by_field_name("arguments")
    names = []
    for item in arguments.named_children if arguments else []:
        key = item.child_by_field_name("key") if item.type == "element_value_pair" else None
        names.append(text(key, unit.content) if key else "value")
    if len(names) != len(set(names)) or any(name not in SUPPORTED_OPTIONS for name in names):
        return None, "CsvSource uses duplicate or unsupported options (including text blocks/headers)."
    options = {"delimiter": ",", "quote": "'", "empty": "", "nulls": [], "trim": True}
    if "delimiter" in names and "delimiterString" in names:
        return None, "CsvSource declares both delimiter and delimiterString."
    for name, target in (("delimiter", "delimiter"), ("quoteCharacter", "quote")):
        values = argument_nodes(annotation, name)
        if values:
            value = java_character(values[0], unit.content) if len(values) == 1 else None
            if value is None:
                return None, f"CsvSource {name} is not a supported character literal."
            options[target] = value
    for name, target in (("delimiterString", "delimiter"), ("emptyValue", "empty")):
        values = argument_nodes(annotation, name)
        if values:
            value = literal_string(values[0], unit.content) if len(values) == 1 else None
            if value is None:
                return None, f"CsvSource {name} is not one literal string."
            options[target] = value
    null_nodes = argument_nodes(annotation, "nullValues")
    for node in null_nodes:
        value = literal_string(node, unit.content)
        if value is None:
            return None, "CsvSource nullValues contains a computed value."
        options["nulls"].append(value)
    trim_nodes = argument_nodes(annotation, "ignoreLeadingAndTrailingWhitespace")
    if trim_nodes:
        value = text(trim_nodes[0], unit.content) if len(trim_nodes) == 1 else ""
        if value not in {"true", "false"}:
            return None, "CsvSource whitespace option is not a literal boolean."
        options["trim"] = value == "true"
    if options["empty"] and options["empty"] in options["nulls"]:
        return None, "CsvSource emptyValue overlaps nullValues; substitution order is unsupported."
    if not options["delimiter"] or len(options["delimiter"]) > 8 or options["quote"] in options["delimiter"]:
        return None, "CsvSource delimiter is empty, too long or conflicts with its quote character."
    return options, None


def csv_fields(value: str, options: dict) -> tuple[list[dict] | None, str | None]:
    """Parse declared CSV cells, preserving quoted empty versus unquoted null cells."""
    if len(value) > MAX_CSV_CHARS or "\n" in value or "\r" in value:
        return None, "CSV row exceeds the supported single-line character budget."
    delimiter, quote, trim = options["delimiter"], options["quote"], options["trim"]
    index, result = 0, []
    while True:
        if len(result) >= MAX_CSV_COLUMNS:
            return None, "CSV column budget exceeded."
        start = index
        if trim:
            while index < len(value) and value[index].isspace():
                index += 1
        quoted = index < len(value) and value[index] == quote
        cell = ""
        if quoted:
            index += 1
            closed = False
            while index < len(value):
                if value[index] != quote:
                    cell += value[index]
                    index += 1
                elif index + 1 < len(value) and value[index + 1] == quote:
                    cell += quote
                    index += 2
                else:
                    index += 1
                    closed = True
                    break
            if not closed:
                return None, "CSV quoted cell has no closing quote."
            if trim:
                while index < len(value) and value[index].isspace():
                    index += 1
            if index < len(value) and not value.startswith(delimiter, index):
                return None, "CSV text after a quoted cell is unsupported."
        else:
            end = value.find(delimiter, index)
            if end < 0:
                end = len(value)
            cell, index = value[index:end], end
            if quote in cell:
                return None, "CSV quote appears inside an unquoted cell."
            if trim:
                cell = cell.strip()
        raw = value[start:index]
        if (not quoted and cell == "") or cell in options["nulls"]:
            fact = {"kind": "null", "expression": raw, "value": None}
        else:
            fact = {"kind": "string", "expression": raw,
                    "value": options["empty"] if quoted and cell == "" else cell}
        result.append(fact)
        if index >= len(value):
            break
        index += len(delimiter)
    return result, None
