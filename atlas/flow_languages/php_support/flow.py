"""Bounded PHP-дерево лексических альтернатив без исполнения и runtime binding."""
from .source import DEFERRED, contains_owned, site, text

MAX_STEPS = 180
MAX_DEPTH = 24
CALLS = {'function_call_expression', 'member_call_expression', 'scoped_call_expression',
         'nullsafe_member_call_expression', 'object_creation_expression'}
NULLSAFE = {'nullsafe_member_call_expression', 'nullsafe_member_access_expression'}
INCLUDES = {'include_expression', 'include_once_expression', 'require_expression', 'require_once_expression'}
TERMINALS = {'return_statement': 'return', 'throw_expression': 'throw',
             'break_statement': 'break', 'continue_statement': 'continue'}
LITERALS = {'integer', 'float', 'string', 'boolean', 'null', 'name', 'qualified_name'}
TRANSPARENT = {'parenthesized_expression', 'argument', 'arguments', 'sequence_expression',
               'array_creation_expression', 'array_element_initializer', 'unary_op_expression',
               'cast_expression', 'reference_assignment_expression', 'by_ref',
               'error_suppression_expression', 'update_expression',
               'encapsed_string', 'subscript_expression', 'member_access_expression',
               'scoped_property_access_expression', 'class_constant_access_expression'}


def simple_foreach_target(node):
    """Допускает только переменные, key/value pair и ссылки; вычисляемые lvalues не пропускает."""
    pending = [node]
    while pending:
        item = pending.pop()
        if item.type == 'variable_name':
            continue
        if item.type not in {'pair', 'by_ref'} or not item.named_children:
            return False
        pending.extend(item.named_children)
    return True


def static_case_label(node):
    """Допускает исходные константные метки без вычисления значения или вызова guard."""
    pending = [node]
    while pending:
        item = pending.pop()
        if item.type in LITERALS:
            continue
        if item.type not in {'unary_op_expression', 'class_constant_access_expression'} or not item.named_children:
            return False
        pending.extend(item.named_children)
    return True


