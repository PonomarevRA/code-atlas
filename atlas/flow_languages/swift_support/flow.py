"""Swift evaluation trees with explicit conditional and deferred boundaries."""
MAX_AST_NODES = 20000
MAX_STEPS = 180
MAX_DEPTH = 24
_DEFERRED = {'lambda_literal', 'function_declaration', 'init_declaration', 'class_declaration'}
_BLOCKS = {'statements', 'function_body'}
_COMMENTS = {'comment', 'multiline_comment'}


class SwiftFlow:
    """Collect source facts and a bounded tree in one pass; no call targets are guessed."""
    WARNING = ('Static Swift source order only; conditions, runtime dispatch, implicit ownership cleanup, '
               'throws propagation and callback execution are not evaluated. Columns are 1-based UTF-8 bytes.')

    def __init__(self, content, max_events):
        """All budgets belong to one selected declaration; the host owns file I/O."""
        self.content = content
        self.max_events = max(0, max_events)
        self.events, self.branches, self.terminals = [], [], []
        self.unsupported = set()
        self.count = 0
        self.truncated = self.events_truncated = False

    @staticmethod
    def limits():
        """Publish fixed budgets so a partial tree cannot look complete."""
        return {'maxAstNodes': MAX_AST_NODES, 'maxSteps': MAX_STEPS, 'maxDepth': MAX_DEPTH,
                'maxExpressionChars': 400}

    def text(self, node):
        """Labels are previews; site coordinates always describe the complete expression."""
        if node is None:
            return ''
        value = self.content[node.start_byte:node.end_byte].decode('utf-8')
        if len(value) > 400:
            self.unsupported.add('Swift source label/expression shortened to 400 characters.')
        return value[:400]

    @staticmethod
    def site(node):
        """Use the same exact occurrence tuple in events and tree call steps."""
        return {'line': node.start_point.row + 1, 'column': node.start_point.column + 1,
                'endLine': node.end_point.row + 1, 'endColumn': node.end_point.column + 1}

    def allowed(self, depth):
        """Stop before traversing more expression work when the shared budget is exhausted."""
        if depth >= MAX_DEPTH or self.count >= MAX_STEPS:
            self.truncated = True
            self.unsupported.add('Swift tree exceeds step/depth budget; remaining source not extracted.')
            return False
        return True

    def step(self, node, kind, **facts):
        """Count every rendered step, including controls, placeholders and literal values."""
        if self.count >= MAX_STEPS:
            self.truncated = True
            self.unsupported.add('Swift tree exceeds step/depth budget; remaining source not extracted.')
            return None
        self.count += 1
        return {'kind': kind, 'line': node.start_point.row + 1, 'column': node.start_point.column + 1,
                'label': self.text(node), **facts}

    def opaque(self, node, reason, *, stop=False):
        """Unsupported source stays visible; its body is never flattened into execution."""
        self.unsupported.add(reason)
        prefix = 'Не раскрыто: '
        if reason.startswith('Swift defer'):
            prefix = 'Регистрация defer; cleanup отложен: '
        elif 'deferred' in reason or 'local declaration' in reason:
            prefix = 'Отложенное объявление/тело: '
        label = prefix + self.text(node)[:400 - len(prefix)]
        item = self.step(node, 'unknown' if stop else 'statement', label=label, incomplete=True, unsupported=reason)
        return [item] if item else []

    def arm(self, label, steps, *, real_empty=False):
        """A real empty arm is distinct from a budget-limited extraction."""
        result = {'label': label, 'steps': steps}
        if not steps:
            result['emptyReason'] = 'skip' if real_empty or not self.truncated else 'not-extracted'
        return result

    def branch(self, node, kind, context):
        """Register control identity before children so nested events have stable arm IDs."""
        identifier = f'swift-{node.start_byte}-{node.end_byte}-{kind}'
        record = {'id': identifier, 'kind': kind, 'line': node.start_point.row + 1,
                  'endLine': node.end_point.row + 1, 'condition': self.text(node),
                  'parentArmIds': list(context), 'arms': []}
        self.branches.append(record)
        return record

    def arm_context(self, record, label, index, context):
        """Arm IDs are lexical source identities, never raw graph node IDs."""
        identifier = record['id'] + f'-a{index}'
        record['arms'].append({'id': identifier, 'label': label, 'kind': 'arm',
                               'line': record['line'], 'endLine': record['endLine']})
        return (*context, identifier)

    def value(self, node, depth, context):
        """Value-only arms remain visible without invoking the same call twice."""
        steps = self.expression(node, depth, context)
        if not steps and node is not None and not self.truncated:
            item = self.step(node, 'value', expression=self.text(node))
            if item:
                steps.append(item)
        return steps

    def call(self, node, depth, context, awaited=False):
        """Evaluate receiver then arguments, excluding deferred closure bodies."""
        children = node.named_children
        if not children:
            return self.opaque(node, 'Swift call target not extracted.')
        target = children[0]
        suffix = children[-1] if len(children) > 1 else None
        if self.text(target) == 'defer':
            return self.opaque(node, 'Swift defer executes on scope exit in reverse registration order; cleanup body withheld.')
        if self.receiver_await(target):
            return self.opaque(node, 'Swift await inside receiver chain has ambiguous grammar scope; call chain withheld.')
        arguments = next((child for child in suffix.named_children if child.type == 'value_arguments'), None) if suffix else None
        if arguments is not None and arguments.children[0].type == '[':
            if self.has_optional(target) or any(child.type == '?' for child in node.children):
                return self.opaque(node, 'Swift optional subscript requires nil/index routing; subscript withheld.')
            result = self.expression(target, depth + 1, context)
            result += self.arguments(suffix, depth + 1, context)
            item = self.step(node, 'value', expression=self.text(node))
            return result + ([item] if item else [])
        # Swift optionality can propagate over a larger chain. Only a single
        # navigation is expanded; complex chains remain a visible static boundary.
        if self.has_optional(target):
            receiver = target.child_by_field_name('target') if target.type == 'navigation_expression' else None
            if receiver is None or self.has_optional(receiver):
                return self.opaque(node, 'Swift compound optional call chain requires propagation analysis; body withheld.')
            before = self.expression(receiver, depth + 1, context)
            choice = self.step(node, 'branch', controlKind='?.', label=self.text(target), arms=[])
            if choice is None:
                return before
            record = self.branch(node, '?.', context)
            active = self.arm_context(record, 'receiver != nil', 0, context)
            self.arm_context(record, 'receiver == nil', 1, context)
            tail = self.arguments(suffix, depth + 1, active)
            tail += self.emit_call(node, target, active, awaited)
            choice['arms'] = [self.arm('receiver != nil', tail), self.arm('receiver == nil', [], real_empty=True)]
            return before + [choice]
        result = self.expression(target, depth + 1, context)
        result += self.arguments(suffix, depth + 1, context)
        result += self.emit_call(node, target, context, awaited)
        return result

    @staticmethod
    def receiver_await(node):
        """Detect the installed grammar's await-on-receiver ambiguity before emitting events."""
        while node is not None and node.type == 'navigation_expression':
            node = node.child_by_field_name('target')
        return node is not None and node.type == 'await_expression'

    @staticmethod
    def has_optional(node):
        """Inspect only navigation/call receiver spines, never strings or deferred bodies."""
        pending = [node] if node else []
        while pending:
            item = pending.pop()
            if item.type in {'navigation_expression', 'call_expression'}:
                if any(child.type == '?' for child in item.children):
                    return True
                pending.extend(child for child in item.named_children
                               if child.type in {'navigation_expression', 'call_expression'})
        return False

    def arguments(self, suffix, depth, context):
        """Only argument expressions run now; trailing closure bodies run at an unknown time."""
        result = []
        if suffix is not None:
            for child in suffix.named_children:
                result += self.expression(child, depth + 1, context)
                if self.truncated:
                    break
        return result

    def emit_call(self, node, target, context, awaited):
        """Share an exact site between a tree step and its bounded lexical event."""
        if len(self.events) >= self.max_events:
            self.events_truncated = self.truncated = True
            return self.opaque(node, 'Swift event budget exhausted; call site not extracted.')
        site = self.site(node)
        expression = self.text(target)
        # The host binds graph labels by lexical method name, while the tree
        # retains the receiver expression. Neither is semantic overload binding.
        suffix = target.child_by_field_name('suffix') if target.type == 'navigation_expression' else None
        callee = self.text(suffix.child_by_field_name('suffix')) if suffix else expression
        item = self.step(node, 'call', label=expression[:398] + '()', site=site,
                         sourceExpression=self.text(node), directAwait=awaited)
        if item is None:
            return []
        self.events.append({'kind': 'call', 'text': self.text(node), 'callee': callee,
                            **site, 'targetNodeIds': [], 'confidence': 'source',
                            'conditional': bool(context), 'awaited': awaited, 'parallel': False,
                            'branchIds': list(context), 'sourceExpression': self.text(node)})
        return [item]

    def expression(self, node, depth=0, context=(), awaited=False):
        """Preserve eager operands, lazy alternatives and await ownership; never run Swift."""
        if node is None or not self.allowed(depth):
            return []
        kind = node.type
        if kind in _DEFERRED:
            return self.opaque(node, 'Swift closure/local declaration body is deferred; capture initializers and invocation are not expanded.')
        if kind in {'macro_invocation', 'consume_expression', 'copy_expression'}:
            return self.opaque(node, 'Swift macro/ownership expression requires expansion or lifetime analysis; expression withheld.')
        if kind == 'call_expression':
            return self.call(node, depth, context, awaited)
        if kind == 'await_expression':
            operand = node.child_by_field_name('expr')
            result = self.expression(operand, depth + 1, context, True)
            item = self.step(node, 'await', label='await ' + self.text(operand)[:394])
            return result + ([item] if item else [])
        if kind == 'try_expression':
            operator = next((child for child in node.named_children if child.type == 'try_operator'), None)
            if self.text(operator) != 'try':
                return self.opaque(node, 'Swift try?/try! success, nil and trap paths are not expanded.')
            self.unsupported.add('Swift try propagation is lexical only; exceptional edges are not expanded.')
            return self.expression(node.child_by_field_name('expr'), depth + 1, context, awaited)
        if kind == 'tuple_expression' and len(node.named_children) == 1:
            return self.expression(node.named_children[0], depth + 1, context, awaited)
        if kind in {'conjunction_expression', 'disjunction_expression', 'nil_coalescing_expression'}:
            return self.lazy(node, depth, context)
        if kind == 'ternary_expression':
            condition = node.child_by_field_name('condition')
            result = self.expression(condition, depth + 1, context)
            item = self.step(node, 'branch', controlKind='conditional', label=self.text(condition), arms=[])
            if item is None:
                return result
            record = self.branch(node, 'conditional', context)
            arms = []
            for index, (field, label) in enumerate([('if_true', 'true'), ('if_false', 'false')]):
                active = self.arm_context(record, label, index, context)
                arms.append(self.arm(label, self.value(node.child_by_field_name(field), depth + 1, active)))
            item['arms'] = arms
            return result + [item]
        if kind == 'navigation_expression' and self.has_optional(node):
            return self.opaque(node, 'Swift optional property chain is not expanded; nil/value paths remain unknown.')
        if kind in {'if_statement', 'switch_statement'}:
            return self.statement(node, depth + 1, context)
        result = []
        for child in node.named_children:
            if child.type not in _COMMENTS:
                result += self.expression(child, depth + 1, context)
                if self.truncated:
                    break
        return result

    def lazy(self, node, depth, context):
        """The right operand of &&, || and ?? belongs only to its evaluating arm."""
        coalescing = node.type == 'nil_coalescing_expression'
        left = node.child_by_field_name('value' if coalescing else 'lhs')
        right = node.child_by_field_name('if_nil' if coalescing else 'rhs')
        operator = '??' if coalescing else self.text(node.child_by_field_name('op'))
        result = self.expression(left, depth + 1, context)
        label = {'??': 'left == nil', '&&': 'left == true', '||': 'left == false'}[operator]
        branch = self.step(node, 'branch', controlKind=operator, label=self.text(left), arms=[])
        if branch is None:
            return result
        record = self.branch(node, operator, context)
        active = self.arm_context(record, label, 0, context)
        self.arm_context(record, 'reuse left / skip right', 1, context)
        evaluated = self.value(right, depth + 1, active)
        skipped = []
        if coalescing:
            item = self.step(left, 'value', expression=self.text(left), reuseResult=True)
            if item:
                skipped.append(item)
        branch['arms'] = [self.arm(label, evaluated), self.arm('reuse left / skip right', skipped, real_empty=not coalescing)]
        return result + [branch]

    def statements(self, node, depth=0, context=()):
        """Guard owns the success continuation; failure terminals never fall through to it."""
        if node is None or not self.allowed(depth):
            return []
        if node.type not in _BLOCKS:
            return self.statement(node, depth + 1, context)
        children = [child for child in node.named_children if child.type not in _COMMENTS]
        if node.type == 'function_body':
            children = [child for child in children if child.type == 'statements']
        return self.sequence(children, depth + 1, context)

    def sequence(self, children, depth, context):
        """Stop lexical successors after explicit jumps; retain nested guard continuation once."""
        result = []
        compilation_depth = 0
        for index, child in enumerate(children):
            if not self.allowed(depth):
                break
            if child.type == 'directive':
                directive = self.text(child).split(None, 1)[0]
                if directive == '#if':
                    if compilation_depth == 0:
                        result += self.opaque(child, 'Swift conditional compilation alternatives require build configuration; region withheld.')
                    compilation_depth += 1
                elif directive == '#endif':
                    compilation_depth = max(0, compilation_depth - 1)
                elif compilation_depth == 0:
                    result += self.opaque(child, 'Swift compiler directive is not expanded.')
                continue
            if compilation_depth:
                continue
            if child.type == 'guard_statement':
                result += self.guard(child, children[index + 1:], depth + 1, context)
                break
            result += self.statement(child, depth + 1, context)
            if child.type == 'control_transfer_statement':
                break
        return result

    def conditions(self, node, depth, context):
        """Comma conditions short circuit; later clauses are nested in successful previous clauses."""
        if any(child.type == 'case' for child in node.children):
            return self.opaque(node, 'Swift case pattern condition matching is not expanded; condition source retained.')
        conditions = []
        for index, child in enumerate(node.children):
            if node.field_name_for_child(index) == 'condition' and child.is_named and child.type not in {'value_binding_pattern', 'statements'}:
                conditions.append(child)
        return self.condition_sequence(conditions, depth + 1, context)

    def condition_sequence(self, conditions, depth, context):
        """Evaluate each Swift condition once without claiming binding success or truth."""
        if not conditions or not self.allowed(depth):
            return []
        first = conditions[0]
        result = self.value(first, depth + 1, context)
        if len(conditions) > 1:
            item = self.step(first, 'branch', controlKind='condition-list', arms=[])
            if item is None:
                return result
            record = self.branch(first, 'condition-list', context)
            active = self.arm_context(record, 'previous condition succeeded', 0, context)
            self.arm_context(record, 'previous condition failed', 1, context)
            tail = self.condition_sequence(conditions[1:], depth + 1, active)
            item['arms'] = [self.arm('previous condition succeeded', tail), self.arm('previous condition failed', [], real_empty=True)]
            result.append(item)
        return result

    def guard(self, node, continuation, depth, context):
        """Enclose following statements in guard success; else remains a separate exit arm."""
        if not self.allowed(depth):
            return []
        result = self.conditions(node, depth + 1, context)
        item = self.step(node, 'branch', controlKind='guard', conditionResult='reuse', arms=[])
        if item is None:
            return result
        record = self.branch(node, 'guard', context)
        success = self.arm_context(record, 'guard succeeded', 0, context)
        failure = self.arm_context(record, 'guard failed', 1, context)
        body = next((child for child in node.named_children if child.type == 'statements'), None)
        # Source order places else before the continuation. The tree arm order
        # remains success/failure, independently of lexical event ordering.
        failure_steps = self.statements(body, depth + 1, failure)
        success_steps = self.sequence(continuation, depth + 1, success)
        item['arms'] = [self.arm('guard succeeded', success_steps), self.arm('guard failed', failure_steps)]
        return result + [item]

    def statement(self, node, depth, context):
        """Dispatch supported statements; unknown compound bodies stay behind explicit boundaries."""
        if not self.allowed(depth):
            return []
        kind = node.type
        if kind in _BLOCKS:
            return self.statements(node, depth + 1, context)
        if kind in _DEFERRED:
            return self.opaque(node, 'Swift local declaration is not an invocation; body withheld.')
        if kind == 'if_statement':
            return self.if_statement(node, depth, context)
        if kind == 'switch_statement':
            return self.switch(node, depth, context)
        if kind in {'for_statement', 'while_statement', 'repeat_while_statement'}:
            return self.loop(node, depth, context)
        if kind in {'do_statement', 'labeled_statement'}:
            return self.opaque(node, 'Swift ' + kind + ' requires exit/exception/label routing; body withheld.')
        if kind == 'statement_label':
            return self.opaque(node, 'Swift statement label retained; named jump targets are not resolved.')
        if kind == 'control_transfer_statement':
            return self.transfer(node, depth, context)
        if kind == 'property_declaration':
            if any(child.type == 'async' for child in node.children):
                return self.opaque(node, 'Swift async let requires task lifetime/join/cleanup analysis; initializer withheld.')
            if node.child_by_field_name('computed_value') is not None:
                return self.opaque(node, 'Swift computed property/accessor body is deferred; getter/setter invocation not expanded.')
            result = []
            for index, child in enumerate(node.children):
                if node.field_name_for_child(index) == 'value':
                    result += self.expression(child, depth + 1, context)
                    if self.truncated:
                        break
            item = self.step(node, 'statement', operationKind='declaration')
            return result + ([item] if item else [])
        if kind in {'assignment', 'directly_assignable_expression'}:
            target = node.child_by_field_name('target')
            if target is not None and (target.type != 'directly_assignable_expression' or
                                       len(target.named_children) != 1 or target.named_children[0].type != 'simple_identifier'):
                return self.opaque(node, 'Swift complex assignment target requires setter/access ordering; assignment withheld.')
            result = self.expression(node, depth + 1, context)
            item = self.step(node, 'statement')
            return result + ([item] if item else [])
        if kind.endswith('_statement'):
            return self.opaque(node, 'Swift statement not expanded: ' + kind)
        result = self.expression(node, depth + 1, context)
        if not result and not self.truncated:
            item = self.step(node, 'statement')
            if item:
                result.append(item)
        return result

    def if_statement(self, node, depth, context):
        """Retain real else/else-if arms and a skip arm when else is absent."""
        result = self.conditions(node, depth + 1, context)
        bodies = [None, None]
        arm_index = 0
        for child in node.named_children:
            if child.type == 'else':
                arm_index = 1
            elif child.type in {'statements', 'if_statement'}:
                bodies[arm_index] = child
        item = self.step(node, 'branch', controlKind='if', conditionResult='reuse', arms=[])
        if item is None:
            return result
        record = self.branch(node, 'if', context)
        arms = []
        for index, label in enumerate(['true', 'false']):
            active = self.arm_context(record, label, index, context)
            body = bodies[index] if index < len(bodies) else None
            empty = body is None or not any(child.type not in _COMMENTS for child in body.named_children)
            arms.append(self.arm(label, self.statements(body, depth + 1, active), real_empty=empty))
        item['arms'] = arms
        return result + [item]

    def switch(self, node, depth, context):
        """Simple lexical case arms only; pattern guards/fallthrough require ordered matching."""
        entries = [child for child in node.named_children if child.type == 'switch_entry']
        if any(child.type == 'directive' for child in node.named_children):
            return self.opaque(node, 'Swift conditional compilation in switch requires build configuration; switch withheld.')
        for entry in entries:
            for pattern in entry.named_children:
                if pattern.type != 'switch_pattern':
                    continue
                pending = [pattern]
                while pending:
                    child = pending.pop()
                    if child.type == 'call_expression':
                        return self.opaque(node, 'Swift switch expression pattern requires ordered pattern evaluation; switch withheld.')
                    pending.extend(child.named_children)
            pending = [entry]
            while pending:
                child = pending.pop()
                if child.type in {'where_keyword', 'fallthrough'} or self.text(child) == 'fallthrough':
                    return self.opaque(node, 'Swift switch where/fallthrough requires ordered pattern transfer; switch withheld.')
                pending.extend(child.named_children)
        result = self.expression(node.child_by_field_name('expr'), depth + 1, context)
        item = self.step(node, 'branch', controlKind='switch', label=self.text(node.child_by_field_name('expr')), arms=[])
        if item is None:
            return result
        record = self.branch(node, 'switch', context)
        arms = []
        for index, entry in enumerate(entries):
            colon = next((child for child in entry.children if child.type == ':'), None)
            label = self.content[entry.start_byte:colon.start_byte].decode('utf-8')[:400] if colon else self.text(entry)
            active = self.arm_context(record, label, index, context)
            body = next((child for child in entry.named_children if child.type == 'statements'), None)
            arms.append(self.arm(label, self.statements(body, depth + 1, active)))
            if self.truncated:
                break
        item['arms'] = arms
        return result + [item]

    def loop(self, node, depth, context):
        """For collection runs once; conditions and loop bodies retain repeat/skip ownership."""
        pattern = node.child_by_field_name('item')
        if pattern is not None and any(child.type == 'case' for child in pattern.children):
            return self.opaque(node, 'Swift for case pattern filtering is not expanded; loop withheld.')
        if any(child.type in {'await', 'try'} for child in node.children):
            return self.opaque(node, 'Swift for await/try requires asynchronous iterator cleanup; loop withheld.')
        foreach = node.type == 'for_statement'
        repeat = node.type == 'repeat_while_statement'
        control_kind = 'foreach' if foreach else 'do' if repeat else 'while'
        result = self.expression(node.child_by_field_name('collection'), depth + 1, context) if foreach else []
        item = self.step(node, 'loop', controlKind=control_kind, condition=[],
                         body=[], update=[], canExit=True)
        if item is None:
            return result
        record = self.branch(node, control_kind, context)
        active = self.arm_context(record, 'loop body', 0, context)
        condition = [] if foreach or repeat else self.conditions(node, depth + 1, context)
        body_node = next((child for child in node.named_children if child.type == 'statements'), None)
        where = next((child for child in node.named_children if child.type == 'where_clause'), None)
        body = self.filtered_body(where, body_node, depth + 1, active) if where else self.statements(body_node, depth + 1, active)
        if repeat:
            condition = self.conditions(node, depth + 1, context)
        item.update(condition=condition, body=body)
        return result + [item]

    def filtered_body(self, where, body, depth, context):
        """A for-where filter runs per element; a false filter skips directly to the next iteration."""
        if not self.allowed(depth):
            return []
        condition = next((child for child in where.named_children if child.type != 'where_keyword'), None)
        result = self.value(condition, depth + 1, context)
        item = self.step(where, 'branch', controlKind='if', conditionResult='reuse', arms=[])
        if item is None:
            return result
        record = self.branch(where, 'if', context)
        selected = self.arm_context(record, 'where true', 0, context)
        self.arm_context(record, 'where false; skip element', 1, context)
        item['arms'] = [self.arm('where true', self.statements(body, depth + 1, selected)),
                        self.arm('where false; skip element', [], real_empty=True)]
        return result + [item]

    def transfer(self, node, depth, context):
        """Evaluate jump operands first and then emit a terminal scoped to lexical arms."""
        token = self.text(node.children[0])
        if token not in {'return', 'throw', 'break', 'continue'}:
            return self.opaque(node, 'Swift control transfer not modeled: ' + token, stop=True)
        if token in {'break', 'continue'} and len(node.named_children):
            return self.opaque(node, 'Swift labeled break/continue requires target routing; jump withheld.', stop=True)
        result = []
        for child in node.named_children:
            if child.type != 'throw_keyword':
                result += self.expression(child, depth + 1, context)
        item = self.step(node, token)
        if item:
            result.append(item)
            self.terminals.append({'kind': token, **self.site(node), 'branchIds': list(context), 'text': self.text(node)})
        return result
