"""YAML 1.1 events: occurrences и aliases без constructors и alias expansion."""
from .models import DocumentError
from .text_source import BoundedTree, TextSource


class YamlProvider:
    """Сохраняет дубликаты, complex keys, tags и raw lexemes отдельно от значений."""
    id = 'document-yaml'
    version = '1'
    capabilities = {'formats': ['.yaml', '.yml'], 'tree': True, 'references': True,
                    'dialect': 'YAML-1.1', 'constructors': False, 'offlineCertified': False}

    @property
    def runtime_fingerprint(self):
        """Фиксирует библиотеку, adapter и общую реализацию source spans."""
        from .providers import runtime_fingerprint
        return runtime_fingerprint('PyYAML', __file__)

    def parse(self, path, options, context):
        """Читает только события captured UTF-8 YAML; не конструирует Python objects."""
        try:
            import yaml
        except ImportError as error:
            raise DocumentError('CAPABILITY_UNAVAILABLE', 'PyYAML не установлен.') from error
        if options.get('dialect', 'YAML-1.1') != 'YAML-1.1':
            raise DocumentError('PROFILE_UNSUPPORTED', 'Поддержан только явный профиль YAML-1.1.')
        source = TextSource(path, options)
        tree = BoundedTree(context.get('logicalPath', path.name), options)
        stack, anchors = [], {}
        document_index = -1
        token_count, alias_count = 0, 0
        try:
            for event in yaml.parse(source.text, Loader=yaml.SafeLoader):
                token_count += 1
                if token_count > min(int(options.get('maxTokens', 300000)), 300000):
                    raise DocumentError('TOKEN_LIMIT', 'Превышен предел YAML events.')
                event_type = type(event).__name__
                if event_type in {'StreamStartEvent', 'StreamEndEvent'}:
                    continue
                if event_type == 'DocumentStartEvent':
                    document_index += 1
                    if event.version and tuple(event.version) != (1, 1):
                        raise DocumentError('PROFILE_UNSUPPORTED', 'YAML directive не соответствует профилю 1.1.')
                    anchors = {}
                    node = tree.add('section', parent_id=tree.root_id, label=f'YAML document {document_index + 1}',
                                    attributes={'streamIndex': document_index, 'dialect': 'YAML-1.1'})
                    stack = [{'id': node, 'kind': 'document', 'count': 0, 'path': []}]
                    continue
                if event_type == 'DocumentEndEvent':
                    stack = []
                    continue
                if event_type in {'MappingEndEvent', 'SequenceEndEvent'}:
                    frame = stack.pop()
                    node = tree.nodes[int(frame['id'])]
                    node['sourceLocator']['characterEnd'] = event.end_mark.index
                    node['sourceLocator'].update(source.span(node['sourceLocator']['characterStart'], event.end_mark.index,
                        kind='yaml', streamIndex=document_index, occurrencePath=frame['path']))
                    continue
                frame = stack[-1]
                occurrence = frame['count']
                frame['count'] += 1
                path_parts = [*frame['path'], occurrence]
                role = 'key' if frame['kind'] == 'mapping' and occurrence % 2 == 0 else 'value'
                attributes = {'occurrence': occurrence, 'role': role, 'tag': getattr(event, 'tag', None),
                              'anchor': getattr(event, 'anchor', None)}
                locator = source.span(event.start_mark.index, event.end_mark.index, kind='yaml',
                                      streamIndex=document_index, occurrencePath=path_parts)
                if event_type == 'ScalarEvent':
                    attributes.update(rawLexeme=source.text[event.start_mark.index:event.end_mark.index],
                                      style=event.style, implicit=list(event.implicit))
                    # Resolver applies dialect typing only; no constructors or value conversion execute.
                    attributes['resolvedTag'] = event.tag or yaml.resolver.Resolver().resolve(
                        yaml.nodes.ScalarNode, event.value, event.implicit)
                    if role == 'key':
                        keys = frame.setdefault('scalarKeys', set())
                        identity = attributes['resolvedTag'], event.value
                        attributes['duplicateKey'] = identity in keys
                        keys.add(identity)
                    node = tree.add(role, parent_id=frame['id'], label=event.value[:200], text=event.value,
                                    source_locator=locator, attributes=attributes)
                elif event_type == 'AliasEvent':
                    alias_count += 1
                    if alias_count > min(int(options.get('maxAliases', 10000)), 10000):
                        raise DocumentError('ALIAS_LIMIT', 'Превышен предел YAML aliases.')
                    node = tree.add('reference', parent_id=frame['id'], label=event.anchor, source_locator=locator,
                                    attributes={**attributes, 'referenceKind': 'yaml-alias'})
                    target = anchors.get(event.anchor)
                    if target is None:
                        tree.warnings.append({'code': 'UNRESOLVED_ALIAS', 'anchor': event.anchor})
                    else:
                        tree.edges.append({'source': node, 'target': target, 'relation': 'alias_of', 'certainty': 'literal'})
                else:
                    collection = 'mapping' if event_type == 'MappingStartEvent' else 'sequence'
                    node = tree.add('section' if collection == 'mapping' else 'list', parent_id=frame['id'],
                                    source_locator=locator, attributes={**attributes, 'collection': collection,
                                    'flowStyle': event.flow_style})
                    stack.append({'id': node, 'kind': collection, 'count': 0, 'path': path_parts})
                if event_type != 'AliasEvent' and getattr(event, 'anchor', None):
                    anchors[event.anchor] = node
        except yaml.YAMLError as error:
            raise DocumentError('INVALID_YAML', str(error)[:500]) from error
        coverage = {'status': 'partial' if tree.warnings else 'complete',
                    'reasonCodes': ['UNRESOLVED_ALIAS'] if tree.warnings else [], 'dialect': 'YAML-1.1'}
        return tree.build(coverage)