class Flow:
    """Один счётчик шагов/глубины и согласованные callsite факты для всего объявления."""

    def __init__(self, content, max_events):
        """Фиксирует байты и бюджеты; никакого доступа к файловой системе."""
        self.content = content
        self.max_events = max(0, max_events)
        self.events, self.unsupported, self.branches, self.terminals = [], [], [], []
        self.steps = 0
        self.truncated = False
        self.events_truncated = False
        self.branch_count = 0

    def label(self, node):
        """Обрезает публикуемое выражение, сохраняя полный AST внутри анализатора."""
        return text(node, self.content)[:400]

    def issue(self, reason):
        """Публикует каждую причину ровно один раз в порядке обнаружения."""
        if reason not in self.unsupported:
            self.unsupported.append(reason)

    def step(self, kind, node, label=None, **extra):
        """Резервирует шаг до вложенного обхода, чтобы весь результат соблюдал общий лимит."""
        if self.steps >= MAX_STEPS:
            self.truncated = True
            self.issue('PHP: исчерпан лимит шагов methodFlow.')
            return None
        self.steps += 1
        return {'kind': kind, 'label': (self.label(node) if label is None else label)[:400],
                **site(node), **extra}

    def unknown(self, node, reason):
        """Сохраняет видимый исходник, не раскрывая неизвестное тело линейной цепочкой."""
        self.issue(reason)
        item = self.step('unknown', node, incomplete=True, reason=reason)
        return [item] if item else []

    def ends_execution(self, steps):
        """Прямой terminal операнда запрещает создавать шаги использующего его выражения."""
        return bool(steps and steps[-1]['kind'] in {'return', 'throw', 'break', 'continue'})

    def branch(self, node, kind, label=None):
        """Создаёт локальный branch ID; raw ID целей никогда не синтезируются."""
        self.branch_count += 1
        identifier = f'php-branch-{node.start_byte}-{self.branch_count}'
        step = self.step('branch', node, label, controlKind=kind, arms=[], id=identifier)
        if step is not None:
            self.branches.append({'id': identifier, 'kind': kind, **site(node),
                                  'condition': step['label'], 'arms': step['arms']})
        return step

    def arm(self, branch, label, nodes, context, depth, *, value=False, reuse=False):
        """Отдельная альтернатива владеет контекстом своих событий, включая пустое skip."""
        identifier = f"{branch['id']}-a{len(branch['arms'])}"
        arm = {'id': identifier, 'label': label[:400], 'steps': []}
        branch['arms'].append(arm)
        arm_context = (*context, identifier)
        if value:
            for node in nodes:
                if node is not None:
                    arm['steps'].extend(self.value(node, arm_context, depth, reuse=reuse))
        else:
            arm['steps'] = self.block([node for node in nodes if node is not None], arm_context, depth)
        if not arm['steps']:
            arm['emptyReason'] = 'not-extracted' if self.truncated else 'skip'
        return arm

    def value(self, node, context, depth, *, reuse=False):
        """Показывает литеральный результат выбора без повторного вычисления вызова."""
        steps = [] if reuse else self.visit(node, context, depth)
        if not steps:
            item = self.step('value', node, expression=self.label(node), reuseResult=reuse)
            if item:
                steps.append(item)
        return steps

    def visit(self, node, context=(), depth=0):
        """Маршрутизирует только известные формы; незнакомая форма остаётся unknown."""
        if node is None or node.type in {'comment', 'empty_statement'}:
            return []
        if depth >= MAX_DEPTH:
            self.truncated = True
            return self.unknown(node, 'PHP: исчерпан лимит глубины methodFlow.')
        if self.steps >= MAX_STEPS:
            self.truncated = True
            self.issue('PHP: исчерпан лимит шагов methodFlow.')
            return []
        depth += 1
        kind = node.type
        if kind in {'compound_statement', 'colon_block'}:
            return self.block(node.named_children, context, depth)
        if kind in DEFERRED:
            self.issue('PHP: тела closures, локальных функций и классов отложены; их вызов неизвестен.')
            item = self.step('statement', node, deferred=True)
            return [item] if item else []
        if kind == 'expression_statement':
            return self.block(node.named_children, context, depth)
        if kind in TERMINALS:
            return self.terminal(node, context, depth)
        if kind == 'if_statement':
            alternatives = [child for child in node.named_children if child.type in {'else_if_clause', 'else_clause'}]
            return self.if_chain(node, alternatives, context, depth)
        if kind in {'while_statement', 'do_statement', 'for_statement', 'foreach_statement'}:
            return self.loop(node, context, depth)
        if kind in {'switch_statement', 'match_expression'}:
            return self.selection(node, context, depth)
        if kind in NULLSAFE:
            return self.nullsafe(node, context, depth)
        if kind in CALLS:
            return self.call(node, context, depth)
        if kind in {'binary_expression', 'augmented_assignment_expression'}:
            operator = node.child_by_field_name('operator')
            if operator is None:
                operator = next((child for child in node.children if not child.is_named), None)
            if text(operator, self.content).lower() in {'&&', '||', 'and', 'or', '??', '??='}:
                return self.short_circuit(node, context, depth, text(operator, self.content).lower())
        if kind == 'conditional_expression':
            return self.conditional(node, context, depth)
        if kind in INCLUDES:
            steps = self.block(node.named_children, context, depth)
            if self.ends_execution(steps):
                return steps
            steps.extend(self.unknown(node, 'PHP: include/require не читаются и не исполняются; подключаемое тело и завершение неизвестны.'))
            return steps
        if kind in {'assignment_expression', 'augmented_assignment_expression', 'reference_assignment_expression'}:
            steps = self.block(node.named_children, context, depth)
            if steps and steps[-1]['kind'] == 'throw':
                return steps
            item = self.step('statement', node, operationKind='assignment')
            if item:
                steps.append(item)
            return steps
        if kind == 'binary_expression' or kind in TRANSPARENT:
            if kind in {'subscript_expression', 'member_access_expression'} and self.contains_nullsafe(node):
                return self.unknown(node, 'PHP: продолжение nullsafe-цепочки требует отдельного контекста; цепочка не развёрнута.')
            steps = self.block(node.named_children, context, depth)
            if kind == 'update_expression':
                item = self.step('statement', node)
                if item:
                    steps.append(item)
            return steps
        if kind in LITERALS or kind in {'variable_name', 'string_content', 'escape_sequence'}:
            return []
        if kind == 'echo_statement':
            steps = self.block(node.named_children, context, depth)
            if steps and steps[-1]['kind'] == 'throw':
                return steps
            item = self.step('statement', node)
            return steps + ([item] if item else [])
        return self.unknown(node, f'PHP: {kind} не развёрнут; exception/cleanup, suspension и динамическое управление неизвестны.')

    def block(self, nodes, context, depth):
        """Читает sibling-узлы слева направо; прямой terminal не превращается в fallthrough."""
        result = []
        for node in nodes:
            result.extend(self.visit(node, context, depth))
            if result and result[-1]['kind'] in {'return', 'throw', 'break', 'continue'}:
                break
            if self.steps >= MAX_STEPS:
                if node != nodes[-1]:
                    self.truncated = True
                    self.issue('PHP: исчерпан лимит шагов methodFlow.')
                break
        return result

    def terminal(self, node, context, depth):
        """Операнд вычисляется до возврата/throw; многоуровневый break/continue неизвестен."""
        kind = TERMINALS[node.type]
        operands = [child for child in node.named_children if child.type != 'comment']
        if kind in {'break', 'continue'} and operands and self.label(operands[0]) != '1':
            return self.unknown(node, 'PHP: многоуровневый break/continue не представлен общими jump-портами.')
        steps = self.block(operands, context, depth)
        if steps and steps[-1]['kind'] == 'throw':
            return steps
        item = self.step(kind, node)
        if item:
            self.terminals.append({**item, 'branchIds': list(context)})
            steps.append(item)
        return steps

    def if_chain(self, node, alternatives, context, depth):
        """elseif проверяется только в предыдущей ложной ветви, с собственными вызовами."""
        if depth >= MAX_DEPTH:
            self.truncated = True
            return self.unknown(node, 'PHP: исчерпан лимит глубины methodFlow.')
        condition = node.child_by_field_name('condition')
        steps = self.visit(condition, context, depth)
        if self.ends_execution(steps):
            return steps
        branch = self.branch(node, 'if', self.label(condition))
        if branch is None:
            return steps
        branch.update(conditionExpression=self.label(condition), conditionResult='reuse')
        self.arm(branch, 'истина', [node.child_by_field_name('body')], context, depth)
        false_arm = self.arm(branch, 'ложь', [], context, depth)
        if alternatives:
            first, *remaining = alternatives
            false_context = (*context, false_arm['id'])
            if first.type == 'else_if_clause':
                false_arm['steps'] = self.if_chain(first, remaining, false_context, depth + 1)
            else:
                false_arm['steps'] = self.visit(first.child_by_field_name('body'), false_context, depth)
            if false_arm['steps']:
                false_arm.pop('emptyReason', None)
        steps.append(branch)
        return steps

    def short_circuit(self, node, context, depth, operator):
        """Правый операнд принадлежит только вычисляемой альтернативе &&/||/??/??=."""
        left, right = node.child_by_field_name('left'), node.child_by_field_name('right')
        steps = self.visit(left, context, depth)
        if self.ends_execution(steps):
            return steps
        branch = self.branch(node, operator)
        if branch is None:
            return steps
        branch.update(conditionExpression=self.label(left), conditionResult='reuse')
        evaluate = 'левое ложно'
        if operator.startswith('??'):
            evaluate = 'левое null'
        elif operator in {'&&', 'and'}:
            evaluate = 'левое истинно'
        arm = self.arm(branch, evaluate, [right], context, depth, value=True)
        if operator == '??=' and not self.ends_execution(arm['steps']):
            item = self.step('statement', node, operationKind='assignment')
            if item:
                arm['steps'].append(item)
        if operator in {'??', '??='}:
            self.arm(branch, 'левое не null', [left], context, depth, value=True, reuse=True)
        else:
            self.arm(branch, 'пропустить правый операнд', [], context, depth)
        return steps + [branch]

    def conditional(self, node, context, depth):
        """Полная и сокращённая ternary-формы сохраняют альтернативы и однократность условия."""
        condition = node.child_by_field_name('condition')
        steps = self.visit(condition, context, depth)
        if self.ends_execution(steps):
            return steps
        branch = self.branch(node, 'conditional')
        if branch is None:
            return steps
        branch.update(conditionExpression=self.label(condition), conditionResult='reuse')
        body = node.child_by_field_name('body')
        self.arm(branch, 'истина', [body if body is not None else condition], context, depth, value=True, reuse=body is None)
        self.arm(branch, 'ложь', [node.child_by_field_name('alternative')], context, depth, value=True)
        return steps + [branch]

    def contains_nullsafe(self, node):
        """Проверяет только явно переданный receiver, не исполняя его выражение."""
        pending = [node] if node is not None else []
        while pending:
            current = pending.pop()
            if current.type in NULLSAFE:
                return True
            if current.type not in DEFERRED:
                pending.extend(current.named_children)
        return False

    def nullsafe(self, node, context, depth):
        """Аргументы и обращение ?-> доступны только при ненулевом receiver."""
        receiver = node.child_by_field_name('object')
        if self.contains_nullsafe(receiver):
            return self.unknown(node, 'PHP: продолжение nullsafe-цепочки требует отдельного контекста; цепочка не развёрнута.')
        steps = self.visit(receiver, context, depth)
        if self.ends_execution(steps):
            return steps
        branch = self.branch(node, '?->')
        if branch is None:
            return steps
        nonnull = self.arm(branch, 'receiver не null', [], context, depth)
        arm_context = (*context, nonnull['id'])
        if node.type in CALLS:
            nonnull['steps'] = self.call(node, arm_context, depth, receiver_evaluated=True)
        else:
            nonnull['steps'] = self.visit(node.child_by_field_name('name'), arm_context, depth)
            item = self.step('value', node, expression=self.label(node), reuseResult=True)
            if item:
                nonnull['steps'].append(item)
        if nonnull['steps']:
            nonnull.pop('emptyReason', None)
        null = self.arm(branch, 'receiver null', [], context, depth)
        item = self.step('value', node, 'null', expression='null')
        if item:
            null['steps'] = [item]
            null.pop('emptyReason', None)
        return steps + [branch]

    def call(self, node, context, depth, *, receiver_evaluated=False):
        """Вложенные вызовы предшествуют внешнему; динамическая цель не подменяется именем."""
        receiver = node.child_by_field_name('object') or node.child_by_field_name('scope')
        if not receiver_evaluated and self.contains_nullsafe(receiver):
            return self.unknown(node, 'PHP: продолжение nullsafe-цепочки требует отдельного контекста; цепочка не развёрнута.')
        args = node.child_by_field_name('arguments')
        if args is None:
            args = next((child for child in node.named_children if child.type == 'arguments'), None)
        if args is not None and any(child.type == 'variadic_placeholder' for child in args.named_children):
            return self.unknown(node, 'PHP: first-class callable создаёт отложенный callable, а не вызов.')
        function = node.child_by_field_name('function') or node.child_by_field_name('name')
        if node.type == 'object_creation_expression':
            function = next((child for child in node.named_children if child.type in {'name', 'qualified_name', 'variable_name', 'anonymous_class'}), None)
            if function is None or function.type == 'anonymous_class':
                return self.unknown(node, 'PHP: создание anonymous class и его initializer не развёрнуты.')
        steps = []
        if receiver is not None and not receiver_evaluated:
            steps.extend(self.visit(receiver, context, depth))
        dynamic = function is None or function.type not in {'name', 'qualified_name', 'relative_name'}
        if dynamic:
            steps.extend(self.visit(function, context, depth))
            self.issue('PHP: динамические вызовы, callable invocation, magic dispatch и cleanup статически неизвестны.')
        if steps and steps[-1]['kind'] == 'throw':
            return steps
        steps.extend(self.visit(args, context, depth))
        if steps and steps[-1]['kind'] in {'return', 'throw'}:
            return steps
        if len(self.events) >= self.max_events:
            self.events_truncated = True
            return steps + self.unknown(node, 'PHP: исчерпан лимит call events; вызов не связан с деревом.')
        callee = self.label(function)
        end = args.start_byte if args is not None else node.end_byte
        label = self.content[node.start_byte:end].decode('utf-8').strip() + '()'
        call = self.step('call', node, label, site=site(node), sourceExpression=self.label(node))
        if call is None:
            return steps
        event = {'kind': 'call', 'text': label[:400], 'callee': callee, **site(node),
                 'sourceExpression': self.label(node), 'targetNodeIds': [],
                 'confidence': 'unresolved' if dynamic else 'source', 'conditional': bool(context),
                 'awaited': False, 'parallel': False, 'branchIds': list(context),
                 'evaluationOrder': 'lexical-source', 'arguments': [], 'argumentsTruncated': False}
        if args is not None:
            arguments = [child for child in args.named_children if child.type != 'comment']
            for argument in arguments[:40]:
                event['arguments'].append({'expression': self.label(argument), **site(argument), 'sourceKind': 'lexical-source'})
            event['argumentsTruncated'] = len(arguments) > 40
        self.events.append(event)
        return steps + [call]

    def loop(self, node, context, depth):
        """Отделяет одноразовый initializer/iterable от повторяемых condition/body/update."""
        kind = node.type.removesuffix('_statement')
        prefix = []
        if kind == 'for':
            prefix = self.visit(node.child_by_field_name('initialize'), context, depth)
        elif kind == 'foreach':
            iterable = next((child for child in node.named_children if child.type != 'comment'), None)
            prefix = self.visit(iterable, context, depth)
            targets = [child for child in node.named_children if child != iterable
                       and child != node.child_by_field_name('body') and child.type != 'comment']
            for target in targets:
                if not simple_foreach_target(target):
                    return prefix + self.unknown(node, 'PHP: сложное foreach-присваивание/destructuring не развёрнуто; его вызовы и тело не линеаризуются.')
        if self.ends_execution(prefix):
            return prefix
        body_node = node.child_by_field_name('body')
        header = self.label(node)
        if body_node is not None:
            header = self.content[node.start_byte:body_node.start_byte].decode('utf-8').strip()
        if kind == 'do':
            header = 'do … while ' + self.label(node.child_by_field_name('condition'))
        loop = self.step('loop', node, header, controlKind=kind, condition=[], body=[], update=[], canExit=True)
        if loop is None:
            return prefix
        identifier = f'php-loop-{node.start_byte}'
        loop_context = (*context, identifier + '-a0')
        if kind == 'do':
            loop['body'] = self.visit(node.child_by_field_name('body'), loop_context, depth)
            loop['condition'] = self.visit(node.child_by_field_name('condition'), loop_context, depth)
        else:
            loop['condition'] = self.visit(node.child_by_field_name('condition'), loop_context, depth)
            loop['body'] = self.visit(node.child_by_field_name('body'), loop_context, depth)
        loop['update'] = self.visit(node.child_by_field_name('update'), loop_context, depth)
        condition = node.child_by_field_name('condition')
        loop['conditionExpression'] = self.label(condition)
        loop['conditionResult'] = 'reuse'
        if kind == 'for' and condition is None:
            loop['canExit'] = False
        if kind == 'do':
            loop['conditionPosition'] = 'after-body'
        self.branches.append({'id': identifier, 'kind': kind, **site(node),
                              'condition': loop['conditionExpression'],
                              'arms': [{'id': identifier + '-a0', 'label': 'итерация', 'steps': loop['body']}]})
        return prefix + [loop]

    def selection(self, node, context, depth):
        """Простые case/match альтернативы; fallthrough и вычисляемые метки остаются unknown."""
        kind = 'match' if node.type == 'match_expression' else 'switch'
        steps = self.visit(node.child_by_field_name('condition'), context, depth)
        if self.ends_execution(steps):
            return steps
        body = node.child_by_field_name('body')
        sections = [child for child in body.named_children if child.type != 'comment'] if body is not None else []
        groups, pending = [], []
        for index, section in enumerate(sections):
            if kind == 'switch' and contains_owned(section, {'continue_statement'}):
                return steps + self.unknown(node, 'PHP: continue внутри switch имеет особую PHP-семантику; общий continue-порт не используется.')
            value = section.child_by_field_name('value') if kind == 'switch' else section.child_by_field_name('conditional_expressions')
            values = value.named_children if value is not None and kind == 'match' else ([value] if value is not None else [])
            if any(not static_case_label(item) for item in values):
                return steps + self.unknown(node, 'PHP: вычисляемые switch/match метки и guards не развёрнуты.')
            default = section.type in {'default_statement', 'match_default_expression'}
            pending.append('default' if default else self.label(value))
            if kind == 'match':
                statements = [section.child_by_field_name('return_expression')]
            else:
                statements = [child for child in section.named_children if child != value and child.type != 'comment']
                if not statements and index < len(sections) - 1:
                    continue
                if statements and statements[-1].type not in {'break_statement', 'return_statement', 'continue_statement'}:
                    throwing = statements[-1].type == 'expression_statement' and statements[-1].named_children[0].type == 'throw_expression'
                    if index < len(sections) - 1 and not throwing:
                        return steps + self.unknown(node, 'PHP: switch fallthrough не представлен общими независимыми arms.')
            groups.append((' / '.join(pending), statements))
            pending = []
        branch = self.branch(node, kind)
        if branch is None:
            return steps
        for label, statements in groups:
            self.arm(branch, label, statements, context, depth, value=kind == 'match')
        if not any('default' in label.split(' / ') for label, _ in groups):
            arm = self.arm(branch, 'нет совпадения', [], context, depth)
            if kind == 'match':
                item = self.step('throw', node, 'UnhandledMatchError', implicitThrow=True)
                if item:
                    arm['steps'] = [item]
                    arm.pop('emptyReason', None)
                    self.terminals.append({**item, 'branchIds': [*context, arm['id']]})
        return steps + [branch]
