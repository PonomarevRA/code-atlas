"""Версионированные локальные метрики AST; вызовы других методов не разворачиваются."""

from . import rust_support
from .lexical_volume import HalsteadTokens
from .code_readability import ReadabilitySignals, READABILITY_NODES

VERSION = 'local-structure-v1'
MAX_AST_NODES = 50000
SUPPORTED = {'csharp', 'python', 'java', 'javascript', 'typescript', 'go', 'rust'}
BRANCHES = {'if_statement', 'if_expression', 'elif_clause', 'conditional_expression',
            'ternary_expression', 'switch_statement', 'switch_expression',
            'expression_switch_statement', 'type_switch_statement', 'match_statement'}
LOOPS = {'for_statement', 'for_in_statement', 'foreach_statement', 'while_statement',
         'do_statement', 'for_each_statement'}
CASES = {'switch_section', 'switch_expression_arm', 'switch_block_statement_group',
         'switch_rule', 'switch_case', 'switch_default', 'expression_case', 'type_case',
         'default_case', 'case_clause'}
DEFERRED = {'lambda_expression', 'anonymous_method_expression', 'local_function_statement',
            'function_definition', 'function_declaration', 'generator_function_declaration',
            'method_definition', 'method_declaration', 'constructor_declaration',
            'arrow_function', 'function_expression', 'func_literal', 'lambda', 'class_definition', 'class_declaration'} | rust_support.DEFERRED


def unavailable_complexity(language='', reason='source unavailable', *, source_kind='unavailable', source_hash=None):
    """Неизвестный метод не получает ложные нули или оценку качества."""
    return {'version': VERSION, 'language': language, 'status': 'unavailable', 'reason': reason,
            'score': None, 'maxNestingDepth': None, 'averageNestingDepth': None,
            'branchingCount': None, 'loopCount': None, 'sourceKind': source_kind, 'sourceHash': source_hash}


