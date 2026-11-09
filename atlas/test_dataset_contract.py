"""Normalize declared dataset cells into named inputs without predicting test execution."""
from copy import deepcopy

from .java_annotations import public_annotation
from .test_dataset_values import stable_id

AGGREGATE_WITH = "org.junit.jupiter.params.aggregator.AggregateWith"
ARGUMENTS_ACCESSOR = "org.junit.jupiter.params.aggregator.ArgumentsAccessor"
CONVERT_WITH = "org.junit.jupiter.params.converter.ConvertWith"


def _extension_annotations(annotations: list[dict], local_annotations: dict) -> set[str]:
    """Follow bounded unique local meta-annotations for conversion/aggregation declarations."""
    found, pending, seen = set(), [(item, 0) for item in annotations], set()
    while pending:
        annotation, depth = pending.pop()
        name = annotation.get("qualifiedName")
        if not name or name in seen or depth >= 8:
            continue
        seen.add(name)
        found.add(name)
        declaration = local_annotations.get(name)
        if declaration is not None:
            pending.extend((item, depth + 1) for item in declaration["annotations"])
    return found


def parameters_and_bindings(record: dict, sources: list[dict], local_annotations: dict) -> tuple[list[dict], list[dict]]:
    """Bind a consistent indexed prefix; trailing resolver context remains explicitly unknown.

    Aggregators may consume several columns and custom converters may change values.
    Their presence never produces a guessed one-column-to-one-parameter mapping.
    """
    widths = set()
    unresolved = False
    for source in sources:
        if source.get("status") != "declared":
            unresolved = True
        for row in source.get("rows", []):
            widths.add(len(row.get("valueFacts", [])))
    width = next(iter(widths)) if len(widths) == 1 and not unresolved else None
    parameters, contexts = [], []
    aggregation = False
    for parameter in record["parameters"]:
        annotations = parameter["annotations"]
        names = _extension_annotations(annotations, local_annotations)
        qualified_type, _ = record["unit"].resolve(parameter.get("type", ""), {ARGUMENTS_ACCESSOR}, record["owner"])
        aggregator = AGGREGATE_WITH in names or qualified_type == ARGUMENTS_ACCESSOR
        aggregation = aggregation or aggregator
        item = {"id": stable_id("test-parameter:", record["nodeId"], parameter["position"]),
                "name": parameter["name"], "type": parameter.get("type", ""),
                "index": parameter["position"] - 1, "position": parameter["position"],
                "role": "aggregator" if aggregator else "unknown",
                "sourceLocation": {key: parameter[key] for key in ("file", "line", "column")},
                "annotations": [public_annotation(annotation) for annotation in annotations],
                "conversion": "unknown" if CONVERT_WITH in names else "not-evaluated"}
        parameters.append(item)
    if not sources:
        for parameter in parameters:
            parameter["role"] = "context"
            contexts.append({**deepcopy(parameter), "status": "unresolved",
                             "reason": "Non-parameterized test argument requires framework context resolution, which is not executed."})
    if width is not None and not aggregation and width <= len(parameters):
        for parameter in parameters:
            if parameter["index"] < width:
                parameter["role"] = "indexed"
            else:
                parameter["role"] = "context"
                contexts.append({**deepcopy(parameter), "status": "unresolved",
                                 "reason": "Trailing parameter is not supplied by declared rows; ParameterResolver execution is unknown."})
    for source in sources:
        source["bindingStatus"] = "declared" if width is not None and not aggregation and width <= len(parameters) else "unresolved"
        if source["bindingStatus"] == "unresolved":
            source["bindingReason"] = "Row widths, missing source facts or argument aggregation prevent an exact parameter binding."
        for row in source.get("rows", []):
            row["bindings"] = []
            if source["bindingStatus"] != "declared":
                continue
            for parameter, value in zip(parameters, row.get("valueFacts", [])):
                resolution = "literal" if value["kind"] not in {"expression", "missing"} else value["kind"]
                binding = {"parameterId": parameter["id"], "name": parameter["name"],
                           "index": parameter["index"], "type": parameter["type"],
                           "value": deepcopy(value), "resolution": resolution}
                if value.get("expressionTruncated"):
                    binding.update(resolution="unknown", reason="The captured value expression is truncated.")
                if parameter["conversion"] == "unknown":
                    binding.update(resolution="unknown", reason="Custom argument conversion is declared but is not executed.")
                row["bindings"].append(binding)
    return parameters, contexts
