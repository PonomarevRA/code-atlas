"""Actual LangGraph state transitions shared by guide and documentation mods."""
from typing import TypedDict

WORKFLOW_VERSION = 'local-facts-v1'


class ResearchState(TypedDict, total=False):
    """Checkpoint only bounded references/cursors, never a graph, model key or provider object."""
    selected: list[dict]
    cursor: int
    warnings: list[str]


def build_workflow(context, saver):
    """Compile the shared local collector; the context owns services and immutable output publication."""
    from langgraph.graph import StateGraph, START, END

    def seed(state):
        context.check('Находим символы по вопросу или выбору')
        selected = context.collector.seeds()
        return {'selected': selected, 'cursor': 0, 'warnings': [] if selected else ['Лексический поиск не нашёл символов. Уточните имя или выберите карточку.']}

    def capture(state):
        context.check(f"Читаем символ {state['cursor'] + 1} из {len(state['selected'])}")
        selected = list(state['selected'])
        current = selected[state['cursor']]
        _, edges = context.collector.capture_node(current['id'])
        warnings = list(state.get('warnings', []))
        if current['depth'] < context.run['maxDepth']:
            seen = {item['id'] for item in selected}
            for edge in edges:
                if edge['target'] in seen:
                    continue
                if len(selected) >= context.run['maxNodes']:
                    if 'Число исследуемых символов ограничено.' not in warnings:
                        warnings.append('Число исследуемых символов ограничено.')
                    break
                seen.add(edge['target'])
                selected.append({'id': edge['target'], 'depth': current['depth'] + 1})
        elif edges and 'Глубина обхода связей ограничена.' not in warnings:
            warnings.append('Глубина обхода связей ограничена.')
        return {'selected': selected, 'cursor': state['cursor'] + 1, 'warnings': warnings}

    def route(state):
        return 'capture' if state['cursor'] < len(state['selected']) else 'references'

    def references(state):
        context.check('Читаем уже импортированные справочники')
        try:
            context.collector.capture_references()
            return {}
        except Exception as error:
            from ..reference_documents import ReferenceError
            if not isinstance(error, ReferenceError):
                raise
            return {'warnings': [*state.get('warnings', []), 'Часть локальных справочников недоступна: ' + str(error)[:300]]}

    def publish(state):
        context.check('Проверяем источники и сохраняем документ')
        context.publish(state.get('warnings', []))
        return {}

    graph = StateGraph(ResearchState)
    for name, callback in [('seed', seed), ('capture', capture), ('references', references), ('publish', publish)]:
        graph.add_node(name, callback)
    graph.add_edge(START, 'seed')
    graph.add_conditional_edges('seed', route)
    graph.add_conditional_edges('capture', route)
    graph.add_edge('references', 'publish')
    graph.add_edge('publish', END)
    return graph.compile(checkpointer=saver, name=WORKFLOW_VERSION)
