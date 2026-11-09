"""Ограниченное дерево локального вычисления JavaScript без runtime-трассировки."""
from .declarations import CALLABLES, CLASSES, text

MAX_STEPS = 180
MAX_DEPTH = 24
MAX_LABEL = 400
WARNING = ('Статический порядок вычисления, не трасса исполнения. Типы, runtime binding, '
           'истинность условий и параллельность не установлены. Отложенные тела исключены. '
           'JSX-компоненты и сгенерированные framework-вызовы не исполняются и не угадываются.')
CHAIN = {'call_expression', 'member_expression', 'subscript_expression'}
LITERALS = {'identifier', 'property_identifier', 'private_property_identifier', 'number', 'string',
            'true', 'false', 'null', 'undefined', 'this', 'super', 'regex', 'shorthand_property_identifier'}


def site(node):
    """Использует 1-based байтовые колонки Tree-sitter, конец исключающий."""
    return {'line': node.start_point.row + 1, 'column': node.start_point.column + 1,
            'endLine': node.end_point.row + 1, 'endColumn': node.end_point.column + 1}


def abrupt(steps):
    """Распознаёт лишь синтаксически гарантированный выход из текущего блока."""
    if not steps:
        return False
    last = steps[-1]
    if last['kind'] in {'return', 'throw', 'break', 'continue'}:
        return True
    return last['kind'] == 'branch' and all(abrupt(arm['steps']) for arm in last['arms'])


def exits_iteration(steps):
    """Отличает continue от путей, которые гарантированно обходят update/condition."""
    if not steps:
        return False
    last = steps[-1]
    if last['kind'] in {'return', 'throw', 'break'}:
        return True
    return last['kind'] == 'branch' and all(exits_iteration(arm['steps']) for arm in last['arms'])


