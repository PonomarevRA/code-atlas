"""Небольшая карта с циклом, входящими рёбрами, DTO и двумя исходными проектами."""


def view_graph():
    """Возвращает независимые факты для проверки выбора, исключений и направления связей."""
    nodes = [
        {'id': 'pa', 'label': 'A', 'kind': 'file', 'file': 'A/A.csproj'},
        {'id': 'pb', 'label': 'B', 'kind': 'file', 'file': 'B/B.csproj'},
        {'id': 'a', 'label': 'OrdersHandler', 'kind': 'class', 'file': 'A/Handler.cs', 'line': 1},
        {'id': 'b', 'label': 'Handle', 'kind': 'method', 'file': 'A/Handler.cs', 'line': 4},
        {'id': 'c', 'label': 'Repository.Save', 'kind': 'method', 'file': 'B/Repository.cs', 'line': 8},
        {'id': 'd', 'label': 'OrderDto', 'kind': 'dto', 'file': 'A/OrderDto.cs', 'line': 2},
        {'id': 'z', 'label': 'Unrelated', 'kind': 'class', 'file': 'B/Other.cs', 'line': 1},
    ]
    edges = [
        {'source': 'a', 'target': 'b', 'relation': 'method', 'confidence': 'EXTRACTED'},
        {'source': 'b', 'target': 'c', 'relation': 'calls', 'confidence': 'EXTRACTED', 'line': 5},
        {'source': 'c', 'target': 'b', 'relation': 'references', 'confidence': 'INFERRED'},
        {'source': 'b', 'target': 'd', 'relation': 'references', 'confidence': 'EXTRACTED'},
        {'source': 'b', 'target': 'c', 'relation': 'calls', 'confidence': 'EXTRACTED', 'line': 6},
    ]
    return {'nodes': nodes, 'edges': edges, 'metadata': {'snapshotId': 'first'}}


def view_request(**overrides):
    """Формирует явный запрос без зависимости от HTTP-defaults."""
    request = {
        'projectId': 'legacy-fixture', 'snapshotId': 'first', 'title': 'Orders overview',
        'query': 'Inspect handler dependencies', 'nodeIds': ['b'], 'search': '',
        'excludeKinds': ['DTO'], 'depth': 1, 'maxNodes': 120, 'annotations': [],
    }
    request.update(overrides)
    return request
