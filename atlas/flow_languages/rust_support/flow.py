"""Bounded Rust evaluation trees with explicit opaque/deferred source boundaries."""
import re

from .syntax import MAX_TEXT, site, text

MAX_STEPS = 180
MAX_DEPTH = 24
DEFERRED = {'closure_expression', 'async_block', 'const_block', 'function_item',
            'struct_item', 'enum_item', 'impl_item', 'trait_item', 'mod_item', 'static_item', 'const_item'}
LEAVES = {'identifier', 'self', 'scoped_identifier', 'type_identifier', 'primitive_type',
          'field_identifier', 'shorthand_field_identifier', 'integer_literal', 'float_literal', 'boolean_literal', 'string_literal', 'raw_string_literal',
          'char_literal', 'unit_expression', 'negative_literal', 'label', 'lifetime', 'metavariable'}
WRAPPERS = {'parenthesized_expression', 'unary_expression', 'reference_expression', 'dereference_expression',
            'tuple_expression', 'array_expression', 'index_expression', 'field_expression', 'range_expression',
            'struct_expression', 'field_initializer_list', 'field_initializer', 'base_field_initializer',
            'type_cast_expression'}


class RustFlow:
    """Own local budgets, events and branches; never resolve raw graph target IDs."""

    def __init__(self, content, *, max_events):
        """Initialize independent limits for visible steps and lexical call events."""
        self.content = content
        self.max_events = max(0, max_events)
        self.count = 0
        self.truncated = False
        self.events_truncated = False
        self.events, self.branches, self.terminals, self.unsupported = [], [], [], []

    def warn(self, reason):
        """Deduplicate bounded static limitations rather than repeating each occurrence."""
        if reason not in self.unsupported and len(self.unsupported) < 40:
            self.unsupported.append(reason)

    def emit(self, kind, node, *, label=None, **extra):
        """Create one source-backed step or visibly mark exhaustion on the enclosing flow."""
        if self.count >= MAX_STEPS:
            self.truncated = True
            self.warn('Rust step budget exceeded (180); remaining source is not extracted.')
            return []
        self.count += 1
        value = text(node, self.content) if label is None else label
        step = {'kind': kind, 'label': value[:MAX_TEXT], **site(node), **extra}
        if len(value) > MAX_TEXT:
            step['labelTruncated'] = True
        if kind in {'return', 'break', 'continue', 'throw'}:
            self.terminals.append({'kind': kind, **site(node), 'text': value[:MAX_TEXT]})
        return [step]

    def opaque(self, node, reason):
        """Keep unmodeled source visible without traversing a falsely executable body."""
        self.warn(reason)
        return self.emit('statement', node, incomplete=True, unsupportedReason=reason)

    def branch(self, node, control, label, parents):
        """Register one control identity shared by tree arms and event branch contexts."""
        identifier = f'rust-{node.start_byte}-{control}'
        fact = {'id': identifier, 'kind': control, **site(node), 'condition': label[:MAX_TEXT],
                'arms': [], 'parentArmIds': list(parents)}
        self.branches.append(fact)
        steps = self.emit('branch', node, label=label, controlKind=control, arms=[], branchId=identifier)
        return (steps[0] if steps else None), fact

    def arm(self, branch, fact, index, label, steps):
        """Attach real alternatives, preserving intentionally empty skip paths."""
        identifier = fact['id'] + f'-a{index}'
        item = {'id': identifier, 'label': label[:MAX_TEXT], 'steps': steps}
        if not steps:
            item['emptyReason'] = 'not-extracted' if self.truncated else 'skip'
        branch['arms'].append(item)
        fact['arms'].append({'id': identifier, 'label': label[:MAX_TEXT]})

    @staticmethod
    def falls_through(steps):
        """Recognize structural termination; never evaluate conditions or runtime values."""
        if not steps:
            return True
        last = steps[-1]
        if last['kind'] in {'return', 'throw', 'break', 'continue'}:
            return False
        if last['kind'] == 'branch':
            return any(RustFlow.falls_through(arm['steps']) for arm in last['arms'])
        return not (last['kind'] == 'loop' and not (last['canExit'] or last.get('hasBreakExit')))

    @staticmethod
    def may_exit(steps):
        """Conservatively mark source after a conditional jump as conditionally reached."""
        for step in steps:
            if step['kind'] in {'return', 'throw', 'break', 'continue'} or step.get('incomplete'):
                return True
            if step['kind'] in {'loop', 'branch'}:
                return True
        return False

    def block(self, node, parents, depth, conditional=False):
        """Read lexical statements, stopping at unconditional local jumps without fallthrough."""
        result = []
        for child in node.named_children:
            if child.type in {'line_comment', 'block_comment'}:
                continue
            if child.type == 'attribute_item':
                # cfg and procedural attributes can remove/rewrite the following source.
                return result + self.opaque(node, 'Rust statement attributes/cfg may rewrite this block; remaining body is opaque.')
            steps = self.visit(child, parents, depth + 1, conditional)
            if not steps and not self.truncated and (child.type in LEAVES or child.type.endswith('_expression')):
                steps = self.emit('value', child, expression=text(child, self.content)[:MAX_TEXT])
            result.extend(steps)
            conditional |= self.may_exit(steps)
            if not self.falls_through(steps):
                break
            if self.truncated and self.count >= MAX_STEPS:
                break
        return result

    def visit(self, node, parents, depth, conditional=False):
        """Dispatch syntax by evaluation semantics; depth is bounded before recursion."""
        if node is None:
            return []
        if depth > MAX_DEPTH:
            self.truncated = True
            return self.opaque(node, 'Rust extraction depth exceeded (24); nested source is opaque.')
        if self.count >= MAX_STEPS:
            self.truncated = True
            self.warn('Rust step budget exceeded (180); remaining source is not extracted.')
            return []
        kind = node.type
        if kind in {'line_comment', 'block_comment'} or kind in LEAVES or kind.endswith('_type'):
            return []
        if kind == 'block':
            return self.block(node, parents, depth, conditional)
        if kind in DEFERRED:
            return self.opaque(node, f'Rust {kind} body is deferred/separate; invocation and runtime are unknown.')
        if kind in {'macro_invocation', 'macro_definition'}:
            return self.opaque(node, 'Rust macro expansion unavailable; tokens are not executable call facts.')
        if kind in {'try_block', 'unsafe_block', 'gen_block', 'yield_expression'}:
            return self.opaque(node, f'Rust {kind} control/effects are not modeled.')
        if kind == 'expression_statement':
            children = node.named_children
            steps = self.visit(children[0], parents, depth + 1, conditional) if children else []
            return steps or ([] if self.truncated else self.emit('statement', node))
        if kind == 'let_declaration':
            return self.let(node, parents, depth, conditional)
        if kind == 'if_expression':
            return self.choice(node, parents, depth, conditional)
        if kind == 'match_expression':
            return self.match(node, parents, depth, conditional)
        if kind in {'for_expression', 'while_expression', 'loop_expression'}:
            return self.loop(node, parents, depth, conditional)
        if kind == 'call_expression':
            return self.call(node, parents, depth, conditional)
        if kind == 'await_expression':
            operand = node.named_children[0]
            steps = self.visit(operand, parents, depth + 1, conditional)
            # Parentheses preserve the produced value; argument calls remain unawaited.
            while operand.type == 'parenthesized_expression' and operand.named_child_count == 1:
                operand = operand.named_children[0]
            if operand.type == 'call_expression':
                for event in reversed(self.events):
                    if all(event[key] == value for key, value in site(operand).items()):
                        event['awaited'] = True
                        break
                if steps and steps[-1]['kind'] == 'call':
                    steps[-1]['directAwait'] = True
            self.warn('Rust await suspension, polling and cancellation/drop behavior are unknown.')
            return steps + self.emit('await', node) if self.falls_through(steps) else steps
        if kind == 'try_expression':
            return self.propagate(node, parents, depth, conditional)
        if kind in {'return_expression', 'break_expression', 'continue_expression'}:
            if any(c.type == 'label' for c in node.named_children):
                return self.opaque(node, 'Rust labeled jumps are not modeled.')
            steps = self.children(node, parents, depth, conditional)
            if not self.falls_through(steps):
                return steps
            return steps + self.emit(kind.removesuffix('_expression'), node)
        if kind == 'binary_expression':
            operator = text(node.child_by_field_name('operator'), self.content)
            if operator in {'&&', '||'}:
                return self.short_circuit(node, operator, parents, depth, conditional)
            return self.children(node, parents, depth, conditional)
        if kind == 'let_condition':
            return self.visit(node.child_by_field_name('value'), parents, depth + 1, conditional)
        if kind in {'assignment_expression', 'compound_assignment_expr'}:
            if kind == 'compound_assignment_expr':
                return self.opaque(node, 'Rust compound assignment evaluation/overloaded operators require type semantics.')
            # Rust plain assignment evaluates the right operand before its place expression.
            steps = self.visit(node.child_by_field_name('right'), parents, depth + 1, conditional)
            if not self.falls_through(steps):
                return steps
            steps += self.visit(node.child_by_field_name('left'), parents, depth + 1, conditional or self.may_exit(steps))
            return steps + self.emit('statement', node) if self.falls_through(steps) else steps
        if kind == 'generic_function':
            return self.visit(node.child_by_field_name('function'), parents, depth + 1, conditional)
        if kind == 'array_expression' and node.child_by_field_name('length') is not None:
            return self.opaque(node, 'Rust repeated array length/const context is not extracted as runtime calls.')
        if kind == 'field_expression':
            return self.visit(node.child_by_field_name('value'), parents, depth + 1, conditional)
        if kind in WRAPPERS:
            steps = self.children(node, parents, depth, conditional)
            if not steps:
                steps = self.emit('value', node, expression=text(node, self.content)[:MAX_TEXT])
            return steps
        if kind == 'empty_statement':
            return []
        return self.opaque(node, f'Rust syntax {kind} is not extracted.')

    def children(self, node, parents, depth, conditional):
        """Evaluate regular operands in source order; later operands inherit possible exits."""
        steps = []
        for child in node.named_children:
            current = self.visit(child, parents, depth + 1, conditional)
            steps.extend(current)
            conditional |= self.may_exit(current)
            if not self.falls_through(current):
                break
        return steps

    def value(self, node, parents, depth, conditional):
        """Preserve value-only alternatives rather than displaying them as an empty arm."""
        steps = self.visit(node, parents, depth + 1, conditional)
        if node is not None and node.type != 'block' and not steps and not self.truncated:
            steps = self.emit('value', node, expression=text(node, self.content)[:MAX_TEXT])
        return steps

    def call(self, node, parents, depth, conditional):
        """Read receiver/callee then argument expressions before emitting one outer call."""
        function = node.child_by_field_name('function')
        steps = self.visit(function, parents, depth + 1, conditional)
        if not self.falls_through(steps):
            return steps
        arguments = node.child_by_field_name('arguments')
        if arguments is not None:
            for argument in arguments.named_children:
                current = self.visit(argument, parents, depth + 1, conditional or self.may_exit(steps))
                steps.extend(current)
                if not self.falls_through(current):
                    return steps
        expression = text(function, self.content)
        name_node = function
        if name_node is not None and name_node.type == 'generic_function':
            name_node = name_node.child_by_field_name('function')
        if name_node is not None and name_node.type == 'field_expression':
            name_node = name_node.child_by_field_name('field')
        elif name_node is not None and name_node.type == 'scoped_identifier':
            name_node = name_node.child_by_field_name('name')
        name = text(name_node, self.content)
        simple = re.fullmatch(r'(?:r#)?[A-Za-z_][A-Za-z_0-9]*', name)
        event = {'kind': 'call', 'text': expression[:396] + '()', 'callee': name.removeprefix('r#') if simple else None,
                 **site(node), 'targetNodeIds': [], 'confidence': 'source' if simple else 'unresolved',
                 'conditional': conditional or bool(parents) or self.may_exit(steps), 'awaited': False,
                 'parallel': False, 'branchIds': list(parents), 'sourceExpression': text(node, self.content)[:MAX_TEXT],
                 'sourceExpressionTruncated': len(text(node, self.content)) > MAX_TEXT,
                 'arguments': [], 'argumentsTruncated': False}
        if arguments is not None:
            for index, argument in enumerate(arguments.named_children[:40]):
                event['arguments'].append({'index': index, 'expression': text(argument, self.content)[:MAX_TEXT],
                                           'expressionTruncated': len(text(argument, self.content)) > MAX_TEXT, 'sourceKind': 'lexical'})
            event['argumentsTruncated'] = arguments.named_child_count > 40
        if len(self.events) < self.max_events:
            self.events.append(event)
        else:
            self.events_truncated = True
            self.warn('Rust call event budget exceeded; remaining events are omitted.')
        return steps + self.emit('call', node, label=event['text'], site=site(node), sourceExpression=event['sourceExpression'])

    def choice(self, node, parents, depth, conditional):
        """Evaluate if/if-let condition once, then retain mutually exclusive alternatives."""
        condition = node.child_by_field_name('condition')
        before = self.visit(condition, parents, depth + 1, conditional)
        if not self.falls_through(before):
            return before
        branch, fact = self.branch(node, 'if', text(condition, self.content), parents)
        if branch is None:
            return before
        if before:
            branch.update(conditionResult='reuse', conditionExpression=text(condition, self.content)[:MAX_TEXT])
        for index, field in enumerate(('consequence', 'alternative')):
            body = node.child_by_field_name(field)
            if body is not None and body.type == 'else_clause':
                body = body.named_children[-1] if body.named_children else None
            branch_parents = parents + (fact['id'] + f'-a{index}',)
            steps = self.value(body, branch_parents, depth + 1, True)
            self.arm(branch, fact, index, 'true' if index == 0 else 'false', steps)
        return before + [branch]

    def let(self, node, parents, depth, conditional):
        """Keep a let binding and its diverging else path separate from initializer effects."""
        value = node.child_by_field_name('value')
        steps = self.visit(value, parents, depth + 1, conditional)
        if not self.falls_through(steps):
            return steps
        alternative = node.child_by_field_name('alternative')
        if alternative is not None:
            branch, fact = self.branch(node, 'let-else', 'let ' + text(node.child_by_field_name('pattern'), self.content), parents)
            if branch is not None:
                self.arm(branch, fact, 0, 'pattern matched', [])
                failed = self.block(alternative, parents + (fact['id'] + '-a1',), depth + 1, True)
                self.arm(branch, fact, 1, 'pattern failed', failed)
                steps.append(branch)
        return steps + self.emit('statement', node)

    def match(self, node, parents, depth, conditional):
        """Retain plain match arms; guarded fallthrough stays opaque until UI models it."""
        body = node.child_by_field_name('body')
        arms = [c for c in body.named_children if c.type == 'match_arm'] if body is not None else []
        for arm in arms:
            if any(c.type == 'attribute_item' for c in arm.named_children):
                return self.opaque(node, 'Rust match arm attributes/cfg may change alternatives; match is opaque.')
            pattern = arm.child_by_field_name('pattern')
            if pattern is not None and pattern.child_by_field_name('condition') is not None:
                return self.opaque(node, 'Rust match guards can resume later arms; guarded match is opaque (no false linear guard path).')
        before = self.visit(node.child_by_field_name('value'), parents, depth + 1, conditional)
        if not self.falls_through(before):
            return before
        branch, fact = self.branch(node, 'match', 'match ' + text(node.child_by_field_name('value'), self.content), parents)
        if branch is None:
            return before
        if before:
            branch.update(conditionResult='reuse', conditionExpression=text(node.child_by_field_name('value'), self.content)[:MAX_TEXT])
        for index, arm in enumerate(arms):
            label = text(arm.child_by_field_name('pattern'), self.content)
            steps = self.value(arm.child_by_field_name('value'), parents + (fact['id'] + f'-a{index}',), depth + 1, True)
            self.arm(branch, fact, index, label, steps)
        return before + [branch]

    def short_circuit(self, node, operator, parents, depth, conditional):
        """Keep the right operand conditional and the skipped operand genuinely empty."""
        left = node.child_by_field_name('left')
        before = self.visit(left, parents, depth + 1, conditional)
        if not self.falls_through(before):
            return before
        branch, fact = self.branch(node, operator, text(left, self.content), parents)
        if branch is None:
            return before
        right = self.value(node.child_by_field_name('right'), parents + (fact['id'] + '-a0',), depth + 1, True)
        self.arm(branch, fact, 0, 'left true' if operator == '&&' else 'left false', right)
        self.arm(branch, fact, 1, 'skip right operand', [])
        return before + [branch]

    def propagate(self, node, parents, depth, conditional):
        """Expose ? as success versus residual return, without guessing Result/Option types."""
        before = self.visit(node.named_children[0], parents, depth + 1, conditional)
        if not self.falls_through(before):
            return before
        branch, fact = self.branch(node, '?', text(node, self.content), parents)
        if branch is None:
            return before
        self.arm(branch, fact, 0, 'continue with value', [])
        failed = self.emit('return', node, label='? propagate residual (conversion unknown)', implicit=True, implicitReturn=True, reuseExpressionResult=True)
        self.arm(branch, fact, 1, 'propagate residual', failed)
        self.warn('Rust ? residual conversion and drop cleanup require type/runtime semantics.')
        return before + [branch]

    def loop(self, node, parents, depth, conditional):
        """Evaluate for iterable once and while condition inside the repeated loop boundary."""
        if any(c.type == 'label' for c in node.named_children):
            return self.opaque(node, 'Rust labeled loops/jumps are not modeled.')
        kind = node.type.removesuffix('_expression')
        body = node.child_by_field_name('body')
        condition = node.child_by_field_name('condition')
        iterable = node.child_by_field_name('value')
        before = self.visit(iterable, parents, depth + 1, conditional) if kind == 'for' else []
        if not self.falls_through(before):
            return before
        branch_id = f'rust-{node.start_byte}-loop'
        loop_parents = parents + (branch_id + '-body',)
        header = self.content[node.start_byte:body.start_byte].decode('utf-8').strip() if body is not None else kind
        # Reserve the owner before nested steps, so exhaustion never drops an entire loop.
        loop_steps = self.emit('loop', node, label=header, controlKind='foreach' if kind == 'for' else kind,
                               condition=[], body=[], update=[], canExit=kind != 'loop',
                               hasBreakExit=False)
        if not loop_steps:
            return before
        self.branches.append({'id': branch_id, 'kind': kind, **site(node),
                              'condition': text(condition or iterable, self.content)[:MAX_TEXT],
                              'arms': [{'id': loop_parents[-1], 'label': 'loop body/condition'}],
                              'parentArmIds': list(parents)})
        checks = self.visit(condition, loop_parents, depth + 1, True)
        if not self.falls_through(checks):
            return before + checks
        contents = self.block(body, loop_parents, depth + 1, True) if body is not None else []
        loop_steps[0].update(condition=checks, body=contents, hasBreakExit=self.owned_break(contents),
                             bodyEmptyReason=None if contents else ('not-extracted' if self.truncated else 'skip'))
        return before + loop_steps

    @staticmethod
    def owned_break(steps):
        """Only extracted reachable breaks count; a nested loop owns its own jump targets."""
        for step in steps:
            if step['kind'] == 'break':
                return True
            if step['kind'] == 'branch':
                for arm in step['arms']:
                    if RustFlow.owned_break(arm['steps']):
                        return True
        return False
