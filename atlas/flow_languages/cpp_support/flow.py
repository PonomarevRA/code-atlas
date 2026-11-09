"""C++ lexical evaluation and structured controls with explicit unknown boundaries."""
import re
from .declarations import text

MAX_STEPS = 180
MAX_DEPTH = 24
TERMINALS = {'return_statement': 'return', 'throw_statement': 'throw',
             'break_statement': 'break', 'continue_statement': 'continue'}
OPAQUE = {'try_statement', 'co_return_statement', 'co_yield_statement', 'co_await_expression',
          'goto_statement', 'labeled_statement', 'asm_statement', 'gnu_asm_expression',
          'new_expression', 'delete_expression', 'requires_expression', 'fold_expression',
          'static_assert_declaration'}
UNEVALUATED = {'sizeof_expression', 'alignof_expression', 'decltype', 'noexcept_expression', 'typeid_expression'}
DEFERRED = {'lambda_expression', 'function_definition', 'class_specifier', 'struct_specifier'}


def site(node):
    """Match host call sites using 1-based UTF-8 byte coordinates and exclusive ends."""
    return {'line': node.start_point.row + 1, 'column': node.start_point.column + 1,
            'endLine': node.end_point.row + 1, 'endColumn': node.end_point.column + 1}


class Flow:
    """Build one tree and its events together so call identities cannot drift."""
    warning = ('Static C++ syntax only. Sibling arguments/operators use lexical presentation order, '
               'not guaranteed evaluation order. Overloads, implicit calls, external macros, '
               'RAII/destructors and exception cleanup are unresolved.')

    def __init__(self, source, max_events, macro_names):
        """Keep all budgets and observations local to one selected method."""
        self.source = source
        self.max_events = max(0, max_events)
        self.macro_names = macro_names
        self.events = []
        self.branches = []
        self.terminals = []
        self.count = 0
        self.truncated = False
        self.events_truncated = False
        self.unsupported = ['Implicit constructors, overloaded operators, RAII/destructors and exception cleanup are not resolved.',
                            'External preprocessing/macros and runtime overload binding are not resolved.']

    def note(self, reason):
        """Retain each visible limitation once, with bounded reason count."""
        if reason not in self.unsupported and len(self.unsupported) < 40:
            self.unsupported.append(reason)

    def label(self, node):
        """Bound every displayed source expression independently of source ranges."""
        return text(node, self.source)[:400]

    def step(self, node, kind, **facts):
        """Charge every visible row, including value-only branch results."""
        if self.count >= MAX_STEPS:
            self.truncated = True
            self.note('C++ method step budget exceeded.')
            return []
        self.count += 1
        return [{'kind': kind, 'label': self.label(node), 'line': node.start_point.row + 1,
                 'column': node.start_point.column + 1, **facts}]

    def opaque(self, node, reason):
        """Keep unsupported source visible without inventing execution of its children."""
        self.note(reason)
        return self.step(node, 'statement', incomplete=True, unsupported=reason)

    def sequence(self, children, depth, paths):
        """Visit sibling statements; direct jumps stop the local lexical continuation."""
        result = []
        for child in children:
            result.extend(self.build(child, depth + 1, paths))
            if self.truncated or child.type in TERMINALS or child.type in {'goto_statement', 'co_return_statement'}:
                break
        return result

    def arm(self, node, label, depth, paths, value=False):
        """Represent actual skips separately from expressions returning simple values."""
        result = self.build(node, depth + 1, paths)
        if not result and value and node is not None and not self.truncated:
            result = self.step(node, 'value', expression=self.label(node))
        arm = {'label': label, 'steps': result}
        if not result:
            empty = node is None or node.type in {'compound_statement', 'empty_statement', 'comment'} and not node.named_children
            arm['emptyReason'] = 'skip' if empty else 'not-extracted'
        return arm

    def branch(self, node, control, label, arms, paths):
        """Publish control metadata with arm IDs used by conditional call events."""
        identifier = f'cpp-branch-{node.start_byte}'
        metadata = {'id': identifier, 'kind': control, 'line': node.start_point.row + 1,
                    'endLine': node.end_point.row + 1, 'condition': label,
                    'parentArmIds': list(paths), 'arms': []}
        for index, arm in enumerate(arms):
            arm_id = f'{identifier}-a{index}'
            metadata['arms'].append({'id': arm_id, 'kind': 'alternative', 'label': arm['label'],
                                     'line': node.start_point.row + 1, 'endLine': node.end_point.row + 1})
        self.branches.append(metadata)
        return self.step(node, 'branch', controlKind=control, label=label, arms=arms)

    def choice(self, node, depth, paths, conditional=False):
        """Evaluate the predicate once, then retain both possible statement/value arms."""
        condition = node.child_by_field_name('condition')
        result = self.build(condition, depth + 1, paths)
        identifier = f'cpp-branch-{node.start_byte}'
        arms = []
        for index, field in enumerate(('consequence', 'alternative')):
            child = node.child_by_field_name(field)
            arm_paths = paths + (f'{identifier}-a{index}',)
            arms.append(self.arm(child, 'true' if index == 0 else 'false', depth, arm_paths, conditional))
        kind = 'conditional' if conditional else 'if'
        return result + self.branch(node, kind, self.label(condition), arms, paths)

    def short_circuit(self, node, operator, depth, paths):
        """Model builtin logical alternatives; overloaded logical operators remain unknown."""
        self.note('Logical &&/|| alternatives assume builtin operators; overloaded operators require type analysis.')
        left, right = node.child_by_field_name('left'), node.child_by_field_name('right')
        result = self.build(left, depth + 1, paths)
        identifier = f'cpp-branch-{node.start_byte}'
        arm = self.arm(right, 'left true' if operator == '&&' else 'left false', depth,
                       paths + (f'{identifier}-a0',), True)
        skip = {'label': 'short-circuit skip', 'steps': [], 'emptyReason': 'skip'}
        return result + self.branch(node, operator, self.label(left), [arm, skip], paths)

    def call(self, node, depth, paths):
        """Visit receiver/arguments before a source-only call; never guess a target raw ID."""
        function = node.child_by_field_name('function')
        function_text = self.label(function)
        callee = re.split(r'::|->|\.', function_text)[-1].split('<', 1)[0]
        if function_text in {'typeid', '__builtin_choose_expr', '__builtin_constant_p'}:
            return self.opaque(node, 'Type-dependent/compile-time builtin evaluation is not expanded: ' + function_text)
        if callee in self.macro_names:
            return self.opaque(node, 'Macro expansion and argument evaluation are unknown: ' + callee)
        if function and function.type == 'lambda_expression':
            return self.opaque(node, 'Immediately invoked lambda body/captures are not expanded.')
        result = self.build(function, depth + 1, paths)
        arguments = node.child_by_field_name('arguments')
        args = arguments.named_children if arguments else []
        for argument in args:
            result.extend(self.build(argument, depth + 1, paths))
        if len(args) > 1:
            self.note('Sibling argument evaluation order is unspecified; shown in lexical order only.')
        if len(self.events) >= self.max_events:
            self.events_truncated = True
            self.truncated = True
            return result + self.opaque(node, 'C++ call event budget exceeded.')
        if self.count >= MAX_STEPS:
            self.truncated = True
            return result
        location = site(node)
        self.events.append({'kind': 'call', 'text': (function_text + '()')[:400], 'callee': callee,
                            **location, 'targetNodeIds': [], 'confidence': 'unresolved',
                            'conditional': bool(paths), 'awaited': False, 'parallel': False,
                            'branchIds': list(paths), 'sourceExpression': self.label(node),
                            'arguments': [{'expression': self.label(a), **site(a), 'kind': 'lexical-source'} for a in args[:40]],
                            'argumentsTruncated': len(args) > 40,
                            'evaluationOrder': 'lexical-not-runtime'})
        return result + self.step(node, 'call', label=(function_text + '()')[:400],
                                  site=location, sourceExpression=self.label(node))

    def loop(self, node, depth, paths):
        """Retain initialization, repeated condition, body and continue/update ownership."""
        kind = {'for_statement': 'for', 'for_range_loop': 'foreach',
                'while_statement': 'while', 'do_statement': 'do'}[node.type]
        result = self.build(node.child_by_field_name('initializer'), depth + 1, paths)
        if kind == 'foreach':
            result.extend(self.build(node.child_by_field_name('right'), depth + 1, paths))
            self.note('Range-for implicit begin/end/iterator calls are not resolved.')
        body_node = node.child_by_field_name('body')
        loop_id = f'cpp-loop-{node.start_byte}'
        loop_paths = paths + (loop_id + '-a0',)
        self.branches.append({'id': loop_id, 'kind': kind, 'line': node.start_point.row + 1,
                              'endLine': node.end_point.row + 1, 'parentArmIds': list(paths),
                              'arms': [{'id': loop_id + '-a0', 'kind': 'body', 'label': 'iteration',
                                        'line': body_node.start_point.row + 1,
                                        'endLine': body_node.end_point.row + 1}]})
        # Construct in lexical evaluation order, including do's first body pass.
        if kind == 'do':
            body = self.build(body_node, depth + 1, loop_paths)
            condition = self.build(node.child_by_field_name('condition'), depth + 1, loop_paths)
        else:
            condition = self.build(node.child_by_field_name('condition'), depth + 1, paths)
            body = self.build(body_node, depth + 1, loop_paths)
        update_node = node.child_by_field_name('update')
        update = self.build(update_node, depth + 1, loop_paths)
        if update_node is not None and not self.truncated and (not update or update_node.type in {'assignment_expression', 'update_expression', 'comma_expression'}):
            update.extend(self.step(update_node, 'statement'))
        condition_node = node.child_by_field_name('condition')
        label = self.source[node.start_byte:body_node.start_byte].decode('utf-8')[:400] if body_node else self.label(node)
        return result + self.step(node, 'loop', controlKind=kind, label=label, condition=condition,
                                  body=body, update=update, canExit=kind != 'for' or condition_node is not None,
                                  conditionExpression=self.label(condition_node))

    def switch(self, node, depth, paths):
        """Extract terminated case arms; fallthrough remains one opaque source statement."""
        body = node.child_by_field_name('body')
        cases = [c for c in body.named_children if c.type == 'case_statement'] if body else []
        sections = []
        labels = []
        for case in cases:
            value = case.child_by_field_name('value')
            labels.append('case ' + self.label(value) if value else 'default')
            statements = [c for c in case.named_children if c != value and c.type != 'comment']
            if not statements:
                continue
            # A continuing case can execute later cases; mutually exclusive arms would lie.
            if statements[-1].type not in TERMINALS:
                return self.opaque(node, 'C++ switch fallthrough or compound case termination is not modeled.')
            sections.append((' / '.join(labels), statements, case))
            labels = []
        if labels:
            sections.append((' / '.join(labels), [], cases[-1]))
        result = self.build(node.child_by_field_name('condition'), depth + 1, paths)
        arms = []
        identifier = f'cpp-branch-{node.start_byte}'
        for index, (label, statements, case) in enumerate(sections):
            steps = self.sequence(statements, depth + 1, paths + (f'{identifier}-a{index}',))
            arms.append({'label': label, 'steps': steps, **({'emptyReason': 'skip'} if not steps else {})})
        if not any('default' in label for label, _, _ in sections):
            arms.append({'label': 'no match', 'steps': [], 'emptyReason': 'skip'})
        return result + self.branch(node, 'switch', self.label(node.child_by_field_name('condition')), arms, paths)

    def build(self, node, depth=0, paths=()):
        """Dispatch syntax conservatively; deferred/unevaluated source never becomes outer calls."""
        if node is None or node.type == 'comment':
            return []
        if self.count >= MAX_STEPS or depth >= MAX_DEPTH:
            self.truncated = True
            self.note('C++ method step/depth budget exceeded.')
            return []
        kind = node.type
        if kind in DEFERRED:
            return self.opaque(node, 'Deferred lambda/capture or local type body is not expanded.')
        if kind in UNEVALUATED:
            return self.opaque(node, 'Unevaluated/type-dependent expression is not expanded: ' + kind)
        if kind == 'co_return_statement':
            reason = 'Coroutine return/suspension machinery and operand execution are not expanded.'
            self.note(reason)
            self.terminals.append({'kind': 'return', **site(node), 'branchIds': list(paths)})
            return self.step(node, 'return', incomplete=True, unsupported=reason)
        if kind in OPAQUE or kind.startswith('preproc_'):
            return self.opaque(node, 'Unsupported execution boundary: ' + kind)
        if kind == 'compound_statement':
            return self.sequence(node.named_children, depth, paths)
        if kind == 'call_expression':
            return self.call(node, depth, paths)
        if kind in {'if_statement', 'conditional_expression'}:
            return self.choice(node, depth, paths, kind == 'conditional_expression')
        if kind in {'for_statement', 'for_range_loop', 'while_statement', 'do_statement'}:
            return self.loop(node, depth, paths)
        if kind == 'switch_statement':
            return self.switch(node, depth, paths)
        if kind == 'binary_expression':
            operator = self.label(node.child_by_field_name('operator'))
            if operator in {'&&', '||'}:
                return self.short_circuit(node, operator, depth, paths)
        if kind in TERMINALS:
            result = self.sequence(node.named_children, depth, paths)
            terminal = TERMINALS[kind]
            self.terminals.append({'kind': terminal, **site(node), 'branchIds': list(paths)})
            return result + self.step(node, terminal)
        if kind == 'else_clause':
            return self.sequence(node.named_children, depth, paths)
        result = self.sequence(node.named_children, depth, paths)
        if kind in {'declaration', 'expression_statement'}:
            # A call-only statement is already visible; declarations/assignments retain their binding.
            only_call = kind == 'expression_statement' and len(node.named_children) == 1 and node.named_children[0].type == 'call_expression'
            if not only_call:
                result.extend(self.step(node, 'statement'))
        return result
