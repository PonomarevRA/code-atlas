"""Общий контракт объявлений членов: видимость и исходные выражения, без вычислений."""
import hashlib
from .interface_source import text

MAX_EXPRESSION = 600
MAX_PAIRS = 60


def member_facts(language, kind, name, owner_id, modifiers=(), *, owner_kind='', static=False):
    """Отделяет языковую видимость от соглашений имён Python и экспортируемости Go."""
    modifiers = list(modifiers)
    visibility = next((m for m in modifiers if m in {'public', 'private', 'protected', 'internal', 'fileprivate'}), None)
    visibility_kind = 'language'
    if language == 'python':
        visibility = 'private' if name.startswith('__') and not name.endswith('__') else 'protected' if name.startswith('_') and not name.endswith('__') else 'public'
        visibility_kind = 'naming-convention'
    elif language == 'go':
        visibility = 'public' if name[:1].isupper() else 'private'
        visibility_kind = 'package-export'
    elif name.startswith('#') and language in {'javascript', 'typescript'}:
        visibility = 'private'
    elif visibility is None:
        defaults = {'csharp': 'public' if owner_kind == 'interface' else 'private', 'java': 'public' if owner_kind == 'interface' else 'package-private', 'cpp': 'private' if owner_kind == 'class' else 'public', 'rust': 'private', 'swift': 'internal'}
        visibility = 'public' if 'pub' in modifiers else defaults.get(language, 'public')
    return {'language': language, 'kind': kind, 'name': name, 'ownerNodeId': owner_id,
            'visibility': visibility, 'visibilityKind': visibility_kind, 'modifiers': modifiers,
            'static': static or 'static' in modifiers, 'readonly': language != 'python' and bool({'readonly', 'final', 'const', 'val', 'let'}.intersection(modifiers)),
            'constantKind': 'naming-convention' if language == 'python' and 'const' in modifiers else 'language',
            'constant': 'const' in modifiers, 'sourceKind': 'snapshot-captured-source'}


def member_id(language, filename, owner, kind, name, signature=''):
    """Синтетические source IDs не меняют исходные ID Graphify; перегрузки имеют разные сигнатуры."""
    identity = f'{filename}:{owner}:{kind}:{name}:{signature}'
    return f'atlas-member:{language}:' + hashlib.sha256(identity.encode()).hexdigest()[:24]


def initializer_data(initializer, source, language, *, declared_type='', constant=False):
    """Сохраняет literal dictionary pairs и исходное выражение; произвольная фабрика остаётся выражением."""
    if initializer is None:
        return None
    expression = text(initializer, source)
    pairs = []
    children = initializer.named_children
    dictionary = False
    if initializer.type in {'dictionary', 'object'}:
        dictionary = True
        for item in children:
            if item.type != 'pair':
                continue
            key, value = item.child_by_field_name('key'), item.child_by_field_name('value')
            if key and value:
                pairs.append((key, value))
    elif language == 'java' and initializer.type == 'method_invocation':
        receiver = text(initializer.child_by_field_name('object'), source)
        name = text(initializer.child_by_field_name('name'), source)
        if receiver in {'Map', 'java.util.Map'} and name == 'of':
            arguments = initializer.child_by_field_name('arguments')
            values = arguments.named_children if arguments else []
            if len(values) % 2 == 0:
                dictionary = True
                pairs = list(zip(values[::2], values[1::2]))
    elif language == 'go' and initializer.type == 'composite_literal' and text(initializer.child_by_field_name('type'), source).startswith('map['):
        dictionary = True
        body = initializer.child_by_field_name('body')
        for item in body.named_children if body else []:
            if item.type == 'keyed_element':
                pairs.append((item.child_by_field_name('key'), item.child_by_field_name('value')))
    entries = [{'key': text(k, source)[:MAX_EXPRESSION], 'value': text(v, source)[:MAX_EXPRESSION], 'line': k.start_point.row + 1} for k, v in pairs[:MAX_PAIRS]]
    truncated = len(expression) > MAX_EXPRESSION or len(pairs) > MAX_PAIRS or any(max(len(text(k, source)), len(text(v, source))) > MAX_EXPRESSION for k, v in pairs[:MAX_PAIRS])
    return {'kind': 'dictionary' if dictionary else 'constant' if constant else 'initializer',
            'initializer': expression[:MAX_EXPRESSION], 'entries': entries, 'line': initializer.start_point.row + 1,
            'sourceKind': 'snapshot-captured-source', 'truncated': truncated,
            'limits': {'maxEntries': MAX_PAIRS, 'maxExpressionChars': MAX_EXPRESSION},
            'warning': 'Lexical expressions only. Values, factories and library bindings are not evaluated; entries preserve source order and duplicate keys.'}