class Flow:
    """Единый владелец порядка шагов, событий и контекста ветвей одного метода."""

    def __init__(self, source, max_events):
        """Устанавливает отдельные ограничения дерева и массива событий."""
        self.source = source
        self.max_events = max(0, max_events)
        self.events, self.branches, self.terminals, self.unsupported = [], [], [], []
        self.steps = self.serial = 0
        self.truncated = self.events_truncated = False

    def label(self, node):
        """Ограничивает только сериализованный фрагмент, сохраняя точные координаты."""
        value = text(node, self.source)
        if len(value) > MAX_LABEL:
            self.issue('Исходное выражение сокращено до 400 символов.')
        return value[:MAX_LABEL]

    def issue(self, reason):
        """Сохраняет каждую причину неполноты один раз."""
        if reason not in self.unsupported:
            self.unsupported.append(reason)

    def step(self, kind, node, label=None, **facts):
        """Выделяет один шаг в общем бюджете, включая вложенные arms и значения."""
        if self.truncated:
            return None
        if self.steps >= MAX_STEPS:
            self.truncated = True
            self.events_truncated = True
            self.issue('Исчерпан бюджет 180 шагов; оставшийся исходник не извлечён.')
            return None
        self.steps += 1
        return {'kind': kind, 'label': self.label(node) if label is None else label[:MAX_LABEL],
                **site(node), **facts}

    def opaque(self, node, reason):
        """Оставляет видимый исходник вместо ложного раскрытия неподдержанного пути."""
        self.issue(reason)
        item = self.step('statement', node, label='Не извлечено: ' + self.label(node),
                         sourceExpression=self.label(node), incomplete=True, unsupported=reason)
        return [item] if item else []

    def sequence(self, nodes, context=(), depth=0):
        """Читает последовательность до безусловного выхода или ограничения дерева."""
        result = []
        for node in nodes:
            if self.truncated:
                break
            result.extend(self.read(node, context, depth + 1))
            if abrupt(result):
                break
        return result

    def branch(self, node, kind, arms, context, depth, label=None):
        """Строит альтернативы; только события внутри arm получают его ID."""
        item = self.step('branch', node, label=label, controlKind=kind, arms=[])
        if not item:
            return []
        self.serial += 1
        identifier = f'js-b{self.serial}'
        item['id'] = identifier
        record = {'id': identifier, 'kind': kind, 'label': item['label'],
                  **site(node), 'arms': []}
        self.branches.append(record)
        for index, (arm_label, expression, as_value) in enumerate(arms):
            arm_id = f'{identifier}-a{index}'
            arm_context = (*context, arm_id)
            if callable(expression):
                steps = expression(arm_context)
            else:
                steps = self.read(expression, arm_context, depth + 1)
                if as_value and expression is not None and not steps:
                    value = self.step('value', expression, expression=self.label(expression))
                    steps = [value] if value else []
            empty = ('not-extracted' if self.truncated else 'skip') if not steps else None
            item['arms'].append({'id': arm_id, 'label': arm_label, 'steps': steps, 'emptyReason': empty})
            record['arms'].append({'id': arm_id, 'label': arm_label, 'kind': 'alternative',
                                   'line': node.start_point.row + 1, 'endLine': node.end_point.row + 1})
        return [item]

    def call(self, node, context, awaited=False):
        """Добавляет вызов после receiver/аргументов с общим точным site для host binding."""
        function = node.child_by_field_name('function') or node.child_by_field_name('constructor')
        name = function
        if function is not None and function.type == 'member_expression':
            name = function.child_by_field_name('property')
        callee_types = {'identifier', 'property_identifier', 'private_property_identifier', 'super'}
        callee = self.label(name) if name is not None and name.type in callee_types else ''
        label = self.label(function) + '()' if callee else 'Вычисляемая функция()'
        item = self.step('call', node, label=label, site=site(node),
                         sourceExpression=self.label(node), directAwait=awaited)
        if item is None:
            return []
        args = node.child_by_field_name('arguments')
        arguments = []
        if args is not None:
            for argument in args.named_children:
                if argument.type == 'comment':
                    continue
                arguments.append({'index': len(arguments), 'expression': self.label(argument),
                                  'sourceKind': 'lexical-source', **site(argument)})
        event = {'kind': 'call', 'text': label[:MAX_LABEL], 'callee': callee, **site(node),
                 'sourceExpression': self.label(node), 'targetNodeIds': [],
                 'confidence': 'source' if callee else 'unresolved', 'conditional': bool(context),
                 'awaited': awaited, 'parallel': False, 'branchIds': list(context),
                 'arguments': arguments, 'argumentsTruncated': False}
        if len(self.events) < self.max_events:
            self.events.append(event)
        else:
            self.events_truncated = True
            self.issue('Исчерпан бюджет событий вызова.')
        return [item]

    def chain(self, node, context, depth, awaited=False):
        """Сохраняет границы optional-chain; скобки завершают её распространение."""
        operations, base = [], node
        while base is not None and base.type in CHAIN:
            operations.append(base)
            field = 'function' if base.type == 'call_expression' else 'object'
            base = base.child_by_field_name(field)
        operations.reverse()
        result = self.read(base, context, depth + 1)
        if len(operations) + depth > MAX_DEPTH:
            boundary = self.opaque(node, 'Цепочка превышает глубину 24; продолжение не извлечено.')
            self.truncated = self.events_truncated = True
            return result + boundary

        def continuation(index, arm_context):
            """Раскрывает suffix внутри nullish-arm без повторного receiver."""
            steps = []
            while index < len(operations) and not self.truncated:
                operation = operations[index]
                if operation.child_by_field_name('optional_chain') is not None:
                    def present(inner_context, start=index):
                        """Обходит optional boundary ровно один раз."""
                        return one(start, inner_context) + continuation(start + 1, inner_context)
                    steps.extend(self.branch(operation, '?.',
                        [('не null/undefined', present, False), ('null/undefined: skip', None, False)],
                        arm_context, depth + index, label=self.label(operation)))
                    return steps
                steps.extend(one(index, arm_context))
                index += 1
            return steps

        def one(index, arm_context):
            """Обрабатывает одну property/index/call операцию после готового receiver."""
            operation = operations[index]
            if operation.type == 'call_expression':
                args = operation.child_by_field_name('arguments')
                if args is not None and args.type != 'arguments':
                    return self.opaque(operation, 'Tagged template call не раскрыт: специальный порядок аргументов.')
                steps = []
                if args is not None:
                    for argument in args.named_children:
                        steps.extend(self.read(argument, arm_context, depth + index + 1))
                steps.extend(self.call(operation, arm_context, awaited and operation == node))
                return steps
            if operation.type == 'subscript_expression':
                steps = self.read(operation.child_by_field_name('index'), arm_context, depth + index + 1)
            else:
                steps = []
            if index == len(operations) - 1 and arm_context != context:
                value = self.step('value', operation, expression=self.label(operation), reuseResult=True)
                if value:
                    steps.append(value)
            return steps

        return result + continuation(0, context)

    def conditional(self, node, context, depth):
        """Отделяет безусловное условие от условных RHS и альтернатив ?: ."""
        if node.type == 'ternary_expression':
            condition = node.child_by_field_name('condition')
            before = self.read(condition, context, depth + 1)
            arms = [('истина', node.child_by_field_name('consequence'), True),
                    ('ложь', node.child_by_field_name('alternative'), True)]
            return before + self.branch(node, 'conditional', arms, context, depth)
        left, right = node.child_by_field_name('left'), node.child_by_field_name('right')
        operator = text(node.child_by_field_name('operator'), self.source)
        before = self.read(left, context, depth + 1)
        label = {'&&': 'левая часть истинна', '||': 'левая часть ложна',
                 '??': 'левая часть null/undefined'}[operator]
        return before + self.branch(node, operator, [(label, right, True), ('иначе: skip', None, False)],
                                    context, depth)

    def loop(self, node, context, depth):
        """Разделяет init/iterable, повторное condition, body и update; jumps остаются terminals."""
        kind = {'for_statement': 'for', 'for_in_statement': 'foreach',
                'while_statement': 'while', 'do_statement': 'do'}[node.type]
        if node.type == 'for_in_statement' and any(child.type == 'await' for child in node.children):
            return self.opaque(node, 'for await: async iterator/cleanup не представлен общим деревом.')
        before = self.read(node.child_by_field_name('initializer'), context, depth + 1)
        iterable = node.child_by_field_name('right') if node.type == 'for_in_statement' else None
        before.extend(self.read(iterable, context, depth + 1))
        condition = node.child_by_field_name('condition')
        if condition is not None and condition.type == 'empty_statement':
            condition = None
        item = self.step('loop', node, controlKind=kind, condition=[], body=[], update=[],
                         canExit=not (kind == 'for' and condition is None))
        if not item:
            return before
        self.serial += 1
        identifier = f'js-b{self.serial}'
        body_id = identifier + '-a0'
        item['id'] = identifier
        self.branches.append({'id': identifier, 'kind': kind, 'label': item['label'], **site(node),
                              'arms': [{'id': body_id, 'kind': 'body', 'label': 'тело цикла',
                                        'line': node.start_point.row + 1, 'endLine': node.end_point.row + 1}]})
        # A do body precedes its test; the other loop tests are lexical repeated checks.
        body_context = (*context, body_id)
        left = node.child_by_field_name('left') if node.type == 'for_in_statement' else None
        binding = self.read(left, body_context, depth + 1) if left is not None and left.type in CHAIN else []
        if left is not None and left.type in {'object_pattern', 'array_pattern'}:
            binding = self.opaque(left, 'Destructuring iteration binding: defaults/keys не раскрыты.')
        if kind == 'do':
            item['body'] = self.read(node.child_by_field_name('body'), body_context, depth + 1)
            if not exits_iteration(item['body']):
                item['condition'] = self.read(condition, body_context, depth + 1)
        else:
            item['condition'] = self.read(condition, body_context, depth + 1)
            item['body'] = binding + self.read(node.child_by_field_name('body'), body_context, depth + 1)
        if not exits_iteration(item['body']):
            item['update'] = self.read(node.child_by_field_name('increment'), body_context, depth + 1)
        return before + [item]

    def construct(self, node, context, depth, awaited=False):
        """Читает constructor и аргументы перед new; тело конструктора не исполняет."""
        result = self.read(node.child_by_field_name('constructor'), context, depth + 1)
        arguments = node.child_by_field_name('arguments')
        if arguments:
            for argument in arguments.named_children:
                result.extend(self.read(argument, context, depth + 1))
        return result + self.call(node, context, awaited)

    def read(self, node, context=(), depth=0):
        """Диспетчеризует AST по семантике вычисления, никогда не generic-walk deferred bodies."""
        if node is None or self.truncated:
            return []
        if depth > MAX_DEPTH:
            boundary = self.opaque(node, 'Исчерпана глубина 24; вложенный исходник не извлечён.')
            self.truncated = self.events_truncated = True
            return boundary
        kind = node.type
        if kind in LITERALS | {'comment', 'empty_statement'}:
            return []
        if kind in CLASSES:
            return self.opaque(node, 'Локальный class: extends/computed keys/static initialization не раскрыты; тела методов отложены.')
        if kind in CALLABLES:
            before = []
            name = node.child_by_field_name('name')
            if kind == 'method_definition' and name is not None and name.type == 'computed_property_name':
                before = self.read(name, context, depth + 1)
            item = self.step('statement', node, deferred=True)
            return before + ([item] if item else [])
        if kind == 'expression_statement':
            result = self.sequence(node.named_children, context, depth)
            expression = next((child for child in node.named_children if child.type != 'comment'), None)
            has_delete = False
            if expression is not None and expression.type == 'unary_expression':
                has_delete = any(child.type == 'delete' for child in expression.children)
            if not result or has_delete:
                item = self.step('statement', node)
                result.extend([item] if item else [])
            return result
        if kind in {'statement_block', 'else_clause', 'parenthesized_expression'}:
            return self.sequence(node.named_children, context, depth)
        if kind in {'jsx_element', 'jsx_self_closing_element', 'jsx_fragment'}:
            # Attributes and child expressions evaluate here; component bodies do not.
            result = self.sequence(node.named_children, context, depth)
            value = self.step('value', node, label='JSX: ' + self.label(node), expression=self.label(node))
            return result + ([value] if value else [])
        if kind in {'jsx_opening_element', 'jsx_expression', 'jsx_attribute'}:
            return self.sequence(node.named_children, context, depth)
        if kind in {'jsx_closing_element', 'jsx_text', 'jsx_namespace_name', 'html_character_reference'}:
            return []
        if kind in {'try_statement', 'with_statement', 'labeled_statement', 'switch_statement',
                    'yield_expression'}:
            reasons = {'try_statement': 'try/catch/finally: exception paths и finally не представлены линейно.',
                       'switch_statement': 'switch: case evaluation и fallthrough требуют отдельной модели.',
                       'yield_expression': 'yield: suspension/delegation не раскрыты.',
                       'labeled_statement': 'labeled jumps: цель выхода не представлена.',
                       'with_statement': 'with: динамическая область имён не разрешена.'}
            return self.opaque(node, reasons[kind])
        if kind in CHAIN:
            return self.chain(node, context, depth)
        if kind == 'await_expression':
            argument = next((child for child in node.named_children if child.type != 'comment'), None)
            while argument is not None and argument.type == 'parenthesized_expression':
                argument = next((child for child in argument.named_children if child.type != 'comment'), None)
            if argument is not None and argument.type in CHAIN:
                result = self.chain(argument, context, depth + 1, awaited=True)
            elif argument is not None and argument.type == 'new_expression':
                result = self.construct(argument, context, depth + 1, awaited=True)
            else:
                result = self.read(argument, context, depth + 1)
            item = self.step('await', node, label='await ' + self.label(argument))
            return result + ([item] if item else [])
        operator = text(node.child_by_field_name('operator'), self.source)
        short_circuit = kind == 'binary_expression' and operator in {'&&', '||', '??'}
        if kind == 'ternary_expression' or short_circuit:
            return self.conditional(node, context, depth)
        if kind == 'if_statement':
            condition = node.child_by_field_name('condition')
            before = self.read(condition, context, depth + 1)
            choice = self.branch(node, 'if',
                [('истина', node.child_by_field_name('consequence'), False),
                 ('ложь', node.child_by_field_name('alternative'), False)], context, depth,
                 label=self.label(condition))
            if choice and any(step['kind'] == 'branch' for step in before):
                choice[0].update(conditionResult='reuse', conditionExpression=self.label(condition))
            return before + choice
        if kind in {'for_statement', 'for_in_statement', 'while_statement', 'do_statement'}:
            return self.loop(node, context, depth)
        if kind in {'return_statement', 'throw_statement', 'break_statement', 'continue_statement'}:
            terminal = kind.removesuffix('_statement')
            if terminal in {'break', 'continue'} and node.named_children:
                return self.opaque(node, 'labeled break/continue: цель выхода не представлена.')
            before = self.sequence(node.named_children, context, depth)
            item = self.step(terminal, node)
            if item:
                self.terminals.append({'kind': terminal, **site(node), 'branchIds': list(context)})
            return before + ([item] if item else [])
        if kind in {'lexical_declaration', 'variable_declaration'}:
            return self.sequence(node.named_children, context, depth)
        if kind == 'variable_declarator':
            binding = node.child_by_field_name('name')
            if binding is not None and binding.type not in {'identifier'}:
                return self.opaque(node, 'Destructuring binding: defaults/computed keys/iterator effects не раскрыты.')
            result = self.read(node.child_by_field_name('value'), context, depth + 1)
            item = self.step('statement', node, operationKind='declaration')
            return result + ([item] if item else [])
        if kind in {'assignment_expression', 'augmented_assignment_expression'}:
            left = node.child_by_field_name('left')
            if left is not None and left.type in {'array_pattern', 'object_pattern'}:
                return self.opaque(node, 'Destructuring assignment: порядок binding effects не раскрыт.')
            if kind == 'augmented_assignment_expression' and any(child.type in {'&&=', '||=', '??='} for child in node.children):
                return self.opaque(node, 'Logical assignment: условный setter/RHS не раскрыты.')
            before = self.read(left, context, depth + 1)
            before.extend(self.read(node.child_by_field_name('right'), context, depth + 1))
            item = self.step('statement', node, operationKind='assignment')
            return before + ([item] if item else [])
        if kind == 'new_expression':
            return self.construct(node, context, depth)
        if kind in {'object', 'array', 'arguments', 'sequence_expression', 'binary_expression',
                    'unary_expression', 'template_string', 'template_substitution', 'pair', 'computed_property_name',
                    'spread_element'}:
            return self.sequence(node.named_children, context, depth)
        if kind in {'update_expression', 'debugger_statement'}:
            before = self.sequence(node.named_children, context, depth)
            item = self.step('statement', node)
            return before + ([item] if item else [])
        if kind in {'string_fragment', 'escape_sequence'}:
            return []
        return self.opaque(node, f'Не извлечена конструкция JavaScript: {kind}.')

    def model(self, steps):
        """Возвращает совместимую с UI модель с явными пределами и статической границей."""
        return {'version': 3, 'status': 'parsed', 'steps': steps, 'truncated': self.truncated,
                'unsupported': list(self.unsupported), 'sourceKind': 'working-tree-ast',
                'limits': {'maxAstNodes': 20000, 'maxSteps': MAX_STEPS, 'maxDepth': MAX_DEPTH,
                           'maxEvents': self.max_events, 'maxLabelChars': MAX_LABEL}, 'warning': WARNING}
