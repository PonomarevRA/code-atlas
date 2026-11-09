"""Связность и ограниченный размер Natural, в том числе для больших хабов."""

from copy import deepcopy

import networkx as nx
import pytest

from atlas.natural_grouping import MAX_GROUP_SIZE, partitions


@pytest.mark.parametrize("graph", [nx.Graph(), nx.empty_graph(8), nx.star_graph(320),
                                    nx.barbell_graph(45, 3), nx.path_graph(500)])
def test_natural_partitions_cover_connected_bounded_groups(graph):
    """Порядок входа, изоляты и fanout не нарушают покрытие, связность и исходные факты."""
    graph = nx.relabel_nodes(graph, lambda node: f"n-{node:04d}")
    for source, target in graph.edges:
        graph[source][target]["weight"] = 2.5
    before = deepcopy(graph)
    groups = partitions(graph)
    assert len(set().union(*groups)) == graph.number_of_nodes()
    assert sum(map(len, groups)) == graph.number_of_nodes()
    for group in groups:
        assert 0 < len(group) <= MAX_GROUP_SIZE
        assert nx.is_connected(graph.subgraph(group))
    reversed_graph = nx.Graph()
    reversed_graph.add_nodes_from(reversed(list(graph.nodes)))
    reversed_graph.add_edges_from(reversed(list(graph.edges(data=True))))
    assert partitions(reversed_graph) == groups
    assert nx.utils.graphs_equal(graph, before)
