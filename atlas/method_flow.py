"""Bounded C# statement/evaluation trees, separate from graph identity and runtime traces."""
MAX_STEPS = 180
MAX_DEPTH = 24
VERSION = 'csharp-method-flow-v3'
DEFERRED = {'lambda_expression', 'anonymous_method_expression', 'local_function_statement'}


class MethodFlow:
    """Reuses the parsed AST and extracted call sites; never evaluates values or chooses branches."""
    def __init__(self, content, events):
        self.content = content
        self.events = {(e['line'], e['column'], e.get('endLine'), e.get('endColumn')): e
                       for e in events if e['kind'] in {'call', 'log', 'span'}}
        self.count = 0
        self.truncated = False
        self.unsupported = set()

    def text(self, node):
        """Keep labels bounded while locations point to the complete source."""
        return self.content[node.start_byte:node.end_byte].decode('utf-8')[:400] if node else ''

    def step(self, node, kind, **facts):
        """One shared node budget covers expressions, statements, arms and nested controls."""
        if self.count >= MAX_STEPS:
            self.truncated = True
            return None
        self.count += 1
        return {'kind': kind, 'line': node.start_point.row + 1, 'column': node.start_point.column + 1,
                'label': self.text(node), **facts}

    def value_arm(self, node, label, depth):
        """Keep literal/property outcomes visible; calls already describe their own evaluation."""
        steps = self.expression(node, depth)
        if not steps and node is not None and not self.truncated:
            value = self.step(node, 'value', label='Значение', expression=self.text(node))
            if value:
                steps.append(value)
        return {'label': label, 'steps': steps, 'valueExpression': self.text(node)}

    def statement_arm(self, node, label, depth):
        """Distinguish absent/empty branches from source statements the reader could not extract."""
        steps = self.statements(node, depth)
        empty = node is None or node.type == 'empty_statement' or node.type == 'block' and all(c.type in {'comment', 'empty_statement'} for c in node.named_children)
        arm = {'label': label, 'steps': steps}
        if not steps:
            arm['emptyReason'] = 'skip' if empty else 'not-extracted'
            if not empty:
                self.unsupported.add('branch statements are not represented')
                arm.update(line=node.start_point.row + 1, column=node.start_point.column + 1)
        return arm

    def expression(self, node, depth=0):
        """C# receiver/arguments evaluate before the containing call; deferred bodies stay deferred."""
        if node is None:
            return []
        if self.count >= MAX_STEPS:
            self.truncated = True
            return []
        if depth >= MAX_DEPTH:
            self.truncated = True
            return []
        if node.type in DEFERRED:
            return []
        if node.type == 'variable_declaration':
            return self.declaration(node, depth + 1)
        if node.type == 'switch_expression':
            selector = node.named_children[0]
            result = self.expression(selector, depth + 1)
            arms = []
            for arm in node.named_children[1:]:
                if arm.type != 'switch_expression_arm':
                    continue
                arrow = next(c for c in arm.children if c.type == '=>')
                label = self.content[arm.start_byte:arrow.start_byte].decode('utf-8')[:200]
                arms.append(self.value_arm(arm.named_children[-1], label, depth + 1))
                if any(c.type == 'when_clause' for c in arm.named_children):
                    self.unsupported.add('switch pattern guards require ordered match analysis')
            if not any(a['label'].strip() == '_' for a in arms):
                failure = self.step(node, 'throw', label='SwitchExpressionException')
                arms.append({'label': 'нет подходящего образца', 'steps': [failure] if failure else []})
            item = self.step(node, 'branch', label=self.text(selector), controlKind='switch-expression', arms=arms)
            return result + ([item] if item else [])
        if node.type == 'conditional_expression':
            condition = node.child_by_field_name('condition')
            result = self.expression(condition, depth + 1)
            arms = [self.value_arm(node.child_by_field_name(field), label, depth + 1)
                    for field, label in [('consequence', 'истина'), ('alternative', 'иначе')]]
            item = self.step(node, 'branch', label=self.text(condition), controlKind='conditional', arms=arms)
            return result + ([item] if item else [])
        if node.type in {'binary_expression', 'conditional_access_expression'}:
            operator = node.child_by_field_name('operator')
            token = self.text(operator)
            if token in {'&&', '||', '??'}:
                left, right = node.child_by_field_name('left'), node.child_by_field_name('right')
                result = self.expression(left, depth + 1)
                label = {'&&': 'если левая часть истинна', '||': 'если левая часть ложна', '??': 'если левая часть равна null'}[token]
                right_arm = self.value_arm(right, label, depth + 1)
                skip = {'label': 'левая часть ложна; правая не вычисляется' if token == '&&'
                        else 'левая часть истинна; правая не вычисляется', 'steps': [], 'emptyReason': 'skip'}
                if token == '??':
                    value = self.step(left, 'value', label='Левое значение', expression=self.text(left), reuseResult=True)
                    skip = {'label': 'левая часть задана', 'steps': [value] if value else [], 'valueExpression': self.text(left)}
                item = self.step(node, 'branch', label=self.text(left), controlKind=token, arms=[right_arm, skip])
                return result + ([item] if item else [])
            if node.type == 'conditional_access_expression':
                receiver = node.child_by_field_name('condition') or node.named_children[0]
                result = self.expression(receiver, depth + 1)
                tail = []
                for child in node.named_children:
                    if child.id != receiver.id:
                        tail.extend(self.expression(child, depth + 1))
                if not tail:
                    # Show the accessed value, not a second evaluation of the receiver.
                    expression = self.text(node).replace('?', '', 1)
                    value = self.step(node, 'value', label='Взять значение', expression=expression, reuseResult=True)
                    if value:
                        tail.append(value)
                null_value = self.step(node, 'value', label='Значение', expression='null')
                item = self.step(node, 'branch', label=self.text(receiver) + ' != null', controlKind='?.',
                                 arms=[{'label': 'значение задано', 'steps': tail}, {'label': 'null', 'steps': [null_value] if null_value else []}])
                return result + ([item] if item else [])
        function = node.child_by_field_name('function') if node.type == 'invocation_expression' else None
        if function is not None and function.type == 'conditional_access_expression':
            # The argument expressions and call are skipped together when the receiver is null.
            receiver = function.child_by_field_name('condition')
            result = self.expression(receiver, depth + 1)
            tail = self.expression(node.child_by_field_name('arguments'), depth + 1)
            item = self.call(node)
            if item:
                tail.append(item)
            branch = self.step(node, 'branch', label=self.text(receiver) + ' != null', controlKind='?.',
                               arms=[{'label': 'значение задано', 'steps': tail}, {'label': 'null: вызов пропущен', 'steps': [], 'emptyReason': 'skip'}])
            return result + ([branch] if branch else [])
        result = []
        for child in node.named_children:
            result.extend(self.expression(child, depth + 1))
        if node.type in {'invocation_expression', 'constructor_initializer'}:
            chained_optional = function is not None and function.type == 'member_access_expression' and self.has_optional(function)
            if chained_optional:
                self.unsupported.add('chained conditional access requires lifted receiver analysis')
                item = self.step(node, 'unknown', label='Условная цепочка: ' + self.text(node))
            else:
                item = self.call(node)
            if item:
                result.append(item)
        elif node.type == 'await_expression':
            item = self.step(node, 'await', label='Дождаться результата')
            if item:
                result.append(item)
        elif node.type == 'throw_expression':
            item = self.step(node, 'throw')
            if item:
                result.append(item)
        elif self.changes_value(node):
            item = self.step(node, 'operation')
            if item:
                result.append(item)
        return result

    def changes_value(self, node):
        """Expose assignment and increment steps, especially the update reached by a for-continue."""
        if node.type == 'assignment_expression':
            return True
        if node.type not in {'prefix_unary_expression', 'postfix_unary_expression'}:
            return False
        token = self.text(node).strip()
        return token.startswith(('++', '--')) or token.endswith(('++', '--'))

    def declaration(self, node, depth):
        """Keep local setup in source order without repeating a direct initializer call row."""
        result = []
        declarators = [c for c in node.named_children if c.type == 'variable_declarator']
        type_label = self.text(node.child_by_field_name('type'))
        for declarator in declarators:
            name = declarator.child_by_field_name('name')
            if name is None:
                name = next((c for c in declarator.named_children if c.type == 'tuple_pattern'), None)
            equals = next((i for i, c in enumerate(declarator.children) if c.type == '='), None)
            initializer = None
            if equals is not None:
                initializer = next((c for c in declarator.children[equals + 1:] if c.is_named), None)
            evaluation = self.expression(initializer, depth + 1)
            result.extend(evaluation)
            # A sole direct call already displays the complete declaration via
            # sourceStatement. Selection, multiple bindings and literal setup
            # need an explicit row after their initializer evaluations.
            represented = any(s.get('sourceStatement') for s in evaluation if s['kind'] == 'call')
            if represented:
                continue
            parent = node.parent
            label = f'{type_label} {self.text(declarator)}'.strip()
            if len(declarators) == 1 and parent is not None and parent.type == 'local_declaration_statement':
                label = self.text(parent)
            item = self.step(declarator, 'operation', label=label, operationKind='declaration',
                             variable=self.text(name), initializer=self.text(initializer))
            if item:
                result.append(item)
        return result

    def condition_facts(self, expression, steps):
        """Separate evaluation of a conditional value from testing its already computed result."""
        if any(s['kind'] == 'branch' for s in steps):
            return {'conditionResult': 'reuse', 'conditionExpression': self.text(expression)}
        return {}

    def expression_body(self, declaration, body):
        """Expose the implicit exit of a method arrow body; void/constructors return no value."""
        expression = next((c for c in body.named_children if c.type != 'comment'), None)
        result = self.expression(expression)
        if expression is None or expression.type == 'throw_expression':
            return result
        return_type = declaration.child_by_field_name('returns')
        returns = self.text(return_type)
        async_method = any(c.type == 'modifier' and self.text(c) == 'async' for c in declaration.named_children)
        task_name = returns.replace('global::', '')
        no_async_value = async_method and task_name in {'Task', 'ValueTask', 'System.Threading.Tasks.Task', 'System.Threading.Tasks.ValueTask'}
        returns_value = declaration.type == 'method_declaration' and bool(returns) and returns != 'void' and not no_async_value
        task_base = task_name.split('<', 1)[0].strip()
        known_async_type = task_base in {'void', 'Task', 'ValueTask', 'System.Threading.Tasks.Task', 'System.Threading.Tasks.ValueTask'}
        if async_method and (not known_async_type or self.return_type_is_alias(declaration, task_base)):
            self.unsupported.add('async return aliases and custom task-like types require result-type binding')
            returns_value = None
        returns_reference = return_type is not None and return_type.type == 'ref_type'
        reuse = bool(returns_value and result)
        label = 'Завершить метод'
        if returns_value:
            label = 'return ' + self.text(expression) + ';'
            if reuse:
                label = 'Вернуть ссылку на результат выражения' if returns_reference else 'Вернуть результат выражения'
        item = self.step(expression, 'return', label=label, implicitReturn=True, returnsValue=returns_value,
                         returnsReference=returns_reference, reuseExpressionResult=reuse,
                         expression=self.text(expression), sourceStatement=self.text(body))
        if item:
            result.append(item)
        return result

    def return_type_is_alias(self, declaration, type_name):
        """Detect source-visible aliases without guessing their resolved async result contract."""
        alias_name = type_name.split('.', 1)[0]
        scope = declaration.parent
        while scope is not None:
            for child in scope.named_children:
                if child.type == 'using_directive' and self.text(child.child_by_field_name('name')) == alias_name:
                    return True
            scope = scope.parent
        return False

    def has_optional(self, node):
        """Inspect the bounded receiver tree without treating optional chains as unconditional calls."""
        pending = [node]
        while pending:
            current = pending.pop()
            if current.type == 'conditional_access_expression':
                return True
            if current.type == 'parenthesized_expression':
                continue
            pending.extend(current.named_children)
        return False

    def call(self, node):
        """End coordinates distinguish chained calls which share a starting line and column."""
        point = node.start_point.row + 1, node.start_point.column + 1
        end = node.end_point.row + 1, node.end_point.column + 1
        event = self.events.get((*point, *end))
        if event is None:
            self.unsupported.add('call extraction is incomplete')
        return self.step(node, 'call' if event else 'unknown', label=event.get('text') if event else 'Вызов не извлечён',
                         site={'line': point[0], 'column': point[1], 'endLine': end[0], 'endColumn': end[1]},
                         sourceStatement=self.initializer_statement(node),
                         directAwait=bool(node.parent and node.parent.type == 'await_expression'), parallel=bool(event and event.get('parallel')))

    def initializer_statement(self, node):
        """A direct initializer keeps its variable/type in a code row without inventing an extra call."""
        parent = node.parent
        while parent is not None and parent.type in {'await_expression', 'parenthesized_expression'}:
            parent = parent.parent
        if parent is None or parent.type != 'variable_declarator':
            return ''
        declaration = parent.parent
        if declaration is None or declaration.type != 'variable_declaration':
            return ''
        if sum(c.type == 'variable_declarator' for c in declaration.named_children) != 1:
            return ''
        statement = declaration.parent
        return self.text(statement) if statement is not None and statement.type == 'local_declaration_statement' else ''

    def statements(self, node, depth=0):
        """Preserve statement order and lexical alternatives without turning catches into subsequent steps."""
        if node is None:
            return []
        if self.count >= MAX_STEPS:
            self.truncated = True
            return []
        if depth >= MAX_DEPTH:
            self.truncated = True
            return []
        kind = node.type
        if kind in DEFERRED or kind == 'comment':
            return []
        if kind in {'block', 'switch_section'}:
            result = []
            for child in node.named_children:
                result.extend(self.statements(child, depth + 1))
            return result
        if kind == 'if_statement':
            condition = node.child_by_field_name('condition')
            result = self.expression(condition)
            arms = [self.statement_arm(node.child_by_field_name(field), label, depth + 1)
                    for field, label in [('consequence', 'истина'), ('alternative', 'иначе')]]
            item = self.step(node, 'branch', label=self.text(condition), controlKind='if', arms=arms,
                             **self.condition_facts(condition, result))
            return result + ([item] if item else [])
        loops = {'for_statement': 'for', 'foreach_statement': 'foreach', 'while_statement': 'while', 'do_statement': 'do'}
        if kind in loops:
            body = node.child_by_field_name('body')
            condition = node.child_by_field_name('condition')
            initializers = node.children_by_field_name('initializer') or node.children_by_field_name('right')
            result = []
            for initializer in initializers:
                result.extend(self.expression(initializer))
            updates = []
            for update in node.children_by_field_name('update'):
                updates.extend(self.expression(update))
            body_arm = self.statement_arm(body, 'тело цикла', depth + 1)
            condition_steps = self.expression(condition)
            item = self.step(node, 'loop', label=self.text(node).split('{', 1)[0].strip(), controlKind=loops[kind],
                             condition=condition_steps, body=body_arm['steps'], bodyEmptyReason=body_arm.get('emptyReason'), update=updates,
                             **self.condition_facts(condition, condition_steps),
                             canExit=condition is not None or kind == 'foreach_statement')
            return result + ([item] if item else [])
        if kind == 'switch_statement':
            selector = node.child_by_field_name('value')
            result = self.expression(selector)
            body = node.child_by_field_name('body')
            arms, labels = [], []
            for section in body.named_children if body else []:
                if section.type != 'switch_section':
                    continue
                colon = next((c for c in section.children if c.type == ':'), None)
                label = self.content[section.start_byte:colon.start_byte].decode('utf-8')[:200] if colon else 'case'
                labels.append(label)
                statements = [c for c in section.named_children if c.type.endswith('_statement') or c.type == 'block']
                if statements:
                    steps = []
                    for statement in statements:
                        steps.extend(self.statements(statement, depth + 1))
                    arms.append({'label': ' / '.join(labels), 'steps': steps})
                    labels = []
                if any(c.type == 'when_clause' for c in section.named_children):
                    self.unsupported.add('switch pattern guards require ordered match analysis')
            if labels:
                arms.append({'label': ' / '.join(labels), 'steps': []})
            if not any('default' in a['label'] for a in arms):
                arms.append({'label': 'нет совпадения', 'steps': []})
            item = self.step(node, 'branch', label=self.text(selector), controlKind='switch', arms=arms)
            return result + ([item] if item else [])
        if kind == 'try_statement':
            arms = [{'label': 'обычное выполнение', 'steps': self.statements(node.child_by_field_name('body'), depth + 1)}]
            for child in node.named_children:
                if child.type == 'catch_clause':
                    body = child.child_by_field_name('body')
                    label = self.content[child.start_byte:body.start_byte].decode('utf-8')[:200] if body else 'catch'
                    arms.append({'label': label, 'steps': self.statements(body, depth + 1), 'exception': True})
            final = next((c for c in node.named_children if c.type == 'finally_clause'), None)
            if final:
                self.unsupported.add('finally transitions require pending-exit analysis')
                arms.append({'label': 'finally: область завершения, переходы не установлены',
                             'steps': self.statements(final.child_by_field_name('body'), depth + 1), 'exception': True})
            item = self.step(node, 'branch', label='try / обработка исключений', controlKind='try', arms=arms, incomplete=bool(final))
            return [item] if item else []
        terminals = {'return_statement': 'return', 'throw_statement': 'throw', 'break_statement': 'break', 'continue_statement': 'continue'}
        if kind in terminals:
            result = self.expression(node)
            item = self.step(node, terminals[kind])
            return result + ([item] if item else [])
        if kind in {'goto_statement', 'yield_statement'}:
            self.unsupported.add(kind)
            item = self.step(node, 'unknown', label=self.text(node))
            return [item] if item else []
        # Wrappers such as using/checked/lock preserve their lexical body. Their
        # cleanup/exception effects are not advertised as a computed runtime CFG.
        body = node.child_by_field_name('body')
        if body is not None and kind.endswith('_statement'):
            result = []
            for child in node.named_children:
                if child.id != body.id:
                    result.extend(self.expression(child))
            return result + self.statements(body, depth + 1)
        return self.expression(node)


def analyze_method_flow(declaration, content, events):
    """Return a bounded, source-derived reading tree with explicit incomplete control semantics."""
    builder = MethodFlow(content, events)
    if declaration.has_error:
        builder.unsupported.add('source has parse errors')
    steps = []
    initializer = next((c for c in declaration.named_children if c.type == 'constructor_initializer'), None)
    if initializer:
        steps.extend(builder.expression(initializer))
    body = declaration.child_by_field_name('body') or next((c for c in declaration.named_children if c.type == 'arrow_expression_clause'), None)
    if body is not None and body.type == 'arrow_expression_clause':
        steps.extend(builder.expression_body(declaration, body))
    else:
        steps.extend(builder.statements(body))
    return {'version': VERSION, 'status': 'parsed' if body is not None else 'declaration-only', 'steps': steps, 'truncated': builder.truncated,
            'unsupported': sorted(builder.unsupported), 'sourceKind': 'working-tree-ast',
            'limits': {'maxSteps': MAX_STEPS, 'maxDepth': MAX_DEPTH},
            'warning': 'Static C# reading order, not an executed trace. Runtime targets, scheduling and exceptional cleanup are not inferred.'}
