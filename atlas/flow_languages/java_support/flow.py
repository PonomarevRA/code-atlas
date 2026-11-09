"""Статическое дерево Java с единым бюджетом шагов и точными call sites."""
from .declarations import TYPES, text

MAX_STEPS = 180
MAX_DEPTH = 24
CALLS = {'method_invocation', 'object_creation_expression', 'explicit_constructor_invocation'}
COMMENTS = {'line_comment', 'block_comment'}
METADATA = {'annotation', 'marker_annotation', 'modifiers', 'type_arguments', 'type_parameters'}
DEFERRED = TYPES | {'lambda_expression', 'class_body', 'method_declaration', 'constructor_declaration'}
OPAQUE = {'try_statement', 'try_with_resources_statement', 'synchronized_statement',
          'assert_statement', 'labeled_statement', 'yield_statement'}


class JavaFlow:
    """Обходит только выбранное тело; условные и отложенные пути не становятся линейными."""

    def __init__(self, content, max_events):
        """Фиксирует исходник и независимые лимиты шагов и событий одного запроса."""
        self.content = content
        self.max_events = max(0, max_events)
        self.count = 0
        self.events = []
        self.branches = []
        self.terminals = []
        self.unsupported = set()
        self.truncated = self.events_truncated = False

    def step(self, node, kind, **facts):
        """Выделяет шаг из общего бюджета, сохраняя ограниченную метку исходника."""
        if self.count >= MAX_STEPS:
            self.truncated = True
            return None
        self.count += 1
        item = {'kind': kind, 'label': text(node, self.content),
                'line': node.start_point.row + 1, 'column': node.start_point.column + 1, **facts}
        item['labelTruncated'] = len(item['label']) >= 400
        return item

    def one(self, node, kind, **facts):
        """Возвращает ноль или один шаг, учитывая исчерпанный бюджет."""
        item = self.step(node, kind, **facts)
        return [item] if item else []

    def unknown(self, node, reason):
        """Оставляет видимый исходник без выдуманного порядка неподдержанного тела."""
        self.unsupported.add(reason)
        return self.one(node, 'unknown', incomplete=True, unsupported=True)

    def guard(self, node, depth):
        """Обрывает рекурсию и делает причину ограничения доступной потребителю."""
        if node is None or node.type in COMMENTS or self.count >= MAX_STEPS:
            self.truncated |= node is not None and self.count >= MAX_STEPS
            return False
        if depth > MAX_DEPTH:
            self.truncated = True
            self.unsupported.add('Java flow depth exceeds 24; remaining syntax not extracted.')
            return False
        return True

    def call(self, node, context):
        """Добавляет вызов после receiver/аргументов; raw graph IDs не угадываются."""
        if len(self.events) >= self.max_events:
            self.events_truncated = self.truncated = True
            self.unsupported.add('Java call event budget exhausted; calls omitted from tree.')
            return []
        name_node = node.child_by_field_name('name')
        if node.type == 'object_creation_expression':
            name_node = node.child_by_field_name('type')
        elif node.type == 'explicit_constructor_invocation':
            name_node = node.child_by_field_name('constructor')
        callee = text(name_node, self.content)
        receiver = node.child_by_field_name('object')
        label = (text(receiver, self.content) + '.' if receiver else '') + callee + '()'
        if node.type == 'object_creation_expression':
            label = 'new ' + label
        site = {'line': node.start_point.row + 1, 'column': node.start_point.column + 1,
                'endLine': node.end_point.row + 1, 'endColumn': node.end_point.column + 1}
        item = self.step(node, 'call', label=label[:400], site=site,
                         sourceExpression=text(node, self.content))
        if item is None:
            return []
        arguments_node = node.child_by_field_name('arguments')
        arguments = []
        for index, argument in enumerate(arguments_node.named_children if arguments_node else []):
            if len(arguments) >= 64:
                break
            arguments.append({'index': index, 'expression': text(argument, self.content),
                              'expressionTruncated': len(text(argument, self.content)) >= 400,
                              'sourceKind': 'lexical-java-argument'})
        event = {'kind': 'call', 'text': label[:400], 'callee': callee, **site,
                 'targetNodeIds': [], 'confidence': 'source', 'conditional': bool(context),
                 'awaited': False, 'parallel': False, 'branchIds': list(context),
                 'sourceExpression': text(node, self.content),
                 'sourceExpressionTruncated': node.end_byte - node.start_byte > 400,
                 'arguments': arguments,
                 'argumentsTruncated': bool(arguments_node and len(arguments_node.named_children) > 64), 'bindingWarning': 'overload/runtime binding unknown'}
        self.events.append(event)
        return [item]

    def arm(self, node, label, depth, context, value=False):
        """Различает пустую ветвь, значение и пропущенный из-за бюджета исходник."""
        steps = self.expression(node, depth, context) if value else self.statements(node, depth, context)
        if value and not steps and node is not None and not self.truncated:
            steps = self.one(node, 'value', expression=text(node, self.content))
        result = {'label': label, 'steps': steps}
        if not steps:
            empty = node is None or node.type == 'empty_statement' or node.type == 'block' and all(c.type in COMMENTS for c in node.named_children)
            result['emptyReason'] = 'skip' if empty else 'not-extracted'
        return result

    def branch(self, node, kind, label, arms, context, item):
        """Публикует одну ветвь дерева и соответствующую описательную controlFlow-ветвь."""
        identifier = f'java-branch-{node.start_byte}-{kind}'
        result = [item] if item else []
        if item:
            item['arms'] = arms
            self.branches.append({'id': identifier, 'kind': kind, 'line': node.start_point.row + 1,
                'endLine': node.end_point.row + 1, 'condition': label, 'parentArmIds': list(context),
                'arms': [{'id': f'{identifier}-a{i}', 'label': a['label'], 'kind': 'case'} for i, a in enumerate(arms)]})
        return result

    def expression(self, node, depth=0, context=()):
        """Сохраняет Java left-to-right порядок, short-circuit и отложенные тела."""
        if not self.guard(node, depth):
            return []
        kind = node.type
        if kind in METADATA:
            return []
        if kind in DEFERRED:
            self.unsupported.add('Java lambda/local/anonymous class bodies are analyzed separately; invocation and initialization are unknown.')
            return self.one(node, 'statement', deferred=True)
        if kind in OPAQUE:
            return self.unknown(node, f'Java {kind}: exception/cleanup/jump semantics are not represented.')
        if kind == 'switch_expression':
            return self.switch(node, depth, context)
        if kind == 'ternary_expression':
            condition = node.child_by_field_name('condition')
            result = self.expression(condition, depth + 1, context)
            identifier = f'java-branch-{node.start_byte}-conditional'
            item = self.step(node, 'branch', id=identifier, controlKind='conditional', label=text(condition, self.content), arms=[])
            if item is None:
                return result
            if any(s['kind'] == 'branch' for s in result):
                item.update(conditionResult='reuse', conditionExpression=text(condition, self.content))
            arms = [self.arm(node.child_by_field_name(field), label, depth + 1,
                             context + (f'{identifier}-a{i}',), value=True)
                    for i, (field, label) in enumerate((('consequence', 'true'), ('alternative', 'false')))]
            return result + self.branch(node, 'conditional', text(condition, self.content), arms, context, item)
        if kind == 'binary_expression':
            operator = node.child_by_field_name('operator')
            op = text(operator, self.content)
            if op in {'&&', '||'}:
                left, right = node.child_by_field_name('left'), node.child_by_field_name('right')
                result = self.expression(left, depth + 1, context)
                identifier = f'java-branch-{node.start_byte}-{op}'
                item = self.step(node, 'branch', id=identifier, controlKind=op, label=text(left, self.content), arms=[])
                if item is None:
                    return result
                if any(s['kind'] == 'branch' for s in result):
                    item.update(conditionResult='reuse', conditionExpression=text(left, self.content))
                arms = [self.arm(right, 'left true' if op == '&&' else 'left false', depth + 1,
                                 context + (f'{identifier}-a0',), value=True),
                        {'label': 'short-circuit: skip right', 'steps': [], 'emptyReason': 'skip'}]
                return result + self.branch(node, op, text(left, self.content), arms, context, item)
        result = []
        for child in node.named_children:
            # Anonymous class declarations are not evaluations of the enclosing method.
            if kind == 'object_creation_expression' and child.type == 'class_body':
                self.unsupported.add('Java anonymous class bodies/initializers are separate; initialization effects unknown.')
                continue
            result.extend(self.expression(child, depth + 1, context))
        if kind in CALLS:
            result.extend(self.call(node, context))
        return result

    def statements(self, node, depth=0, context=()):
        """Разбирает statements, не раскрывая cleanup, labeled jumps или deferred bodies."""
        if not self.guard(node, depth):
            return []
        kind = node.type
        if kind in {'block', 'constructor_body'}:
            result = []
            for child in node.named_children:
                result.extend(self.statements(child, depth + 1, context))
            return result
        if kind == 'if_statement':
            condition = node.child_by_field_name('condition')
            result = self.expression(condition, depth + 1, context)
            identifier = f'java-branch-{node.start_byte}-if'
            item = self.step(node, 'branch', id=identifier, controlKind='if', label=text(condition, self.content), arms=[])
            if item is None:
                return result
            if any(s['kind'] == 'branch' for s in result):
                item.update(conditionResult='reuse', conditionExpression=text(condition, self.content))
            arms = [self.arm(node.child_by_field_name(field), label, depth + 1,
                             context + (f'{identifier}-a{i}',))
                    for i, (field, label) in enumerate((('consequence', 'true'), ('alternative', 'false')))]
            return result + self.branch(node, 'if', text(condition, self.content), arms, context, item)
        if kind in {'for_statement', 'enhanced_for_statement', 'while_statement', 'do_statement'}:
            return self.loop(node, depth, context)
        if kind in {'return_statement', 'throw_statement', 'break_statement', 'continue_statement'}:
            terminal = kind.removesuffix('_statement')
            if terminal in {'break', 'continue'} and node.named_children:
                return self.unknown(node, 'Java labeled break/continue target is not represented.')
            result = []
            for child in node.named_children:
                result.extend(self.expression(child, depth + 1, context))
            items = self.one(node, terminal)
            if items:
                self.terminals.append({**items[0], 'endLine': node.end_point.row + 1, 'branchIds': list(context)})
            return result + items
        if kind in OPAQUE or kind in DEFERRED:
            return self.expression(node, depth, context)
        if kind == 'switch_expression':
            return self.switch(node, depth, context)
        if kind in {'empty_statement'} | COMMENTS:
            return []
        result = self.expression(node, depth + 1, context)
        expression = node.named_children[0] if node.named_children else None
        direct_call = kind in CALLS or kind == 'expression_statement' and expression is not None and expression.type in CALLS
        if not direct_call or not result:
            result.extend(self.one(node, 'statement'))
        return result

    def loop(self, node, depth, context):
        """Отделяет однократный init/iterable от повторяемых condition/body/update."""
        kind = {'for_statement': 'for', 'enhanced_for_statement': 'foreach',
                'while_statement': 'while', 'do_statement': 'do'}[node.type]
        result = []
        for child in node.children_by_field_name('init'):
            result.extend(self.statements(child, depth + 1, context))
        if kind == 'foreach':
            result.extend(self.expression(node.child_by_field_name('value'), depth + 1, context))
        identifier = f'java-loop-{node.start_byte}'
        loop_context = context + (identifier + '-a0',)
        item = self.step(node, 'loop', id=identifier, controlKind=kind)
        if item is None:
            return result
        condition_node = node.child_by_field_name('condition')
        # A do-loop evaluates its first body before the first condition.
        if kind == 'do':
            body = self.statements(node.child_by_field_name('body'), depth + 1, loop_context)
            condition = self.expression(condition_node, depth + 1, loop_context)
        else:
            condition = self.expression(condition_node, depth + 1, loop_context)
            body = self.statements(node.child_by_field_name('body'), depth + 1, loop_context)
        update = []
        for child in node.children_by_field_name('update'):
            steps = self.expression(child, depth + 1, loop_context)
            update.extend(steps)
            if child.type not in CALLS or not steps:
                update.extend(self.one(child, 'statement'))
        loop_body = node.child_by_field_name('body')
        header = self.content[node.start_byte:loop_body.start_byte].decode('utf-8').strip()[:400] if loop_body else kind
        item.update(label=header, labelTruncated=len(header) >= 400, condition=condition, body=body, update=update,
                    canExit=not (kind == 'for' and condition_node is None),
                    conditionExpression=text(condition_node, self.content))
        if any(s['kind'] == 'branch' for s in condition):
            item['conditionResult'] = 'reuse'
        result.append(item)
        self.branches.append({'id': identifier, 'kind': kind, 'line': node.start_point.row + 1,
            'endLine': node.end_point.row + 1, 'condition': text(condition_node, self.content),
            'arms': [{'id': identifier + '-a0', 'label': 'iteration', 'kind': 'body'}],
            'parentArmIds': list(context)})
        return result

    def switch(self, node, depth, context):
        """Строит независимые arms; fallthrough, yield и guards оставляет opaque."""
        body = node.child_by_field_name('body')
        value_context = node.parent is not None and node.parent.type not in {'block', 'constructor_body', 'switch_block_statement_group', 'switch_rule'}
        groups = [c for c in body.named_children if c.type not in COMMENTS] if body else []
        sections, pending_labels, has_default = [], [], False
        if len(groups) > 60:
            self.truncated = True
            return self.unknown(node, 'Java switch exceeds 60 arms; remaining syntax not extracted.')
        for group in groups:
            label = next((c for c in group.named_children if c.type == 'switch_label'), None)
            statements = [c for c in group.named_children if c.type not in COMMENTS | {'switch_label'}]
            if label is None or any(c.type == 'guard' for c in label.named_children):
                return self.unknown(node, 'Java switch pattern guards are not represented.')
            # Test default syntax, not a substring inside a case string literal.
            has_default |= any(c.type == 'default' or c.type == 'identifier' and text(c, self.content) == 'default' for c in label.children)
            pending_labels.append(text(label, self.content))
            if not statements and group != groups[-1]:
                continue
            sections.append((' / '.join(pending_labels)[:400], statements, group.type))
            pending_labels = []
        for index, (_, statements, group_kind) in enumerate(sections):
            if group_kind == 'switch_block_statement_group' and index < len(sections) - 1:
                if not statements or statements[-1].type not in {'break_statement', 'return_statement', 'throw_statement'}:
                    return self.unknown(node, 'Java switch fallthrough is not represented; entire switch kept opaque.')
            # Arrow blocks may yield a value or perform jumps; do not pretend yield is return.
            if value_context and group_kind == 'switch_rule' and any(s.type == 'block' for s in statements):
                return self.unknown(node, 'Java switch expression block/yield requires value-join support.')
        if value_context and not has_default:
            return self.unknown(node, 'Java switch expression exhaustiveness/missing match is unknown without type analysis.')
        result = self.expression(node.child_by_field_name('condition'), depth + 1, context)
        identifier = f'java-branch-{node.start_byte}-switch'
        item = self.step(node, 'branch', id=identifier, controlKind='switch', label=text(node.child_by_field_name('condition'), self.content), arms=[])
        if item is None:
            return result
        arms = []
        for index, (label, statements, _) in enumerate(sections):
            steps = []
            for statement in statements:
                steps.extend(self.statements(statement, depth + 1, context + (f'{identifier}-a{index}',)))
            arm = {'label': label, 'labelTruncated': len(label) >= 400, 'steps': steps}
            if not steps:
                arm['emptyReason'] = 'skip' if not statements else 'not-extracted'
            arms.append(arm)
        if not has_default:
            arms.append({'label': 'no match', 'steps': [], 'emptyReason': 'skip'})
        return result + self.branch(node, 'switch', text(node.child_by_field_name('condition'), self.content), arms, context, item)