def analyze_complexity(declaration, source, language, *, source_kind, source_hash):
    """Один bounded обход уже разобранного метода; среднее взвешено по лексическим операторам.

    Глубина считает if/switch/?: и циклы, не скобки и не цепочку вызовов. else-if
    остаётся на уровне исходного if; short-circuit добавляет выбор, но не уровень.
    Вложенные функции/lambda считаются отдельно и не становятся логикой caller.
    Это структурная эвристика, не cyclomatic/cognitive complexity или runtime cost.
    """
    if language not in SUPPORTED:
        return unavailable_complexity(language, 'language rules unavailable', source_kind=source_kind, source_hash=source_hash)
    if declaration.has_error:
        # Recovery AST is not a reliable lower bound for the intended valid program.
        return unavailable_complexity(language, 'syntax errors', source_kind=source_kind, source_hash=source_hash)
    body = declaration.child_by_field_name('body')
    if body is None:
        body = next((child for child in declaration.named_children if child.type == 'arrow_expression_clause'), None)
    if body is None:
        return unavailable_complexity(language, 'declaration has no body', source_kind=source_kind, source_hash=source_hash)
    counts = {'branches': 0, 'shortCircuit': 0, 'loops': 0, 'samples': 0, 'depthSum': 0, 'maxDepth': 0,
              'deferredBodies': 0, 'visited': 0}
    rust = language == 'rust'
    branches = BRANCHES | rust_support.BRANCHES if rust else BRANCHES
    loops = LOOPS | rust_support.LOOPS if rust else LOOPS
    unsupported = set()
    readability = ReadabilitySignals()
    halstead = HalsteadTokens()
    pending = [(body, 0)]
    while pending and counts['visited'] < MAX_AST_NODES:
        node, depth = pending.pop()
        counts['visited'] += 1
        kind = node.type
        if kind in DEFERRED:
            counts['deferredBodies'] += 1
            continue
        halstead.observe(node, source)
        if 'comment' in kind or kind in {'string', 'string_literal', 'verbatim_string_literal', 'raw_string_literal'}:
            continue
        branch = kind in branches or rust and kind == 'let_declaration' and node.child_by_field_name('alternative') is not None
        loop = kind in loops
        # Else-if alternatives do not imply another nested scope.
        alternative = node.parent and (node.parent.type == 'else_clause' or
            node.parent.child_by_field_name('alternative') == node)
        flat_if = kind in {'if_statement', 'elif_clause'} or rust and kind == 'if_expression'
        nested = depth + int((branch or loop) and not (flat_if and alternative))
        counts['maxDepth'] = max(counts['maxDepth'], nested)
        if branch:
            arms = sum(child.type in CASES for child in node.named_children)
            for child in node.named_children:
                if child.type in {'switch_body', 'switch_block', 'block', 'match_block'}:
                    arms += sum(case.type in CASES or rust and case.type == 'match_arm' for case in child.named_children)
            counts['branches'] += max(1, arms - 1)
        if rust and kind == 'match_pattern' and node.child_by_field_name('condition') is not None:
            counts['branches'] += 1  # Guard — отдельная проверка после совпадения образца.
        if loop:
            counts['loops'] += 1
        token = None
        if kind in {'binary_expression', 'boolean_operator', 'binary_operator'}:
            operator = node.child_by_field_name('operator')
            token = source[operator.start_byte:operator.end_byte].decode('utf-8') if operator else ''
            if not token:
                token = next((child.type for child in node.children if child.type in {'&&', '||', '??', 'and', 'or'}), '')
            if token in {'&&', '||', '??', 'and', 'or'}:
                counts['shortCircuit'] += 1
        if kind in READABILITY_NODES:
            readability.observe(node, source, token)
        optional = kind in {'member_expression', 'subscript_expression', 'call_expression'} and any(child.type == 'optional_chain' for child in node.named_children)
        if kind == 'conditional_access_expression' or optional:
            counts['shortCircuit'] += 1
        if rust and kind == 'try_expression':
            counts['shortCircuit'] += 1  # ? может завершить текущую область, тип Try не вычисляется.
        if rust and kind == 'macro_invocation':
            unsupported.add('macro expansion unavailable')
        sample = branch or loop or kind.endswith('_statement') and kind not in {'block', 'statement_block'}
        if rust:
            sample = rust_support.statement_sample(node, branch or loop)
        # Python assignments are wrapped in expression_statement; avoid counting their AST twice.
        if sample:
            counts['samples'] += 1
            counts['depthSum'] += nested
        pending.extend((child, nested) for child in reversed(node.named_children))
    partial = bool(pending) or bool(unsupported)
    branching = counts['branches'] + counts['shortCircuit']
    score = branching + counts['loops'] + counts['maxDepth']
    return {'version': VERSION, 'language': language, 'status': 'partial' if partial else 'complete',
            'score': score, 'level': 'high' if score >= 10 else 'medium' if score >= 5 else 'low',
            'maxNestingDepth': counts['maxDepth'],
            'averageNestingDepth': None if partial else round(counts['depthSum'] / counts['samples'], 2) if counts['samples'] else 0.0,
            'branchingCount': branching, 'branchCount': counts['branches'], 'shortCircuitCount': counts['shortCircuit'],
            'loopCount': counts['loops'], 'statementCount': counts['samples'],
            'excludedDeferredBodies': counts['deferredBodies'], 'truncated': bool(pending),
            'readability': readability.result(partial=partial, deferred_bodies=counts['deferredBodies']),
            'halstead': halstead.result(partial=partial, deferred_bodies=counts['deferredBodies']),
            **({'languageRulesVersion': 'rust-structure-v1', 'unsupported': sorted(unsupported)} if rust else {}),
            'formula': 'branchingCount + loopCount + maxNestingDepth',
            'averageBasis': 'lexical statements; top level 0; control statement includes its own level',
            'sourceKind': source_kind, 'sourceHash': source_hash,
            'scope': 'local method body; callees and deferred bodies excluded'}
