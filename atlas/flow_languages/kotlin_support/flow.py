"""Kotlin lexical evaluation and structured control ownership with shared budgets."""
from hashlib import sha256
from .declarations import body_of, text

VERSION = 'kotlin-method-flow-v1'
MAX_STEPS = 180
MAX_DEPTH = 24
COMMENTS = {'line_comment', 'block_comment'}
DEFERRED = {'lambda_literal', 'annotated_lambda', 'anonymous_function', 'function_declaration',
            'class_declaration', 'object_declaration'}
OPAQUE = {'try_expression', 'object_literal', 'delegation_specifier', 'labeled_expression'}
TRANSPARENT = {'value_arguments', 'value_argument', 'parenthesized_expression',
               'binary_expression', 'unary_expression', 'assignment', 'index_expression',
               'string_literal', 'string_interpolation', 'interpolation', 'collection_literal',
               'range_expression', 'check_expression', 'as_expression', 'spread_expression',
               'type_test', 'range_test'}
LEAVES = {'identifier', 'number_literal', 'character_literal', 'boolean_literal', 'null_literal',
          'this_expression', 'super_expression', 'type_arguments', 'user_type', 'nullable_type',
          'string_content', 'escape_sequence', 'variable_declaration', 'multi_variable_declaration'}


class KotlinFlow:
    """Build calls and trees in one traversal, so source sites and branch context cannot drift."""

    def __init__(self, content, max_events, scope_key):
        """Use only host-supplied bytes; no filesystem, runtime or compiler interaction."""
        self.content = content
        self.scope = sha256(str(scope_key).encode('utf-8')).hexdigest()[:16]
        self.max_events = max(0, max_events)
        self.events, self.branches, self.terminals = [], [], []
        self.count = 0
        self.truncated = self.events_truncated = False
        self.unsupported = set()

    def label(self, node):
        """Bound every visible source expression while preserving full byte locations."""
        return text(node, self.content)[:400]

    def site(self, node):
        """Use the host's one-based UTF-8 byte columns and exclusive end positions."""
        return {'line': node.start_point.row + 1, 'column': node.start_point.column + 1,
                'endLine': node.end_point.row + 1, 'endColumn': node.end_point.column + 1}

    def step(self, node, kind, **facts):
        """Charge one global step budget, including nested controls, values and markers."""
        if self.count >= MAX_STEPS:
            self.truncated = True
            return None
        self.count += 1
        item = {'kind': kind, 'label': self.label(node), **self.site(node), **facts}
        item['label'] = item['label'][:400]
        return item

    def marker(self, node, reason, *, stops_flow=True):
        """Keep opaque/deferred syntax visible without inventing paths through its body."""
        self.unsupported.add(reason)
        item = self.step(node, 'unknown' if stops_flow else 'statement', incomplete=True, warning=reason)
        return [item] if item else []

    def arm(self, node, label, depth, context, *, value=False):
        """Describe an actual empty arm as skip; budget losses remain not-extracted."""
        steps = self.visit(node, depth + 1, context) if node is not None else []
        value_node = node
        if node is not None and node.type == 'block':
            children = self.children(node)
            value_node = children[-1] if children and children[-1].type in LEAVES | {'navigation_expression'} else None
        final_value_is_visible = bool(steps and value_node is not None and steps[-1].get('endLine') == value_node.end_point.row + 1
                                      and steps[-1].get('endColumn') == value_node.end_point.column + 1
                                      and steps[-1]['kind'] in {'call', 'branch', 'value'})
        needs_value = value_node is not None and (not steps or node.type == 'block' and not final_value_is_visible)
        if value and needs_value and not self.truncated:
            item = self.step(value_node, 'value', expression=self.label(value_node))
            if item:
                steps.append(item)
        result = {'label': label[:400], 'steps': steps}
        if not steps:
            empty = node is None or node.type == 'block' and not self.children(node)
            result['emptyReason'] = 'skip' if empty else 'not-extracted'
        return result

    def children(self, node):
        """Ignore comments as executable steps; declarations own their own documentation."""
        return [c for c in node.named_children if c.type not in COMMENTS]

    def sequence(self, nodes, depth, context):
        """Keep lexical order, without flattening nested control or skipping unreachable source."""
        result = []
        for node in nodes:
            if self.count >= MAX_STEPS:
                self.truncated = True
                break
            result.extend(self.visit(node, depth + 1, context))
        return result

    def branch(self, node, kind, context, label=None):
        """Reserve the parent first so nested limits cannot orphan a branch's children."""
        item = self.step(node, 'branch', controlKind=kind, arms=[], label=label or self.label(node))
        if item is None:
            return None, None
        identifier = 'kotlin-' + self.scope + '-branch-' + str(node.start_byte) + '-' + kind
        self.branches.append({'id': identifier, 'kind': kind, **self.site(node),
                              'condition': (label or self.label(node))[:400], 'selector': None,
                              'arms': [], 'parentArmIds': list(context)})
        return item, self.branches[-1]

    def attach(self, item, branch, arms):
        """Share stable arm IDs between control facts, tree arms and call event contexts."""
        item['arms'] = arms
        for index, arm in enumerate(arms):
            arm['id'] = branch['id'] + '-a' + str(index)
            branch['arms'].append({'id': arm['id'], 'kind': 'alternative', 'label': arm['label'],
                                   'line': item['line'], 'endLine': item['endLine']})

    def terminal(self, node, kind, depth, context):
        """Keep local return/throw/jump terminals explicit; labeled targets stay unsupported."""
        if node.child_by_field_name('label') or self.label(node).startswith(('return@', 'break@', 'continue@')):
            return self.marker(node, 'Kotlin labeled jumps require explicit target resolution.')
        result = self.sequence(self.children(node), depth + 1, context) if kind in {'return', 'throw'} else []
        item = self.step(node, kind)
        if item:
            result.append(item)
            self.terminals.append({'kind': kind, 'text': self.label(node), **self.site(node), 'branchIds': list(context)})
        return result

    def call_parts(self, node):
        """A trailing lambda wraps the same invocation in this grammar, not a second call."""
        children = self.children(node)
        callee = children[0] if node.type == 'call_expression' and children else None
        arguments = children[1:] if callee else children
        if callee and callee.type == 'call_expression' and arguments and all(c.type == 'annotated_lambda' for c in arguments):
            inner = self.children(callee)
            if any(c.type == 'value_arguments' for c in inner) and not any(c.type == 'annotated_lambda' for c in inner):
                return inner[0], inner[1:] + arguments
        return callee, arguments

    def call(self, node, depth, context, *, receiver_done=False):
        """Evaluate callee receiver and argument expressions before recording one call site."""
        callee_node, arguments = self.call_parts(node)
        result = []
        if callee_node and not receiver_done:
            if callee_node.type == 'navigation_expression':
                receiver = self.children(callee_node)[0]
                result.extend(self.visit(receiver, depth + 1, context))
            elif callee_node.type != 'identifier':
                result.extend(self.visit(callee_node, depth + 1, context))
        for child in arguments:
            if child.type == 'type_arguments':
                continue
            result.extend(self.visit(child, depth + 1, context))
        if node.type == 'constructor_delegation_call':
            callee = next((c.type for c in node.children if c.type in {'this', 'super'}), 'constructor')
            expression = callee
        else:
            expression = self.label(callee_node)
            names = self.children(callee_node) if callee_node else []
            callee = self.label(names[-1]) if callee_node and callee_node.type == 'navigation_expression' else expression
        item = self.step(node, 'call', label=(expression + '()')[:400], site=self.site(node), sourceExpression=self.label(node))
        if item is None:
            return result
        if len(self.events) >= self.max_events:
            self.events_truncated = True
            item.update(kind='statement', incomplete=True, warning='Kotlin max_events reached; call binding unavailable.')
        else:
            self.events.append({'kind': 'call', 'text': self.label(node), 'callee': callee[:400],
                                **self.site(node), 'targetNodeIds': [], 'confidence': 'source',
                                'conditional': bool(context), 'awaited': False, 'parallel': False,
                                'branchIds': list(context), 'sourceExpression': self.label(node)})
        result.append(item)
        return result

    def optional(self, node, navigation, depth, context):
        """Safe access guards the entire call, including arguments, after evaluating its receiver once."""
        receiver = self.children(navigation)[0]
        result = self.visit(receiver, depth + 1, context)
        item, branch = self.branch(node, '?.', context, self.label(receiver))
        if item is None:
            return result
        active = context + [branch['id'] + '-a0']
        if node.type == 'call_expression':
            steps = self.call(node, depth + 1, active, receiver_done=True)
        else:
            value = self.step(node, 'value', expression=self.label(node), reuseResult=True)
            steps = [value] if value else []
        null = self.step(node, 'value', label='null', expression='null')
        self.attach(item, branch, [{'label': 'receiver != null', 'steps': steps},
                                   {'label': 'receiver == null', 'steps': [null] if null else [], 'emptyReason': 'not-extracted' if null is None else None}])
        return result + [item]

    def binary(self, node, operator, depth, context):
        """Short-circuit/Elvis alternatives retain conditional calls and reuse the left result."""
        left, right = node.child_by_field_name('left'), node.child_by_field_name('right')
        result = self.visit(left, depth + 1, context)
        item, branch = self.branch(node, operator, context, self.label(left))
        if item is None:
            return result
        label = {'&&': 'left == true', '||': 'left == false', '?:': 'left == null'}[operator]
        take = self.arm(right, label, depth, context + [branch['id'] + '-a0'], value=True)
        skip = {'label': {'&&': 'left == false', '||': 'left == true', '?:': 'left != null'}[operator], 'steps': [], 'emptyReason': 'skip'}
        if operator == '?:':
            value = self.step(left, 'value', expression=self.label(left), reuseResult=True)
            skip = {'label': 'left != null', 'steps': [value] if value else []}
        self.attach(item, branch, [take, skip])
        return result + [item]

    def if_flow(self, node, depth, context):
        """Condition evaluation precedes disjoint true/false arms, including expression values."""
        condition = node.child_by_field_name('condition')
        result = self.visit(condition, depth + 1, context)
        bodies = [c for c in self.children(node) if c != condition]
        item, branch = self.branch(node, 'if', context, self.label(condition))
        if item is None:
            return result
        arms = []
        for index, label in enumerate(('true', 'false')):
            body = bodies[index] if len(bodies) > index else None
            arms.append(self.arm(body, label, depth, context + [branch['id'] + '-a' + str(index)], value=True))
        self.attach(item, branch, arms)
        return result + [item]

    def when_flow(self, node, depth, context):
        """Lower ordered when entries to nested alternatives, so later predicates stay conditional."""
        subject = next((c for c in node.named_children if c.type == 'when_subject'), None)
        entries = [c for c in node.named_children if c.type == 'when_entry']
        # Multi-condition predicates may short-circuit and invoke contains/equals; do not flatten them.
        for entry in entries:
            conditions = entry.children_by_field_name('condition')
            if len(conditions) > 1 and any(c.type not in LEAVES for c in conditions):
                return self.marker(node, 'Kotlin when comma conditions with expressions require ordered matching analysis.')
        result = self.sequence(self.children(subject), depth + 1, context) if subject else []
        result.extend(self.when_entry(entries, 0, subject, depth + 1, context))
        return result

    def when_entry(self, entries, index, subject, depth, context):
        """Evaluate one guard on the unmatched path; an else entry has no additional condition."""
        if index >= len(entries):
            return []
        if depth >= MAX_DEPTH:
            self.truncated = True
            return []
        entry = entries[index]
        arrow = next(c for c in entry.children if c.type == '->')
        conditions = entry.children_by_field_name('condition')
        body = next((c for c in self.children(entry) if c.start_byte >= arrow.end_byte), None)
        header = self.content[entry.start_byte:arrow.start_byte].decode('utf-8').strip()[:400]
        if header == 'else':
            return self.arm(body, 'else', depth, context, value=True)['steps']
        result = self.sequence(conditions, depth + 1, context)
        label = ('when ' + self.label(subject) + ': ' if subject else 'when: ') + header
        item, branch = self.branch(entry, 'when', context, label)
        if item is None:
            return result
        chosen = self.arm(body, header, depth, context + [branch['id'] + '-a0'], value=True)
        remaining = self.when_entry(entries, index + 1, subject, depth + 1, context + [branch['id'] + '-a1'])
        other = {'label': 'otherwise', 'steps': remaining}
        if not remaining:
            other['emptyReason'] = 'not-extracted' if self.truncated else 'skip'
        self.attach(item, branch, [chosen, other])
        return result + [item]

    def loop(self, node, depth, context):
        """Keep per-test condition calls in the loop and evaluate a for iterable only once."""
        children = self.children(node)
        if any(c.type == 'label' for c in children):
            return self.marker(node, 'Kotlin labeled loop requires labeled jump target resolution; body is not flattened.')
        result = []
        if node.type == 'for_statement':
            close = next(c for c in node.children if c.type == ')')
            before = [c for c in children if c.end_byte <= close.start_byte and c.type not in {'variable_declaration', 'multi_variable_declaration'}]
            result = self.sequence(before, depth + 1, context)
            condition = None
            body = next((c for c in children if c.start_byte >= close.end_byte), None)
            kind = 'foreach'
        else:
            condition = node.child_by_field_name('condition')
            body = next((c for c in children if c != condition), None)
            kind = 'do' if node.type == 'do_while_statement' else 'while'
        loop_label = self.content[node.start_byte:body.start_byte].decode('utf-8')[:400] if kind == 'foreach' and body else kind + ' (' + self.label(condition) + ')'
        item = self.step(node, 'loop', label=loop_label.strip(), controlKind=kind, condition=[], body=[], update=[], canExit=True)
        if item is None:
            return result
        scope = 'kotlin-' + self.scope + '-loop-' + str(node.start_byte)
        body_id = scope + '-a0'
        self.branches.append({'id': scope, 'kind': kind, **self.site(node),
                              'condition': self.label(condition), 'selector': None,
                              'arms': [{'id': body_id, 'kind': 'body', 'label': 'body', **self.site(body or node)}],
                              'parentArmIds': list(context)})
        # Loop-local calls are conditional on entry/repetition; the UI owns break/continue routing.
        if kind == 'do':
            item['body'] = self.visit(body, depth + 1, context)
            item['condition'] = self.visit(condition, depth + 1, context + [body_id])
        else:
            item['condition'] = self.visit(condition, depth + 1, context)
            item['body'] = self.visit(body, depth + 1, context + [body_id])
        item['conditionExpression'] = self.label(condition)
        item['bodyEmptyReason'] = 'skip' if body is None or body.type == 'block' and not self.children(body) else None
        if condition is not None and self.label(condition) == 'true':
            item['canExit'] = False
        return result + [item]

    def declaration(self, declaration):
        """Constructor delegation precedes the body; expression bodies end with an implicit return."""
        result = []
        for child in declaration.named_children:
            if child.type == 'constructor_delegation_call':
                result.extend(self.call(child, 0, []))
        body = body_of(declaration)
        result.extend(self.visit(body, 0, []))
        if body.type == 'function_body' and any(c.type == '=' for c in body.children) and not any(c.type == 'throw_expression' for c in body.named_children):
            item = self.step(body, 'return', label=('implicit return: ' + self.label(body)[1:].strip())[:400],
                             implicit=True, implicitReturn=True, reuseExpressionResult=bool(result))
            if item:
                result.append(item)
                self.terminals.append({'kind': 'return', **self.site(body), 'branchIds': [], 'implicit': True})
        return result

    def visit(self, node, depth=0, context=None):
        """Dispatch syntax explicitly; unknown nodes never leak hidden calls into a linear path."""
        if node is None:
            return []
        context = context or []
        if self.count >= MAX_STEPS or depth >= MAX_DEPTH:
            self.truncated = True
            return []
        kind = node.type
        if kind in COMMENTS:
            return []
        if kind in DEFERRED:
            return self.marker(node, 'Kotlin lambda/local declaration body is deferred; invocation, inlining and coroutine scheduling are unknown.', stops_flow=False)
        if kind in OPAQUE:
            return self.marker(node, 'Kotlin ' + kind + ' requires control/lifetime analysis; body is not flattened.')
        if kind in {'function_body', 'block'}:
            return self.sequence(self.children(node), depth, context)
        if kind == 'call_expression':
            callee, _ = self.call_parts(node)
            if callee.type == 'navigation_expression' and any(c.type == '?.' for c in callee.children):
                return self.optional(node, callee, depth, context)
            return self.call(node, depth, context)
        if kind == 'navigation_expression':
            if any(c.type == '?.' for c in node.children):
                return self.optional(node, node, depth, context)
            return self.visit(self.children(node)[0], depth + 1, context)
        if kind == 'binary_expression':
            left, right = node.child_by_field_name('left'), node.child_by_field_name('right')
            if (self.label(node.child_by_field_name('operator')) == '>' and left and left.type == 'binary_expression'
                    and self.label(left.child_by_field_name('operator')) == '<' and right and right.type == 'parenthesized_expression'):
                return self.marker(node, 'Kotlin grammar parses this generic-call/comparison syntax ambiguously; calls are not inferred.')
            operator = self.label(node.child_by_field_name('operator'))
            if operator in {'&&', '||', '?:'}:
                return self.binary(node, operator, depth, context)
        if kind == 'if_expression':
            return self.if_flow(node, depth, context)
        if kind == 'when_expression':
            return self.when_flow(node, depth, context)
        if kind in {'for_statement', 'while_statement', 'do_while_statement'}:
            return self.loop(node, depth, context)
        if kind in {'return_expression', 'throw_expression'}:
            return self.terminal(node, kind.split('_')[0], depth, context)
        if kind == 'identifier' and self.label(node) in {'break', 'continue'}:
            return self.terminal(node, self.label(node), depth, context)
        if kind == 'property_declaration':
            values = [c for c in self.children(node) if c.type not in {'modifiers', 'variable_declaration', 'multi_variable_declaration'}]
            if any(c.type in {'getter', 'setter', 'property_delegate'} for c in values):
                return self.marker(node, 'Kotlin delegated/accessor local property is not modeled.')
            result = self.sequence(values, depth + 1, context)
            item = self.step(node, 'statement', operationKind='declaration')
            return result + ([item] if item else [])
        if kind in LEAVES:
            return []
        if kind == 'assignment' and self.children(node) and '?.' in self.label(self.children(node)[0]):
            return self.marker(node, 'Kotlin safe-access assignment requires guarding the right-hand side.')
        if kind in TRANSPARENT:
            result = self.sequence(self.children(node), depth, context)
            if kind in {'assignment', 'unary_expression'}:
                item = self.step(node, 'statement')
                if item:
                    result.append(item)
            return result
        return self.marker(node, 'Kotlin syntax not extracted: ' + kind)
