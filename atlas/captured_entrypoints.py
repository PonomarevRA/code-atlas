"""Read declared entries from one snapshot independently of current-source infrastructure scans."""
from copy import deepcopy
from .test_entry_summary import build_test_entry_summary


def captured_entries(graph):
    """Keep existing handler IDs, bounded test summaries and captured Python registrations."""
    known = {node['id'] for node in graph.get('nodes', [])}
    metadata = graph.get('metadata', {})
    entries = []
    for entry in metadata.get('testDiscovery', {}).get('entries', []):
        if entry.get('handlerNodeIds') and all(identifier in known for identifier in entry['handlerNodeIds']):
            entries.append(build_test_entry_summary(entry))
    for entry in metadata.get('entrypointDiscovery', {}).get('entries', []):
        copy = deepcopy(entry)
        copy['handlerNodeIds'] = [identifier for identifier in entry.get('handlerNodeIds', []) if identifier in known]
        entries.append(copy)
    return entries


def captured_entrypoint_catalog(graph, snapshot_id):
    """A no-source-read catalogue stays available when a working-tree scan is slow or unavailable."""
    metadata = graph.get('metadata', {})
    discovery = metadata.get('entrypointDiscovery', {})
    return {'snapshotId': snapshot_id, 'sourceKind': 'snapshot-captured-source',
        'entries': sorted(captured_entries(graph), key=lambda entry: (entry['kind'], entry['label'], entry['id'])),
        'coverage': {'candidateFiles': 0, 'readFiles': 0, 'limited': False, 'scope': 'captured-only'},
        'capturedCoverage': deepcopy(discovery.get('coverage', {})),
        'entrypointDiscovery': {key: value for key, value in discovery.items() if key != 'entries'},
        'testDiscovery': {key: value for key, value in metadata.get('testDiscovery', {}).items() if key != 'entries'},
        'analysisMods': deepcopy(metadata.get('analysisMods', {})),
        'warning': 'Объявления сохранённого снимка. Исходники повторно не читаются; выполнение, конфигурация и транспорт неизвестны.'}
