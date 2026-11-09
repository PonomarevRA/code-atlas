"""TypeScript evaluation trees: static alternatives, bounded calls, and explicit opaque syntax."""
from .source import CALLABLES, TYPES, site, text

MAX_STEPS = 180
MAX_DEPTH = 24
VERSION = 'typescript-method-flow-v2'
DEFERRED = CALLABLES | TYPES | {'class_declaration', 'class', 'interface_declaration'}
TYPE_ONLY = {'type_annotation', 'type_arguments', 'type_parameters', 'type_alias_declaration',
             'interface_declaration', 'function_signature', 'method_signature', 'abstract_method_signature',
             'ambient_declaration', 'comment'}
OPAQUE = {'try_statement', 'switch_statement', 'with_statement', 'labeled_statement',
          'yield_expression', 'decorator', 'enum_declaration', 'internal_module', 'namespace_export'}
LEAVES = {'identifier', 'property_identifier', 'private_property_identifier', 'number', 'string',
          'true', 'false', 'null', 'undefined', 'this', 'super', 'regex', 'jsx_text', 'type_identifier',
          'shorthand_property_identifier', 'import'}


class Flow:
    """Emit call events and UI steps together so their ownership and exact call sites agree."""

    def __init__(self, content, max_events):
        """Track independent event and step budgets for one selected declaration."""
        self.content = content
        self.max_events = max(0, max_events)
        self.count = 0
        self.events = []
        self.branches = []
        self.terminals = []
        self.unsupported = []
        self.truncated = False
        self.events_truncated = False

    def value(self, node):
        """Keep labels and source expressions within the shared display budget."""
        return text(node, self.content)[:400]

    def step(self, kind, node, label=None, **fields):
        """Reserve one global step, including branch containers and literal outcomes."""
        if self.count >= MAX_STEPS:
            self.truncated = True
            return []
        self.count += 1
        return [dict(kind=kind, label=self.value(node) if label is None else label[:400],
                     line=node.start_point.row + 1, column=node.start_point.column + 1, **fields)]

    def unknown(self, node, reason):
        """Expose an unexpanded subtree; its calls cannot become false unconditional events."""
        message = f'{node.type} at L{node.start_point.row + 1}: {reason}'
        if message not in self.unsupported and len(self.unsupported) < MAX_STEPS:
            self.unsupported.append(message)
        return self.step('unknown', node, incomplete=True)

    def branch(self, node, kind, label, arms, context, depth):
        """Evaluate each lexical arm under its own source identity; no runtime choice is made."""
        reserved = self.step('branch', node, label, controlKind=kind, arms=[])
        if not reserved:
            return []
        identifier = f'ts:{node.start_byte}:{node.end_byte}:{kind}'
        branch = reserved[0]
        branch['id'] = identifier
        facts = {'id': identifier, 'kind': kind, 'line': node.start_point.row + 1,
                 'endLine': node.end_point.row + 1, 'condition': label[:400],
                 'selector': None, 'parentArmIds': list(context), 'arms': []}
        self.branches.append(facts)
        for index, (arm_label, callback) in enumerate(arms):
            arm_id = f'{identifier}:{index}'
            facts['arms'].append(dict(id=arm_id, kind=str(index), label=arm_label,
                                      line=node.start_point.row + 1, endLine=node.end_point.row + 1))
            steps = callback(context + [arm_id], depth + 1) if callback else []
            branch['arms'].append(dict(label=arm_label, steps=steps,
                                       emptyReason=None if steps else ('not-extracted' if self.truncated else 'skip')))
        return reserved

    def outcome(self, node, context, depth):
        """Show a literal/reference arm as a value; never invoke a previously computed call twice."""
        steps = self.visit(node, context, depth)
        if not steps and node is not None and not self.truncated:
            steps = self.step('value', node, expression=self.value(node))
        return steps

    def sequence(self, nodes, context, depth):
        """Preserve source order without flattening nested branch or loop ownership."""
        steps = []
        for node in nodes:
            if self.count >= MAX_STEPS:
                self.truncated = True
                break
            steps.extend(self.visit(node, context, depth + 1))
        return steps

    def call(self, node, context, depth, optional=False):
        """Evaluate receiver and arguments before the invocation, skipping deferred callback bodies."""
        function = node.child_by_field_name('function') or node.child_by_field_name('constructor')
        args = node.child_by_field_name('arguments')
        if args is None or args.type != 'arguments':
            return self.unknown(node, 'tagged templates and nonstandard invocation are not expanded')
        # Some installed TS grammar versions attach await to a generic callee.
        # Its suspension belongs after the complete invocation, not before its arguments.
        awaited_callee = function is not None and function.type == 'await_expression'
        if awaited_callee:
            function = function.named_children[0] if function.named_children else None
        steps = self.visit(function, context, depth + 1) if not optional else []
        steps.extend(self.sequence(args.named_children, context, depth))
        label = self.value(function) + '()'
        row = self.step('call', node, label, site=site(node), sourceExpression=self.value(node))
        if row:
            awaited = awaited_callee or self.directly_awaited(node)
            row[0]['directAwait'] = awaited
            if len(self.events) < self.max_events:
                callee = function
                if function is not None and function.type == 'member_expression':
                    callee = function.child_by_field_name('property')
                name = self.value(callee)
                if function is not None and function.type not in {'identifier', 'member_expression', 'super', 'import'}:
                    name = ''
                if function is not None and function.type == 'subscript_expression':
                    name = ''  # A computed property is not a statically resolved method name.
                self.events.append(dict(kind='call', text=label[:400], callee=name, **site(node),
                                        targetNodeIds=[], confidence='source' if name else 'unresolved',
                                        conditional=bool(context), awaited=awaited, parallel=False,
                                        branchIds=list(context), sourceExpression=self.value(node)))
            else:
                self.events_truncated = True
            steps.extend(row)
            if awaited_callee and not optional:
                steps.extend(self.step('await', node, self.value(node), parallel=False))
        return steps

    def directly_awaited(self, node):
        """Only transparent value wrappers can carry a direct awaited result to the call."""
        parent = node.parent
        while parent is not None and parent.type in {'parenthesized_expression', 'non_null_expression', 'as_expression', 'satisfies_expression', 'type_assertion'}:
            expression = parent.named_children[-1] if parent.type == 'type_assertion' else parent.named_children[0]
            if expression != node:
                return False
            node, parent = parent, parent.parent
        return parent is not None and parent.type == 'await_expression'

    def optional(self, node, context, depth):
        """Split a single optional access/call, evaluating its receiver once before the guard."""
        is_call = node.type == 'call_expression'
        function = node.child_by_field_name('function') if is_call else node
        awaited_callee = function is not None and function.type == 'await_expression'
        if awaited_callee:
            function = function.named_children[0] if function.named_children else None
        direct_call = is_call and any(c.type == '?.' for c in node.children)
        access = function if function is not None and function.type in {'member_expression', 'subscript_expression'} else None
        if direct_call:
            receiver = function
        elif access is not None and access.child_by_field_name('optional_chain') is not None:
            receiver = access.child_by_field_name('object')
        else:
            return self.unknown(node, 'lifted optional chain requires continuation-aware expansion')
        if self.has_optional(receiver):
            return self.unknown(node, 'multiple optional guards require continuation-aware expansion')
        steps = self.visit(receiver, context, depth + 1)

        def present(arm_context, arm_depth):
            """Only a present receiver evaluates the index, arguments and call."""
            result = []
            if access is not None and not direct_call:
                result.extend(self.visit(access.child_by_field_name('index'), arm_context, arm_depth))
            if is_call:
                result.extend(self.call(node, arm_context, arm_depth, optional=True))
            else:
                result.extend(self.step('value', node, expression=self.value(node)))
            return result

        steps.extend(self.branch(node, '?.', self.value(receiver) + ' != null',
                                 [('present', present), ('nullish: undefined', None)], context, depth))
        if awaited_callee:
            steps.extend(self.step('await', node, parallel=False))
        return steps

    def has_optional(self, node):
        """Inspect only the receiver chain; optional calls inside arguments have their own guards."""
        while node is not None:
            if node.child_by_field_name('optional_chain') is not None or any(c.type == '?.' for c in node.children):
                return True
            if node.type in {'call_expression', 'new_expression'}:
                node = node.child_by_field_name('function') or node.child_by_field_name('constructor')
            elif node.type in {'member_expression', 'subscript_expression'}:
                node = node.child_by_field_name('object')
            elif node.type in {'parenthesized_expression', 'non_null_expression', 'await_expression'} and node.named_children:
                node = node.named_children[0]
            else:
                return False
        return False

    def ambiguous_await_chain(self, node):
        """Reject the grammar's misplaced generic-await callee when a later receiver continues it."""
        current = node
        while current is not None:
            if current.type == 'call_expression':
                function = current.child_by_field_name('function')
                if function is not None and function.type == 'await_expression':
                    return current != node
                current = function
            elif current.type in {'member_expression', 'subscript_expression'}:
                current = current.child_by_field_name('object')
            else:
                # Parentheses are a real boundary: (await f<T>()).next() awaits f first.
                return False
        return False

    def conditional(self, node, context, depth):
        """Model ternaries and logical operators as alternatives after one left/test evaluation."""
        ternary = node.type == 'ternary_expression'
        condition = node.child_by_field_name('condition' if ternary else 'left')
        right = node.child_by_field_name('right')
        operator = 'conditional' if ternary else self.value(node.child_by_field_name('operator'))
        steps = self.visit(condition, context, depth + 1)
        if ternary:
            arms = [('true', lambda c, d: self.outcome(node.child_by_field_name('consequence'), c, d)),
                    ('false', lambda c, d: self.outcome(node.child_by_field_name('alternative'), c, d))]
        else:
            label = {'&&': 'truthy', '||': 'falsy', '??': 'nullish'}[operator]
            arms = [(label, lambda c, d: self.outcome(right, c, d)), ('reuse left result', None)]
        steps.extend(self.branch(node, operator, self.value(condition), arms, context, depth))
        return steps

    def loop(self, node, context, depth):
        """Separate one-time initialization/iterable from repeated condition, body and increment."""
        kind = {'for_statement': 'for', 'for_in_statement': 'foreach', 'while_statement': 'while', 'do_statement': 'do'}[node.type]
        prefix = []
        if node.type == 'for_in_statement':
            if any(c.type == 'await' for c in node.children):
                return self.unknown(node, 'for-await iterator suspension and cleanup are not expanded')
            left = node.child_by_field_name('left')
            if left is not None and left.type != 'identifier':
                return self.unknown(node, 'destructured iteration bindings may execute defaults')
            prefix = self.visit(node.child_by_field_name('right'), context, depth + 1)
        else:
            prefix = self.visit(node.child_by_field_name('initializer'), context, depth + 1)
        row = self.step('loop', node, self.value(node.child_by_field_name('condition')) or self.value(node),
                        controlKind=kind, condition=[], body=[], update=[], canExit=True)
        if not row:
            return prefix
        loop_id = f'ts:loop:{node.start_byte}'
        arm_id = loop_id + ':body'
        self.branches.append(dict(id=loop_id, kind=kind, **site(node),
                                  condition=self.value(node.child_by_field_name('condition')),
                                  selector=None, parentArmIds=list(context),
                                  arms=[dict(id=arm_id, kind='body', label='body', **site(node))]))
        loop_context = context + [arm_id]
        # This is lexical evaluation order, not a flattened execution trace.
        body = node.child_by_field_name('body')
        condition = node.child_by_field_name('condition')
        if kind == 'do':
            row[0]['body'] = self.visit(body, loop_context, depth + 1)
            row[0]['condition'] = self.visit(condition, loop_context, depth + 1)
        else:
            row[0]['condition'] = self.visit(condition, context, depth + 1)
            row[0]['body'] = self.visit(body, loop_context, depth + 1)
        row[0]['update'] = self.visit(node.child_by_field_name('increment'), loop_context, depth + 1)
        row[0]['canExit'] = kind != 'for' or condition is not None and condition.type != 'empty_statement'
        if self.has_branch(row[0]['condition']):
            row[0].update(conditionResult='reuse', conditionExpression=self.value(condition))
        return prefix + row

    def has_branch(self, steps):
        """Detect a separately computed conditional value consumed by if/loop without re-evaluation."""
        return any(step['kind'] == 'branch' for step in steps)

    def visit(self, node, context, depth):
        """Dispatch known executable syntax; unknown syntax stays opaque instead of inventing paths."""
        if node is None or node.type in TYPE_ONLY:
            return []
        if self.count >= MAX_STEPS:
            self.truncated = True
            return []
        if depth > MAX_DEPTH:
            self.truncated = True
            return self.unknown(node, 'depth budget exceeded')
        kind = node.type
        if kind in TYPES:
            return self.unknown(node, 'class heritage, computed names and static initialization are not expanded')
        if kind in DEFERRED:
            name = node.child_by_field_name('name')
            computed = self.visit(name, context, depth + 1) if name is not None and name.type == 'computed_property_name' else []
            return computed + self.step('statement', node, deferred=True)
        if kind in OPAQUE:
            return self.unknown(node, 'control transfer, cleanup or suspension is not expanded')
        if kind in LEAVES or kind in {'empty_statement', 'optional_chain'}:
            return []
        if kind in {'statement_block', 'program', 'arguments', 'sequence_expression'}:
            return self.sequence(node.named_children, context, depth)
        if kind in {'call_expression', 'member_expression', 'subscript_expression'} and self.ambiguous_await_chain(node):
            return self.unknown(node, 'generic await receiver continuation has ambiguous grammar scope')
        if kind in {'call_expression', 'member_expression', 'subscript_expression', 'new_expression'} and self.has_optional(node):
            return self.optional(node, context, depth)
        if kind in {'call_expression', 'new_expression'}:
            return self.call(node, context, depth)
        if kind == 'ternary_expression' or kind == 'binary_expression' and self.value(node.child_by_field_name('operator')) in {'&&', '||', '??'}:
            return self.conditional(node, context, depth)
        if kind == 'if_statement':
            condition = node.child_by_field_name('condition')
            steps = self.visit(condition, context, depth + 1)
            reuse = self.has_branch(steps)
            choice = self.branch(node, 'if', self.value(condition),
                                     [('true', lambda c, d: self.visit(node.child_by_field_name('consequence'), c, d)),
                                      ('false', lambda c, d: self.visit(node.child_by_field_name('alternative'), c, d))], context, depth)
            if choice and reuse:
                choice[0].update(conditionResult='reuse', conditionExpression=self.value(condition))
            return steps + choice
        if kind == 'else_clause':
            return self.sequence(node.named_children, context, depth)
        if kind in {'for_statement', 'for_in_statement', 'while_statement', 'do_statement'}:
            return self.loop(node, context, depth)
        if kind in {'return_statement', 'throw_statement', 'break_statement', 'continue_statement'}:
            if kind in {'break_statement', 'continue_statement'} and node.named_children:
                return self.unknown(node, 'labelled jump target is not expanded')
            steps = self.sequence(node.named_children, context, depth)
            terminal = kind.removesuffix('_statement')
            row = self.step(terminal, node)
            if row:
                self.terminals.append(dict(kind=terminal, **site(node), branchIds=list(context)))
            return steps + row
        if kind in {'lexical_declaration', 'variable_declaration'}:
            return self.sequence(node.named_children, context, depth)
        if kind == 'variable_declarator':
            name = node.child_by_field_name('name')
            if name is not None and name.type != 'identifier':
                return self.unknown(node, 'destructuring bindings may execute defaults and computed keys')
            steps = self.visit(node.child_by_field_name('value'), context, depth + 1)
            return steps + self.step('statement', node)
        if kind == 'expression_statement':
            steps = self.sequence(node.named_children, context, depth)
            return steps or self.step('statement', node)
        if kind == 'await_expression':
            steps = self.sequence(node.named_children, context, depth)
            return steps + self.step('await', node, parallel=False)
        if kind == 'augmented_assignment_expression' and self.value(node.child_by_field_name('operator')) in {'&&=', '||=', '??='}:
            return self.unknown(node, 'logical assignment reference reuse is not expanded')
        if kind in {'assignment_expression', 'augmented_assignment_expression', 'update_expression'}:
            left = node.child_by_field_name('left')
            if left is not None and left.type in {'object_pattern', 'array_pattern'}:
                return self.unknown(node, 'destructuring assignments may execute defaults and computed keys')
            steps = self.sequence(node.named_children, context, depth)
            return steps + self.step('statement', node)
        if kind in {'as_expression', 'satisfies_expression', 'non_null_expression'}:
            return self.visit(node.named_children[0], context, depth + 1)
        if kind == 'type_assertion':
            return self.visit(node.named_children[-1], context, depth + 1)
        if kind == 'member_expression':
            return self.visit(node.child_by_field_name('object'), context, depth + 1)
        if kind == 'subscript_expression':
            return self.sequence([node.child_by_field_name('object'), node.child_by_field_name('index')], context, depth)
        if kind == 'unary_expression':
            steps = self.sequence(node.named_children, context, depth)
            operator = self.value(node.child_by_field_name('operator'))
            return steps + self.step('statement', node) if operator == 'delete' else steps
        if kind in {'parenthesized_expression', 'binary_expression', 'array', 'object',
                    'pair', 'computed_property_name', 'spread_element', 'template_string', 'template_substitution', 'jsx_expression',
                    'jsx_element', 'jsx_self_closing_element', 'jsx_opening_element', 'jsx_closing_element', 'jsx_attribute'}:
            return self.sequence(node.named_children, context, depth)
        return self.unknown(node, 'syntax has no verified evaluation rule')

    def build(self, declaration):
        """Add conditional parameter defaults, then the body, with explicit declaration-only status."""
        body = declaration.child_by_field_name('body')
        steps = []
        if 'generator' in declaration.type or any(c.type == '*' for c in declaration.children):
            steps = self.unknown(declaration, 'generator body runs on iterator resume, not ordinary call')
        elif body is not None:
            params = declaration.child_by_field_name('parameters')
            for parameter in params.named_children if params is not None else []:
                default = parameter.child_by_field_name('value')
                pattern = parameter.child_by_field_name('pattern')
                simple_rest = pattern is not None and pattern.type == 'rest_pattern' and all(child.type == 'identifier' for child in pattern.named_children)
                simple_pattern = pattern is None or pattern.type in {'identifier', 'this'} or simple_rest
                if not simple_pattern:
                    steps.extend(self.unknown(parameter, 'destructured parameter defaults are not expanded'))
                elif default is not None:
                    steps.extend(self.branch(parameter, 'parameter-default', self.value(pattern) + ' === undefined',
                                             [('missing', lambda c, d, value=default: self.outcome(value, c, d)), ('provided', None)], [], 0))
            steps.extend(self.visit(body, [], 0))
            if body.type != 'statement_block':
                row = self.step('return', body, 'return ' + self.value(body))
                steps.extend(row)
                if row:
                    self.terminals.append(dict(kind='return', **site(body), branchIds=[]))
        return dict(version=VERSION, status='parsed' if body is not None else 'declaration-only',
                    steps=steps, truncated=self.truncated, unsupported=self.unsupported,
                    sourceKind='working-tree-ast', limits=dict(maxAstNodes=20_000, maxSteps=MAX_STEPS, maxDepth=MAX_DEPTH, maxEvents=self.max_events),
                    warning='Static lexical alternatives; callbacks are deferred; implicit exceptions, getters and runtime dispatch are not inferred.')
