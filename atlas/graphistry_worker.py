"""Одноразовый CPU-процесс PyGraphistry; запускается через offline_runner."""

from collections import defaultdict
from contextlib import redirect_stdout
import json
import random
import sys


def compute_partitions(payload: dict) -> list[list[str]]:
    """Группирует топологию через настоящий compute_igraph, сохраняя изоляты."""
    import graphistry
    import igraph
    import pandas as pd

    # RNG igraph глобальный, поэтому вычисление вынесено в отдельный процесс.
    igraph.set_random_number_generator(random.Random(42))
    nodes = payload["nodes"]
    edges = payload["edges"]
    if not nodes:
        return []
    if not edges:
        return [[node] for node in nodes]
    node_table = pd.DataFrame({"id": nodes})
    edge_table = pd.DataFrame(edges, columns=["source", "target", "weight"])
    graph = graphistry.nodes(node_table, "id")
    graph = graph.edges(edge_table, "source", "target")
    grouped = graph.compute_igraph(
        "community_multilevel", out_col="community", directed=False,
        params={"weights": "weight", "resolution": 1},
    )
    communities = defaultdict(list)
    for node_id, community in grouped._nodes[["id", "community"]].itertuples(index=False, name=None):
        communities[community].append(node_id)
    return sorted(sorted(members) for members in communities.values())


def main() -> None:
    """Читает минимальную топологию из stdin; stdout содержит только результат JSON."""
    try:
        payload = json.load(sys.stdin)
        # Диагностика сторонних библиотек не должна повреждать протокол ответа.
        with redirect_stdout(sys.stderr):
            if "graphs" in payload:
                result = [compute_partitions(topology) for topology in payload["graphs"]]
            else:
                result = compute_partitions(payload)
        json.dump(result, sys.stdout, ensure_ascii=False)
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
