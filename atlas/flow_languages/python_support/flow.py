"""Bounded lexical Python evaluation trees with explicit deferred/unsupported boundaries."""
import ast

MAX_STEPS = 180
MAX_DEPTH = 24
TERMINALS = (ast.Return, ast.Raise, ast.Break, ast.Continue)
DEFERRED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


class Flow:
    """Build trees and call events together so each call has exactly one shared source site."""

    def __init__(self, source, max_events):
        """Share step, recursion and event budgets across the selected callable only."""
        self.source = source
        self.max_events = max(0, max_events)
        self.count = 0
        self.truncated = False
        self.events_truncated = False
        self.events = []
        self.branches = []
        self.terminals = []
        self.unsupported = set()

    def step(self, node, kind, **facts):
        """Allocate every visible row from one shared budget before descending into children."""
        if self.count >= MAX_STEPS:
            self.truncated = True
            return None
        self.count += 1
        source_label = self.source.fragment(node, limit=401)
        result = {'kind': kind, **self.source.span(node), 'label': source_label, **facts}
        result['labelTruncated'] = len(result['label']) > 400
        result['label'] = result['label'][:400]
        return result

    def row(self, node, kind, **facts):
        """Return an optional row as a list suitable for ordered concatenation."""
        step = self.step(node, kind, **facts)
        return [step] if step else []

    def opaque(self, node, reason):
        """Keep a visible unknown-completion barrier; never flatten the unrepresented body."""
        self.unsupported.add(reason)
        return self.row(node, 'unknown', unsupported=reason, sourceExpression=self.source.fragment(node))

    def bounded(self, depth):
        """Stop before exceeding recursion or row limits; report omitted source explicitly."""
        if depth >= MAX_DEPTH or self.count >= MAX_STEPS:
            self.truncated = True
            return False
        return True

    def branch(self, node, kind, labels):
        """Reserve branch identity before its arms so nested call contexts refer to real arms."""
        step = self.step(node, 'branch', controlKind=kind, arms=[])
        if step is None:
            return None, []
        identifier = f'python-{node.lineno}-{node.col_offset}-{self.count}'
        arms = [{'id': identifier + f'-a{i}', 'label': label, 'steps': []} for i, label in enumerate(labels)]
        step.update(id=identifier, arms=arms)
        self.branches.append({'id': identifier, 'kind': kind, 'label': step['label'],
                              **self.source.span(node), 'arms': arms})
        return step, arms

    def fill_arm(self, arm, steps, known_empty=False):
        """An actually skipped arm differs from source omitted by a resource limit."""
        arm['steps'] = steps
        if not steps:
            arm['emptyReason'] = 'not-extracted' if self.truncated and not known_empty else 'skip'

    def value(self, node, depth, context, awaited=False):
        """Literal/property alternatives remain visible without duplicating invocation rows."""
        steps = self.expression(node, depth, context, awaited=awaited)
        if not steps and not self.truncated:
            steps = self.row(node, 'value', expression=self.source.fragment(node))
        return steps

    def expression(self, node, depth=0, context=(), awaited=False):
        """Follow Python's evaluation order, preserving conditional and deferred subexpressions."""
        if node is None or not self.bounded(depth):
            return []
        if isinstance(node, ast.Call):
            return self.call(node, depth, context, awaited)
        if isinstance(node, ast.Await):
            result = self.expression(node.value, depth + 1, context, awaited=True)
            result.extend(self.row(node, 'await', parallel=False))
            return result
        if isinstance(node, ast.BoolOp):
            if awaited:
                self.unsupported.add('Await of a short-circuit-selected value has unknown per-call await binding.')
            return self.boolean(node, depth, context)
        if isinstance(node, ast.IfExp):
            result = self.expression(node.test, depth + 1, context)
            branch, arms = self.branch(node, 'conditional', ['true', 'false'])
            if branch:
                for arm, expression in zip(arms, (node.body, node.orelse)):
                    self.fill_arm(arm, self.value(expression, depth + 1, (*context, arm['id']), awaited=awaited))
                result.append(branch)
            return result
        if isinstance(node, ast.Compare) and len(node.ops) > 1:
            return self.comparison(node, depth, context)
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp)):
            return self.comprehension(node, depth, context)
        if isinstance(node, ast.GeneratorExp):
            result = self.expression(node.generators[0].iter, depth + 1, context)
            self.unsupported.add('Generator expression body is deferred until iteration.')
            result.extend(self.row(node, 'statement', deferred=True))
            return result
        if isinstance(node, ast.Lambda):
            result = []
            for default in [*node.args.defaults, *node.args.kw_defaults]:
                result.extend(self.expression(default, depth + 1, context))
            result.extend(self.row(node, 'statement', label='lambda: deferred body', deferred=True))
            return result
        if isinstance(node, ast.Dict):
            result = []
            # ast stores keys and values separately; evaluation interleaves each pair.
            for key, value in zip(node.keys, node.values):
                result.extend(self.expression(key, depth + 1, context))
                result.extend(self.expression(value, depth + 1, context))
            return result
        if isinstance(node, ast.NamedExpr):
            result = self.expression(node.value, depth + 1, context)
            result.extend(self.row(node, 'statement', operationKind='assignment'))
            return result
        if isinstance(node, (ast.Yield, ast.YieldFrom)):
            return self.opaque(node, 'Generator suspension/resumption is not represented.')
        # Remaining expression fields use ast's source/evaluation order (receiver,
        # operands, elements, slice). No declaration or statement reaches this walk.
        result = []
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.expr, ast.Slice)):
                result.extend(self.expression(child, depth + 1, context))
        return result

    def call(self, node, depth, context, awaited):
        """Evaluate receiver, positional/starred arguments and keywords before invoking the callee."""
        if isinstance(node.func, ast.Lambda):
            self.unsupported.add('Immediately invoked lambda body is not expanded.')
        result = self.expression(node.func, depth + 1, context)
        for arg in node.args:
            result.extend(self.expression(arg, depth + 1, context))
        for keyword in node.keywords:
            result.extend(self.expression(keyword.value, depth + 1, context))
        if self.truncated:
            return result
        if len(self.events) >= self.max_events:
            self.events_truncated = self.truncated = True
            return result
        site = self.source.span(node)
        step = self.step(node, 'call', site=site, sourceExpression=self.source.fragment(node))
        if step:
            callee = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, 'attr', '')
            self.events.append({'kind': 'call', 'text': step['label'], 'callee': callee,
                                **site, 'targetNodeIds': [], 'confidence': 'source' if callee else 'unresolved',
                                'sourceExpressionTruncated': step['labelTruncated'],
                                'conditional': bool(context), 'awaited': awaited, 'parallel': False,
                                'branchIds': list(context), 'sourceExpression': step['sourceExpression'],
                                'argumentBoundary': 'lexical expressions; values and binding are unknown'})
            result.append(step)
        return result

    def boolean(self, node, depth, context):
        """Nest later operands inside the preceding truth test, preserving n-ary short circuit."""
        operator = 'and' if isinstance(node.op, ast.And) else 'or'

        def tail(index, current, level):
            """Each later operand can run only if all previous operands permit it."""
            if not self.bounded(level):
                return []
            steps = self.value(node.values[index], level + 1, current)
            if index + 1 == len(node.values):
                return steps
            branch, arms = self.branch(node.values[index], operator, ['true', 'false'])
            if branch:
                active = arms[0] if operator == 'and' else arms[1]
                skipped = arms[1] if operator == 'and' else arms[0]
                self.fill_arm(active, tail(index + 1, (*current, active['id']), level + 1))
                self.fill_arm(skipped, [], known_empty=True)
                steps.append(branch)
            return steps

        return tail(0, context, depth)

    def comparison(self, node, depth, context):
        """Evaluate middle operands once; later comparisons are conditional on earlier success."""
        result = self.expression(node.left, depth + 1, context)

        def tail(index, current, level):
            """The right operand of each additional comparator is short-circuited."""
            if not self.bounded(level):
                return []
            steps = self.expression(node.comparators[index], level + 1, current)
            if index + 1 == len(node.comparators):
                return steps
            branch, arms = self.branch(node.comparators[index], 'comparison', ['previous comparison true', 'false'])
            if branch:
                branch.update(label=self.source.through(node.left, node.comparators[index]), conditionResult='reuse')
                self.fill_arm(arms[0], tail(index + 1, (*current, arms[0]['id']), level + 1))
                self.fill_arm(arms[1], [], known_empty=True)
                steps.append(branch)
            return steps

        result.extend(tail(0, context, depth + 1))
        return result

    def comprehension(self, node, depth, context):
        """Represent eager comprehension iteration and guards without hoisting body calls."""
        if any(generator.is_async for generator in node.generators):
            return self.opaque(node, 'Async comprehension iteration/cleanup is not represented.')

        def generator_at(index, current, level):
            """Each inner iterable belongs to the preceding generator's body."""
            if not self.bounded(level):
                return []
            generator = node.generators[index]
            steps = self.expression(generator.iter, level + 1, current)
            loop = self.step(generator.iter, 'loop', controlKind='foreach', label='for ' + self.source.fragment(generator.target),
                             condition=[], body=[], update=[], canExit=True)
            if loop is None:
                return steps
            identifier = f'python-comp-{generator.iter.lineno}-{generator.iter.col_offset}-{self.count}'
            body_context = (*current, identifier + '-a0')
            body = loop['body']
            body.extend(self.expression(generator.target, level + 1, body_context))
            self.branches.append({'id': identifier, 'kind': 'foreach', **self.source.span(generator.iter),
                                  'label': loop['label'], 'arms': [{'id': body_context[-1], 'label': 'iteration'}]})
            for test in generator.ifs:
                level += 1
                if not self.bounded(level):
                    return [*steps, loop]
                body.extend(self.expression(test, level + 1, body_context))
                branch, arms = self.branch(test, 'if', ['true', 'skip item'])
                if branch is None:
                    return [*steps, loop]
                body.append(branch)
                self.fill_arm(arms[1], [], known_empty=True)
                body_context = (*body_context, arms[0]['id'])
                body = arms[0]['steps']
            if index + 1 < len(node.generators):
                body.extend(generator_at(index + 1, body_context, level + 1))
            elif isinstance(node, ast.DictComp):
                body.extend(self.expression(node.key, level + 1, body_context))
                body.extend(self.expression(node.value, level + 1, body_context))
            else:
                body.extend(self.value(node.elt, level + 1, body_context))
            steps.append(loop)
            return steps

        return generator_at(0, context, depth + 1)

    def statements(self, statements, depth=0, context=()):
        """Stop at definite lexical exits; unknown barriers preserve uncertainty about completion."""
        result = []
        for node in statements:
            if not self.bounded(depth):
                break
            result.extend(self.statement(node, depth, context))
            if self.exits(node) or result and result[-1]['kind'] == 'unknown':
                break
        return result

    def exits(self, node):
        """Recognize only certain local exits, without evaluating conditions or assuming loops run."""
        if isinstance(node, TERMINALS):
            return True
        if isinstance(node, ast.If) and node.body and node.orelse:
            body_exits = any(self.exits(statement) for statement in node.body)
            else_exits = any(self.exits(statement) for statement in node.orelse)
            return body_exits and else_exits
        return False

    def statement(self, node, depth, context):
        """Dispatch statements explicitly so hidden bodies never become unconditional calls."""
        if isinstance(node, ast.If):
            result = self.expression(node.test, depth + 1, context)
            branch, arms = self.branch(node, 'if', ['true', 'false'])
            if branch:
                branch['label'] = self.source.fragment(node.test)
                if isinstance(node.test, (ast.BoolOp, ast.IfExp, ast.Compare)):
                    branch['conditionResult'] = 'reuse'
                for arm, body in zip(arms, (node.body, node.orelse)):
                    self.fill_arm(arm, self.statements(body, depth + 1, (*context, arm['id'])), known_empty=not body)
                result.append(branch)
            return result
        if isinstance(node, (ast.For, ast.While, ast.AsyncFor)):
            return self.loop(node, depth, context)
        if isinstance(node, DEFERRED):
            # Class creation, defaults, annotations and decorators can execute code;
            # claiming the declaration has no effects would be equally misleading.
            if self.definition_effects_unknown(node):
                return self.opaque(node, 'Nested class/decorated/annotated declaration effects are not represented.')
            result = []
            for default in [*node.args.defaults, *node.args.kw_defaults]:
                result.extend(self.expression(default, depth + 1, context))
            result.extend(self.row(node, 'statement', label='def ' + node.name + ': deferred body', deferred=True))
            return result
        if isinstance(node, (ast.Try, ast.TryStar, ast.With, ast.AsyncWith, ast.Match)):
            return self.opaque(node, type(node).__name__ + ' exception/cleanup/pattern paths are not represented.')
        if isinstance(node, ast.Assert):
            return self.opaque(node, 'Assert is conditional and may be removed by optimization.')
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return self.opaque(node, 'Import effects and imported module bodies are not analyzed.')
        if isinstance(node, TERMINALS):
            result = []
            for expression in (getattr(node, 'value', None), getattr(node, 'exc', None), getattr(node, 'cause', None)):
                result.extend(self.expression(expression, depth + 1, context))
            kind = {ast.Return: 'return', ast.Raise: 'throw', ast.Break: 'break', ast.Continue: 'continue'}[type(node)]
            terminal = self.step(node, kind)
            if terminal:
                self.terminals.append({**terminal, 'branchIds': list(context)})
                result.append(terminal)
            return result
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            return self.assignment(node, depth, context)
        if isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return []
            result = self.expression(node.value, depth + 1, context)
            return result or self.row(node, 'statement')
        if isinstance(node, (ast.Pass, ast.Global, ast.Nonlocal)):
            return self.row(node, 'statement')
        if isinstance(node, ast.Delete):
            result = []
            for target in node.targets:
                result.extend(self.expression(target, depth + 1, context))
            result.extend(self.row(node, 'statement'))
            return result
        return self.opaque(node, type(node).__name__ + ' statement is not represented.')

    def definition_effects_unknown(self, node):
        """Annotations/decorators/classes need context-specific creation semantics, not a body walk."""
        if isinstance(node, ast.ClassDef) or node.decorator_list or node.returns or getattr(node, 'type_params', []):
            return True
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        arguments.extend(arg for arg in (node.args.vararg, node.args.kwarg) if arg)
        return any(arg.annotation for arg in arguments)

    def assignment(self, node, depth, context):
        """Normal assignment evaluates the RHS first; augmented assignment loads its target first."""
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if isinstance(node, ast.AnnAssign) and node.value is None:
            return self.opaque(node, 'Annotation-only assignment evaluation is not represented.')
        result = []
        if isinstance(node, ast.AugAssign):
            result.extend(self.expression(node.target, depth + 1, context))
        result.extend(self.expression(node.value, depth + 1, context))
        if not isinstance(node, ast.AugAssign):
            for target in targets:
                result.extend(self.expression(target, depth + 1, context))
        result.extend(self.row(node, 'statement', operationKind='assignment'))
        return result

    def loop(self, node, depth, context):
        """Keep iterable setup outside iteration and repeated while tests inside the loop."""
        if isinstance(node, ast.AsyncFor):
            return self.opaque(node, 'Async-for iteration/cleanup is not represented.')
        if node.orelse:
            return self.opaque(node, 'Loop else depends on exhaustion versus break and is not represented.')
        is_for = isinstance(node, ast.For)
        result = self.expression(node.iter, depth + 1, context) if is_for else []
        loop = self.step(node, 'loop', controlKind='foreach' if is_for else 'while',
                         condition=[], body=[], update=[], canExit=True)
        if loop is None:
            return result
        identifier = f'python-loop-{node.lineno}-{node.col_offset}'
        arm_id = identifier + '-a0'
        loop['label'] = ('for ' + self.source.fragment(node.target) + ' in ' + self.source.fragment(node.iter)) if is_for else self.source.fragment(node.test)
        loop['label'] = loop['label'][:400]
        self.branches.append({'id': identifier, 'kind': loop['controlKind'], 'label': loop['label'],
                              **self.source.span(node), 'arms': [{'id': arm_id, 'label': 'iteration'}]})
        if not is_for:
            loop['condition'] = self.expression(node.test, depth + 1, context)
        body_context = (*context, arm_id)
        if is_for:
            loop['body'].extend(self.expression(node.target, depth + 1, body_context))
        loop['body'].extend(self.statements(node.body, depth + 1, body_context))
        result.append(loop)
        return result
