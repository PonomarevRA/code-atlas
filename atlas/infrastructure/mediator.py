"""Explicit MediatR request candidates bridge entry methods to handlers without claiming runtime DI."""
from collections import defaultdict
import re
from ..interface_source import SourceModel, text
from ..interface_dispatch import _ReceiverResolver


def _owned_calls(method):
    """Exclude nested functions/closures: their Send calls do not execute as ordinary owner steps."""
    pending = [method['ast']]
    while pending:
        node = pending.pop()
        if node != method['ast'] and node.type in {'local_function_statement', 'lambda_expression', 'anonymous_method_expression'}:
            continue
        if node.type == 'invocation_expression':
            yield node
        pending.extend(reversed(node.named_children))


def _request_type(expression, resolver, at):
    """Resolve ordinary variables, typed request properties and unambiguous extension factory returns."""
    known = resolver.infer(expression, at)
    if known or expression is None:
        return known
    model, owner = resolver.model, resolver.owner
    if expression.type == 'member_access_expression':
        receiver = resolver.infer(expression.child_by_field_name('expression'), at)
        definition = model.definition(receiver) if receiver else None
        name = text(expression.child_by_field_name('name'), resolver.source)
        if definition:
            declared = definition['fields'].get(name, '')
            if not declared:
                content = model.files[definition['file']]
                for member in definition['ast'].named_children:
                    if member.type != 'declaration_list':
                        continue
                    for prop in member.named_children:
                        if prop.type == 'property_declaration' and text(prop.child_by_field_name('name'), content) == name:
                            declared = text(prop.child_by_field_name('type'), content)
            return model.resolve(declared, definition)
        # The library's ConsumerMessage<T>.Model contract carries the message type;
        # neither the runtime payload nor arbitrary properties are inferred.
        receiver_name = text(expression.child_by_field_name('expression'), resolver.source)
        if name == 'Model' and 'using BCS.Fintech.RabbitMQ.Consumer.Contexts;' in resolver.source.decode('utf-8'):
            for parameter in resolver.method['parameters']:
                wrapper = re.fullmatch(r'[\w.]*ConsumerMessage\s*<\s*([\w.]+)\s*>', parameter['type'])
                if parameter['name'] == receiver_name and wrapper:
                    return model.resolve(wrapper[1], owner)
    if expression.type != 'invocation_expression':
        return None
    function = expression.child_by_field_name('function')
    if function is None or function.type != 'member_access_expression':
        return None
    name = text(function.child_by_field_name('name'), resolver.source)
    receiver_type = _request_type(function.child_by_field_name('expression'), resolver, at)
    args = expression.child_by_field_name('arguments')
    count = len(args.named_children) if args else 0
    returns = set()
    for method in model.methods:
        if method['name'] != name or len(method['parameters']) != count + 1:
            continue
        parameter = model.resolve(method['parameters'][0]['type'], method['owner'])
        if receiver_type and parameter == receiver_type:
            returns.add(model.resolve(method['returns'], method['owner']))
    return returns.pop() if len(returns) == 1 else None


def mediator_edges(root, graph):
    """Match typed Send requests to IRequestHandler.Handle declarations, with call-site evidence and budgets."""
    model = SourceModel(root, graph)
    handlers = defaultdict(list)
    for method in model.methods:
        owner = method['owner']
        if method['name'] != 'Handle' or not method['id'] or not method['parameters']:
            continue
        if not any('IRequestHandler<' in base.replace(' ', '') for base in owner.get('baseTypes', [])):
            continue
        if 'using MediatR;' not in model.files[owner['file']].decode('utf-8') and not any('MediatR.IRequestHandler<' in base for base in owner.get('baseTypes', [])):
            continue
        request = model.resolve(method['parameters'][0]['type'], owner)
        if request:
            handlers[request].append(method)
    result = []
    for method in model.methods:
        if not method['id'] or method['constructor']:
            continue
        resolver = _ReceiverResolver(model, method)
        for call in _owned_calls(method):
            if call.type != 'invocation_expression':
                continue
            function = call.child_by_field_name('function')
            if function is None or function.type != 'member_access_expression':
                continue
            if text(function.child_by_field_name('name'), resolver.source) != 'Send':
                continue
            receiver_node = function.child_by_field_name('expression')
            receiver_name = text(receiver_node, resolver.source).removeprefix('this.')
            declared = method['owner']['fields'].get(receiver_name, '')
            for parameter in method['parameters']:
                if parameter['name'] == receiver_name:
                    declared = parameter['type']
            receiver = resolver.infer(receiver_node, call)
            if receiver and model.definition(receiver) and not receiver.startswith('MediatR.'):
                continue
            if declared.rsplit('.', 1)[-1] not in {'IMediator', 'ISender'} or 'using MediatR;' not in resolver.source.decode('utf-8'):
                continue
            args = call.child_by_field_name('arguments')
            if args is None or not args.named_children:
                continue
            argument = args.named_children[0]
            expression = argument.named_children[-1] if argument.named_children else argument
            request = _request_type(expression, resolver, call)
            for handler in handlers.get(request, [])[:12]:
                result.append({'source': method['id'], 'target': handler['id'], 'relation': 'calls',
                    'confidence': 'INFERRED', '_origin': 'mediator-dispatch', 'source_file': method['owner']['file'],
                    'source_location': f'L{call.start_point.row + 1}', 'lineKind': 'working-tree-ast',
                    'evidence': {'kind': 'mediator-dispatch', 'callee': 'Send', 'requestType': request,
                                 'runtimeSelectionKnown': False, 'file': method['owner']['file'], 'line': call.start_point.row + 1}})
                if len(result) >= 4000:
                    return result, {'limited': True}
    return result, {'limited': model.truncated, 'candidates': len(result), 'files': len(model.files)}
