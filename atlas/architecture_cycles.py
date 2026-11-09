"""Итеративный поиск SCC и одного пути-доказательства на циклическую компоненту."""


def cycle_components(edges: list[dict]) -> list[dict]:
    """Находит SCC и полный набор внутренних связей; сортировка делает результат детерминированным."""
    adjacency, reverse, facts = {}, {}, {}
    for edge in edges:
        left, right = edge['source'], edge['target']
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set())
        reverse.setdefault(right, set()).add(left)
        reverse.setdefault(left, set())
        facts.setdefault((left, right), edge)
    ordered = {node: sorted(targets) for node, targets in adjacency.items()}
    visited, finish = set(), []
    for root in sorted(ordered):
        if root in visited:
            continue
        stack = [(root, False)]
        while stack:
            node, exiting = stack.pop()
            if exiting:
                finish.append(node)
            elif node not in visited:
                visited.add(node)
                stack.append((node, True))
                stack.extend((target, False) for target in reversed(ordered[node]) if target not in visited)
    visited, components = set(), []
    for root in reversed(finish):
        if root in visited:
            continue
        component, stack = set(), [root]
        visited.add(root)
        while stack:
            node = stack.pop()
            component.add(node)
            for target in sorted(reverse[node], reverse=True):
                if target not in visited:
                    visited.add(target)
                    stack.append(target)
        if len(component) > 1 or root in adjacency[root]:
            components.append(component)
    components.sort(key=lambda values: sorted(values))
    by_node = {node: index for index, component in enumerate(components) for node in component}
    identities = [set() for _ in components]
    for edge in edges:
        index = by_node.get(edge['source'])
        if index is not None and by_node.get(edge['target']) == index:
            identities[index].add((edge['source'], edge['target'], edge.get('relation', '')))
    result = []
    for index, component in enumerate(components):
        # Серый узел в текущем DFS-пути даёт замкнутую направленную цепочку.
        color, path, positions = {}, [], {}
        found = None
        for root in sorted(component):
            if root in color or found:
                continue
            color[root] = 1
            path.append(root)
            positions[root] = 0
            stack = [(root, iter(ordered[root]))]
            while stack and found is None:
                node, targets = stack[-1]
                target = next(targets, None)
                if target is None:
                    color[node] = 2
                    positions.pop(node)
                    path.pop()
                    stack.pop()
                elif target in component:
                    if color.get(target) == 1:
                        nodes = path[positions[target]:] + [target]
                        found = [facts[(left, right)] for left, right in zip(nodes, nodes[1:])]
                    elif target not in color:
                        color[target] = 1
                        positions[target] = len(path)
                        path.append(target)
                        stack.append((target, iter(ordered[target])))
        if found:
            result.append({'path': found, 'identity': sorted(identities[index])})
    return result


def cycle_witnesses(edges: list[dict]) -> list[list[dict]]:
    """Возвращает только представительный путь каждой SCC для потребителей без baseline."""
    return [component['path'] for component in cycle_components(edges)]
