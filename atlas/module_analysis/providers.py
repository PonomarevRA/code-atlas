"""Воспроизводимые предложения; недоступный backend не заменяется молча другим."""

import math
import time

import networkx as nx

from .metrics import assess_partition, dependency_graph
from .models import ModuleAnalysisError, VERSION, validate_partition
from .sarf import SOURCE, sarf_partition

ALGORITHMS = ('current', 'louvain', 'leiden', 'sarf-simple-directed')


def _partition(module_graph, identifier, seed, resolution):
    """Выбирает алгоритм и явно описывает отличающуюся входную проекцию."""
    graph = dependency_graph(module_graph)
    if identifier == 'current' or graph.number_of_edges() == 0:
        return [[node] for node in sorted(graph)], 'identity-current-units'
    if identifier == 'sarf-simple-directed':
        return sarf_partition(graph), 'directed-simple-dedication'
    undirected = dependency_graph(module_graph, directed=False)
    if identifier == 'louvain':
        return nx.community.louvain_communities(undirected, weight='weight', seed=seed, resolution=resolution), 'undirected-summed-directions'
    return nx.community.leiden_communities(undirected, weight='weight', seed=seed, resolution=resolution, metric='modularity'), 'undirected-summed-directions'


def compare_partitions(module_graph, *, algorithms=('current', 'louvain', 'leiden'), seed=42, resolution=1.0):
    """
    Сравнивает разрешённые провайдеры с текущей class/folder декомпозицией.

    Leiden без подходящего backend даёт unavailable; исходные raw-факты и
    default группировки Atlas не меняются. SArF — explicit simple adaptation.
    Время этого вызова не является самостоятельным benchmark исследованием.
    """
    if not isinstance(algorithms, (list, tuple)) or not algorithms or len(algorithms) > len(ALGORITHMS):
        raise ModuleAnalysisError('Select one to four algorithms')
    if any(identifier not in ALGORITHMS for identifier in algorithms) or len(set(algorithms)) != len(algorithms):
        raise ModuleAnalysisError('Unknown or duplicate algorithm')
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2 ** 32 - 1:
        raise ModuleAnalysisError('seed must be an unsigned 32-bit integer')
    if isinstance(resolution, bool) or not isinstance(resolution, (int, float)) or not math.isfinite(resolution) or not 0 < resolution <= 10:
        raise ModuleAnalysisError('resolution must be finite and in (0,10]')
    proposals = []
    for identifier in algorithms:
        start = time.perf_counter()
        try:
            groups, preprocessing = _partition(module_graph, identifier, seed, resolution)
            partition = validate_partition(module_graph, [list(group) for group in groups])
            proposal = {'algorithm': identifier, 'status': 'available', 'partition': partition,
                        'preprocessing': preprocessing, 'assessment': assess_partition(module_graph, partition)}
            if identifier == 'sarf-simple-directed':
                proposal.update(variant='simple-directed-greedy', source=SOURCE,
                                effectiveParameters={'resolution': 1.0, 'isolatePolicy': 'preserve-separate'},
                                limitations=['No member-level dedication facts; simple formula D=1/fanin(target).',
                                             'Isolates retained; not a reproduction of paper experiments.',
                                             'SArF optimizes dedication Q; assessment Q uses observed raw counts.'])
        except (ImportError, NotImplementedError, nx.NetworkXNotImplemented, ModuleAnalysisError) as error:
            proposal = {'algorithm': identifier, 'status': 'unavailable', 'reason': str(error)[:500]}
        proposal['elapsedSeconds'] = time.perf_counter() - start
        proposals.append(proposal)
    return {'version': VERSION, 'projectionHash': module_graph['projectionHash'],
            'snapshotId': module_graph['snapshotId'], 'parameters': {'seed': seed, 'resolution': resolution},
            # Frozen runtimes may omit distribution metadata. The imported
            # implementation declares its version even when dist-info is absent.
            'environment': {'networkx': nx.__version__},
            'proposals': proposals, 'defaultChanged': False,
            'comparisonLimits': ['Distinct preprocessing/objectives are explicit; compare all returned partitions on the same observed graph.',
                                 'Functional quality requires independent labelled examples; Q alone is insufficient.']}
