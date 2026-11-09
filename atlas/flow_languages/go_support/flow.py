"""Ограниченное дерево Go: порядок вычисления, выборы и явные неизвестные границы."""

MAX_AST_NODES = 20000
MAX_STEPS = 180
MAX_DEPTH = 24
TYPE_NODES = {'array_type', 'slice_type', 'pointer_type', 'map_type', 'channel_type',
              'function_type', 'struct_type', 'interface_type', 'generic_type', 'type_arguments',
              'implicit_length_array_type'}


class FlowBuilder:
    """Строит события вместе с шагами: один callsite имеет один точный AST-диапазон."""

    def __init__(self, *, max_events):
        """Фиксирует независимые бюджеты событий и видимых шагов."""
        self.max_events = max(0, max_events)
        self.events = []
        self.events_truncated = False
        self.truncated = False
        self.unsupported = []
        self.branches = []
        self.by_branch = {}
        self.terminals = []
        self.count = 0

    def text(self, node):
        """Ограничивает все подписи одним общим лимитом."""
        return node.text.decode('utf-8')[:400] if node is not None else ''

    def site(self, node):
        """Возвращает 1-based UTF-8 byte columns, как у host Tree-sitter callsites."""
        return {'line': node.start_point.row + 1, 'column': node.start_point.column + 1,
                'endLine': node.end_point.row + 1, 'endColumn': node.end_point.column + 1}

    def note(self, message):
        """Не размножает одинаковые bounded причины неполноты."""
        if message not in self.unsupported and len(self.unsupported) < 30:
            self.unsupported.append(message)

    def step(self, kind, node, **fields):
        """Резервирует один шаг; ни один вложенный контейнер не обходит бюджет."""
        if self.count >= MAX_STEPS:
            self.truncated = True
            self.note('Go methodFlow step budget exceeded.')
            return None
        self.count += 1
        return {'kind': kind, 'label': self.text(node), **self.site(node),
                'sourcePreviewTruncated': len(node.text.decode('utf-8')) > 400, **fields}

    def opaque(self, node, reason, *, continuing=False):
        """Показывает исходный синтаксис, не раскрывая неустановленный путь его тела."""
        self.note(reason)
        step = self.step('statement' if continuing else 'unknown', node, incomplete=True)
        return [step] if step else []

    def branch(self, node, kind, label, context):
        """Регистрирует решение до извлечения его условных arm-событий."""
        step = self.step('branch', node, label=label[:400], controlKind=kind, arms=[])
        if step:
            identifier = 'go-branch-' + str(len(self.branches) + 1)
            step['branchId'] = identifier
            record_kind = 'if' if kind in {'&&', '||', 'case-test'} else kind
            record = {'id': identifier, 'kind': record_kind, 'label': label[:400],
                      'condition': label[:400], 'selector': label[:400], 'arms': [],
                      **self.site(node), 'parentArmIds': list(context)}
            self.branches.append(record)
            self.by_branch[identifier] = record
        return step

    def arm_id(self, owner, index):
        """Общий renderer использует в branchIds именно arm IDs, а не ID решения."""
        return owner['branchId'] + '-a' + str(index)

    def arm(self, label, steps, owner, index, *, source=None):
        """Публикует общий controlFlow arm вместе с совместимым methodFlow arm."""
        identifier = self.arm_id(owner, index)
        location = self.site(source) if source is not None else {
            'line': owner['line'], 'endLine': owner['endLine']}
        truth = index == 0
        if owner.get('controlKind') == '||':
            truth = not truth
        record = {'id': identifier, 'kind': 'true' if truth else 'false',
                  'label': label[:400], 'condition': None, 'labels': [], **location}
        self.by_branch[owner['branchId']]['arms'].append(record)
        return {'id': identifier, 'label': label[:400], 'steps': steps,
                'emptyReason': ('not-extracted' if self.truncated else 'skip') if not steps else None}

    def extract(self, node, depth=0, context=(), conditional=False):
        """Сохраняет управляющие конструкции; вложенные func_literal не обходятся."""
        if node is None or node.type in {'comment', 'empty_statement'}:
            return []
        if self.count >= MAX_STEPS:
            self.truncated = True
            self.note('Go methodFlow step budget exceeded.')
            return []
        if depth >= MAX_DEPTH:
            self.truncated = True
            return self.opaque(node, 'Go methodFlow depth budget exceeded.')
        kind = node.type
        if kind in {'block', 'statement_list'}:
            result = []
            for child in node.named_children:
                result.extend(self.extract(child, depth + 1, context, conditional))
            return result
        if kind == 'if_statement':
            return self.if_statement(node, depth, context, conditional)
        if kind == 'for_statement':
            return self.loop(node, depth, context, conditional)
        if kind in {'expression_switch_statement', 'type_switch_statement'}:
            return self.switch(node, depth, context, conditional)
        if kind in {'select_statement', 'send_statement'}:
            return self.opaque(node, 'Go select/channel communication may block; scheduling and communication paths are not expanded.')
        if kind in {'defer_statement', 'go_statement'}:
            return self.deferred(node, depth, context, conditional)
        if kind in {'goto_statement', 'labeled_statement', 'fallthrough_statement'}:
            return self.opaque(node, 'Go goto/labels/fallthrough need nonlocal control-flow targets and are not expanded.')
        if kind in {'return_statement', 'break_statement', 'continue_statement'}:
            if kind != 'return_statement' and node.named_children:
                return self.opaque(node, 'Go labeled jumps are not expanded.')
            result = self.expression_children(node, depth, context, conditional)
            step = self.step(kind.removesuffix('_statement'), node)
            if step:
                result.append(step)
                self.terminals.append({**step, 'text': step['label'], 'branchIds': list(context)})
            return result
        if kind in {'expression_statement', 'short_var_declaration', 'assignment_statement',
                    'var_declaration', 'const_declaration', 'inc_statement', 'dec_statement'}:
            if self.contains(node, {'unary_expression'}, receive_only=True):
                return self.opaque(node, 'Go channel receive may block; enclosing expression is not expanded.')
            result = self.expression_children(node, depth, context, conditional)
            if kind != 'expression_statement' or not result:
                step = self.step('statement', node)
                if step:
                    result.append(step)
            return result
        if kind == 'type_declaration':
            step = self.step('statement', node)
            return [step] if step else []
        return self.opaque(node, 'Unsupported Go statement: ' + kind)

    def contains(self, node, kinds, *, receive_only=False):
        """Ищет барьер в текущем теле, исключая отложенные function literal bodies."""
        pending = [node]
        while pending:
            item = pending.pop()
            if item.type == 'func_literal':
                continue
            if item.type in kinds:
                if not receive_only or self.text(item.child_by_field_name('operator')) == '<-':
                    return True
            pending.extend(item.named_children)
        return False

    def literal_function(self, node):
        """Разворачивает только скобки callable, не исполняя closure или возвращённую функцию."""
        while node is not None and node.type == 'parenthesized_expression' and node.named_children:
            node = node.named_children[0]
        return node is not None and node.type == 'func_literal'

    def expression_children(self, node, depth, context, conditional):
        """Обходит операнды слева направо; expression() владеет calls и short-circuit."""
        result = []
        for child in node.named_children:
            result.extend(self.expression(child, depth + 1, context, conditional))
        return result

    def expression(self, node, depth, context, conditional):
        """Вложенные вызовы предшествуют внешнему, &&/|| сохраняют условный правый операнд."""
        if node is None:
            return []
        if depth >= MAX_DEPTH:
            self.truncated = True
            return self.opaque(node, 'Go methodFlow depth budget exceeded.')
        if self.count >= MAX_STEPS:
            self.truncated = True
            self.note('Go methodFlow step budget exceeded.')
            return []
        if node.type == 'func_literal':
            self.note('Go function literal body is deferred and excluded from the enclosing method.')
            return []
        if node.type in TYPE_NODES:
            # Go array lengths and nested type expressions are compile-time syntax, not runtime calls.
            return []
        if node.type == 'type_conversion_expression':
            result = self.expression(node.child_by_field_name('operand'), depth + 1, context, conditional)
            result.extend(self.opaque(node, 'Go conversion/generic invocation is ambiguous without type information; target is not guessed.'))
            return result
        if self.contains(node, {'unary_expression'}, receive_only=True):
            return self.opaque(node, 'Go channel receive may block; enclosing expression is not expanded.')
        if node.type == 'binary_expression':
            operator = self.text(node.child_by_field_name('operator'))
            if operator in {'&&', '||'}:
                result = self.expression(node.child_by_field_name('left'), depth + 1, context, conditional)
                branch = self.branch(node, operator, self.text(node.child_by_field_name('left')), context)
                if branch:
                    nested = (*context, self.arm_id(branch, 0))
                    right = self.expression(node.child_by_field_name('right'), depth + 1, nested, True)
                    branch['arms'] = [self.arm('evaluate right' if operator == '&&' else 'left is false', right, branch, 0),
                                      self.arm('skip right', [], branch, 1)]
                    result.append(branch)
                return result
        if node.type == 'call_expression':
            function = node.child_by_field_name('function')
            result = self.expression_children(node, depth, context, conditional)
            if self.literal_function(function):
                result.extend(self.opaque(node, 'Go immediately invoked function literal body is not expanded.'))
                return result
            step = self.step('call', node, label=(self.text(function) + '()')[:400], site=self.site(node),
                             sourceExpression=self.text(node))
            if step:
                result.append(step)
                event = {'kind': 'call', 'text': step['label'],
                         'callee': self.text(function).rsplit('.', 1)[-1], **self.site(node),
                         'sourceExpression': self.text(node), 'targetNodeIds': [], 'confidence': 'source',
                         'sourceExpressionTruncated': len(node.text.decode('utf-8')) > 400,
                         'conditional': conditional, 'awaited': False, 'parallel': False,
                         'branchIds': list(context), 'argumentBinding': 'lexical-only'}
                arguments = node.child_by_field_name('arguments')
                event['arguments'] = [{'index': index, 'expression': self.text(item),
                                       'expressionTruncated': len(item.text.decode('utf-8')) > 400}
                                      for index, item in enumerate(arguments.named_children if arguments else [])][:40]
                event['argumentsTruncated'] = bool(arguments and len(arguments.named_children) > 40)
                if len(self.events) < self.max_events:
                    self.events.append(event)
                else:
                    self.events_truncated = True
                    self.note('Go call event budget exceeded.')
                if self.text(function) == 'panic':
                    result.extend(self.opaque(node, 'Go panic may unwind the stack; builtin shadowing and recover paths are unknown.'))
            return result
        return self.expression_children(node, depth, context, conditional)

    def if_statement(self, node, depth, context, conditional):
        """Выполняет initializer/condition до выбора единственного arm."""
        result = self.extract(node.child_by_field_name('initializer'), depth + 1, context, conditional)
        condition = node.child_by_field_name('condition')
        result.extend(self.expression(condition, depth + 1, context, conditional))
        branch = self.branch(node, 'if', self.text(condition), context)
        if branch:
            if any(step['kind'] == 'branch' for step in result):
                branch.update(conditionResult='reuse', conditionExpression=self.text(condition))
            yes_context = (*context, self.arm_id(branch, 0))
            no_context = (*context, self.arm_id(branch, 1))
            yes = self.extract(node.child_by_field_name('consequence'), depth + 1, yes_context, True)
            no = self.extract(node.child_by_field_name('alternative'), depth + 1, no_context, True)
            branch['arms'] = [self.arm('true', yes, branch, 0, source=node.child_by_field_name('consequence')),
                              self.arm('false', no, branch, 1, source=node.child_by_field_name('alternative'))]
            result.append(branch)
        return result

    def loop(self, node, depth, context, conditional):
        """Хранит repeated condition/body/update; range expression вычисляется один раз."""
        body = node.child_by_field_name('body')
        clause = next((item for item in node.named_children if item != body and item.type != 'comment'), None)
        result, condition, update = [], None, None
        control_kind = 'while'
        if clause is not None and clause.type == 'for_clause':
            control_kind = 'for'
            result = self.extract(clause.child_by_field_name('initializer'), depth + 1, context, conditional)
            condition, update = clause.child_by_field_name('condition'), clause.child_by_field_name('update')
        elif clause is not None and clause.type == 'range_clause':
            control_kind = 'foreach'
            iterable = clause.child_by_field_name('right')
            left = clause.child_by_field_name('left')
            arity = len(left.named_children) if left is not None else 0
            # Go's one-variable constant-length array/pointer exception cannot be resolved without types.
            if arity < 2 and iterable is not None and iterable.type not in {'identifier', 'selector_expression'}:
                return self.opaque(node, 'Go range over an expression needs type/arity semantics (constant-length arrays and iterator functions); not expanded.')
            if arity >= 2:
                result.extend(self.expression(iterable, depth + 1, context, conditional))
            self.note('Go range iteration order and iterator/channel semantics are not inferred.')
        else:
            condition = clause
        header = node.text[:body.start_byte - node.start_byte].decode('utf-8').strip()[:400]
        step = self.step('loop', node, label=header, controlKind=control_kind, condition=[], body=[], update=[],
                         canExit=condition is not None or control_kind == 'foreach')
        if step:
            identifier = 'go-branch-' + str(len(self.branches) + 1)
            step['branchId'] = identifier
            record = {'id': identifier, 'kind': control_kind, 'label': header, 'header': header,
                      'condition': self.text(condition), 'arms': [],
                      **self.site(node), 'parentArmIds': list(context)}
            self.branches.append(record)
            self.by_branch[identifier] = record
            nested = (*context, self.arm_id(step, 0))
            step['condition'] = self.expression(condition, depth + 1, context, True)
            step['body'] = self.extract(body, depth + 1, nested, True)
            step['update'] = self.extract(update, depth + 1, nested, True)
            self.arm('body', step['body'], step, 0, source=body)
            result.append(step)
        return result

    def switch(self, node, depth, context, conditional):
        """Сохраняет ленивые case-тесты; fallthrough оставляет весь switch непрочитанным."""
        if self.contains(node, {'fallthrough_statement'}):
            return self.opaque(node, 'Go switch with fallthrough is not expanded; shared case transfer is unknown.')
        result = self.extract(node.child_by_field_name('initializer'), depth + 1, context, conditional)
        result.extend(self.expression(node.child_by_field_name('value'), depth + 1, context, conditional))
        cases = [item for item in node.named_children if item.type in {'expression_case', 'type_case'}]
        default = next((item for item in node.named_children if item.type == 'default_case'), None)
        result.extend(self.case_chain(cases, default, depth + 1, context, conditional,
                                      type_switch=node.type == 'type_switch_statement'))
        return result

    def case_chain(self, cases, default, depth, context, conditional, *, type_switch):
        """Поздние case выражения доступны только после несовпадения ранних."""
        if depth >= MAX_DEPTH:
            self.truncated = True
            return self.opaque(cases[0] if cases else default, 'Go switch case depth budget exceeded.') if cases or default else []
        if not cases:
            if default is None:
                return []
            branch = self.branch(default, 'switch', 'default', context)
            if branch is None:
                return []
            statements = next((item for item in default.named_children if item.type == 'statement_list'), None)
            nested = (*context, self.arm_id(branch, 0))
            body = self.extract(statements, depth + 1, nested, True)
            branch['arms'] = [self.arm('default', body, branch, 0, source=statements)]
            return [branch]
        case = cases[0]
        values = case.child_by_field_name('value')
        result = self.case_values(values.named_children if values else [], depth, context, conditional)
        branch = self.branch(case, 'switch', self.text(case).split(':', 1)[0], context)
        if branch:
            branch['switchKind'] = 'type-switch' if type_switch else 'expression-switch'
            branch.update(conditionResult='reuse', conditionExpression=branch['label'])
            yes_context = (*context, self.arm_id(branch, 0))
            no_context = (*context, self.arm_id(branch, 1))
            statements = next((item for item in case.named_children if item.type == 'statement_list'), None)
            yes = self.extract(statements, depth + 1, yes_context, True)
            no = self.case_chain(cases[1:], default, depth + 1, no_context, True, type_switch=type_switch)
            branch['arms'] = [self.arm('match', yes, branch, 0, source=statements),
                              self.arm('no match', no, branch, 1)]
            result.append(branch)
        return result

    def case_values(self, values, depth, context, conditional):
        """Запятая в case задаёт короткое замыкание по первому совпадению."""
        if not values:
            return []
        result = self.expression(values[0], depth + 1, context, conditional)
        if len(values) > 1:
            if depth >= MAX_DEPTH:
                self.truncated = True
                self.note('Go switch case depth budget exceeded.')
                return result
            branch = self.branch(values[0], 'case-test', 'previous case value did not match', context)
            if branch:
                nested = (*context, self.arm_id(branch, 0))
                rest = self.case_values(values[1:], depth + 1, nested, True)
                branch['arms'] = [self.arm('no match', rest, branch, 0),
                                  self.arm('match: skip other tests', [], branch, 1)]
                result.append(branch)
        return result

    def deferred(self, node, depth, context, conditional):
        """Аргументы defer/go вычисляются сейчас; сам вызов и closure body не линеаризуются."""
        call = next((item for item in node.named_children if item.type in {'call_expression', 'type_conversion_expression'}), None)
        result = []
        if call is not None and call.type == 'type_conversion_expression':
            # This grammar uses the same node for a one-argument generic call and conversion.
            result = self.expression(call.child_by_field_name('operand'), depth + 1, context, conditional)
            self.note('Go deferred generic invocation target is ambiguous without type information.')
        elif call is not None:
            result = self.expression_children(call, depth, context, conditional)
        result.extend(self.opaque(node, 'Go ' + node.type.removesuffix('_statement') +
                                  ': arguments evaluated here; invocation, deferred execution and runtime scheduling are not expanded.',
                                  continuing=True))
        return result

    def control_flow(self):
        """Публикует реальные решения и терминалы с общей границей неполноты."""
        return {'branches': self.branches, 'terminals': self.terminals,
                'truncated': self.truncated, 'unsupported': list(self.unsupported)}
