"""Подробное статическое дерево PHP: установленная Tree-sitter грамматика, без PHP runtime."""
from .parsed_source import parsed_tree, source_bytes
from ..node_documentation import empty_documentation
from .php_support import source as declarations
from .php_support import flow

WARNING = ('PHP: статические альтернативы текущего исходника, не исполненная трасса. '
           'Порядок sibling-выражений лексический: PHP не гарантирует общий порядок вычисления. '
           'Типы, DI, overload/magic dispatch, исключения вызовов и cleanup не разрешаются. '
           'Инициализаторы параметров и attributes вне дерева тела.')


def empty_result(node):
    """Пустые PHP-факты не наследуют предупреждения о поддержке C# attributes/dataflow."""
    return {'kind': 'method', 'line': node.get('line'), 'endLine': None,
            'sourceStatus': 'unavailable', 'analyzerVersion': 'php-method-flow-v1',
            'signature': '', 'parameters': [], 'returnType': '', 'documentation': empty_documentation(),
            'typeContext': None, 'events': [], 'eventsTruncated': False, 'unresolved': [],
            'parameterFacts': {'status': 'unavailable', 'kind': 'lexical-source', 'parameters': [],
                               'conditions': [], 'truncated': False,
                               'warning': 'PHP: lexical parameter references/dataflow не извлекаются; доступны только name/type объявления.'},
            'attributeFacts': {'status': 'unavailable', 'kind': 'lexical-source', 'groups': [],
                               'truncated': False, 'warning': 'PHP: attributes не извлекаются этим адаптером.'},
            'readabilityRecommendations': {'status': 'unavailable', 'items': [], 'truncated': False,
                                            'sourceKind': 'working-tree',
                                            'warning': 'PHP: эвристики сложных return не реализованы.'},
            'controlFlow': {'branches': [], 'terminals': [], 'truncated': False, 'unsupported': []},
            'methodFlow': {'version': 1, 'status': 'unavailable', 'steps': [], 'truncated': False,
                           'unsupported': [], 'sourceKind': 'working-tree-ast',
                           'limits': {'maxAstNodes': declarations.MAX_AST_NODES, 'maxSteps': flow.MAX_STEPS,
                                      'maxDepth': flow.MAX_DEPTH, 'maxExpressionChars': 400}, 'warning': WARNING}}


def unavailable(result, reason, *, unresolved=False, truncated=False):
    """Объясняет отсутствие дерева; parse/selection failure никогда не использует соседний метод."""
    result['sourceStatus'] = 'unresolved' if unresolved else 'unavailable'
    result['unresolved'].append(reason)
    result['methodFlow'].update(unsupported=[reason], truncated=truncated)
    result['controlFlow'].update(unsupported=[reason], truncated=truncated)
    return result


def analyze(source: dict, node: dict, *, max_events: int) -> dict:
    """Разбирает только переданный PHP-файл и точное объявление, соблюдая общие бюджеты."""
    result = empty_result(node)
    result['methodFlow']['limits']['maxEvents'] = max(0, max_events)
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_php
        content = source_bytes(source)
        tree = parsed_tree(source, 'php', lambda data: Parser(Language(tree_sitter_php.language_php())).parse(data))
    except (ImportError, AttributeError, TypeError):
        return unavailable(result, 'PHP: локальная Tree-sitter грамматика недоступна.')
    nodes = declarations.bounded_nodes(tree.root_node)
    if nodes is None:
        return unavailable(result, f'PHP: исчерпан лимит {declarations.MAX_AST_NODES} AST-узлов; частичное объявление не выбирается.', truncated=True)
    if tree.root_node.has_error:
        return unavailable(result, 'PHP: исходник содержит parse error; подробное дерево не построено.', unresolved=True)
    declaration = declarations.select_declaration(nodes, content, node)
    if declaration is None:
        return unavailable(result, 'PHP: точное объявление не найдено или неоднозначно по имени, строке и владельцу.', unresolved=True)
    result.update(declarations.declaration_facts(declaration, content))
    result.update(sourceStatus='parsed', line=declaration.start_point.row + 1, endLine=declaration.end_point.row + 1)
    body = declaration.child_by_field_name('body')
    if body is None:
        result['methodFlow']['status'] = 'declaration-only'
        result['unresolved'].append('PHP: объявление не содержит тела (abstract/interface).')
        return result
    builder = flow.Flow(content, max_events)
    if declarations.contains_owned(body, {'yield_expression'}):
        steps = builder.unknown(body, 'PHP: generator-тело отложено до iteration/resume; yield/send/throw/cleanup не развёрнуты.')
    elif declarations.contains_owned(body, {'goto_statement', 'named_label_statement'}):
        steps = builder.unknown(body, 'PHP: goto_statement/labels меняют достижимость всего тела; переходы не развёрнуты.')
    else:
        steps = builder.visit(body)
    result.update(events=builder.events, eventsTruncated=builder.events_truncated,
                  unresolved=list(builder.unsupported))
    result['methodFlow'].update(status='parsed', steps=steps, truncated=builder.truncated,
                                unsupported=list(builder.unsupported))
    result['controlFlow'].update(branches=builder.branches, terminals=builder.terminals,
                                 truncated=builder.truncated, unsupported=list(builder.unsupported))
    return result
