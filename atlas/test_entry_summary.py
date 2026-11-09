"""Small test catalogue records; detailed evidence is fetched for one real symbol."""
from copy import deepcopy


def build_test_entry_summary(entry: dict) -> dict:
    """Keep navigable provider identities and counts without duplicating fixture/annotation trees."""
    result = {key: deepcopy(value) for key, value in entry.items()
              if key not in {'annotations', 'fixtures', 'dataSources', 'parameters', 'context'}}
    result['annotationCount'] = len(entry.get('annotations', []))
    result['fixtureCount'] = len(entry.get('fixtures', []))
    result['detailsAvailable'] = True
    result['parameterCount'] = len(entry.get('parameters', []))
    result['contextCount'] = sum(parameter.get('role') != 'indexed' for parameter in entry.get('parameters', []))
    keys = {'id', 'strategy', 'kind', 'label', 'status', 'expression', 'file', 'line', 'nodeIds',
            'declaredRowCount', 'rowsTruncated', 'completeness', 'composition', 'reason',
            'bindingStatus', 'bindingReason'}
    result['dataSources'] = [{key: deepcopy(value) for key, value in source.items() if key in keys}
                             for source in entry.get('dataSources', [])]
    return result
