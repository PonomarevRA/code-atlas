"""Статический Helm source profile без helm/render/network выполнения."""
import re
from pathlib import PurePosixPath

from .models import DocumentError
from .text_source import BoundedTree, TextSource

VALUE_REFERENCE = re.compile(r'(?<![\w$.])(?P<root>\$?)\.Values(?P<path>(?:\.[A-Za-z_][\w-]*)+)')


class HelmProvider:
    """Показывает lexical template references; rendered resources не утверждает."""
    id = 'document-helm'
    version = '1'
    capabilities = {'formats': ['.yaml', '.yml', '.tpl'], 'tree': True, 'references': True,
                    'rendering': False, 'crossFileResolution': False, 'offlineCertified': False}

    @property
    def runtime_fingerprint(self):
        """YAML parser и lexical adapter входят в один runtime fingerprint."""
        from .providers import runtime_fingerprint
        return runtime_fingerprint('PyYAML', __file__, 'provider_yaml.py')

    def parse(self, path, options, context):
        """Разбирает Chart/values YAML либо ограниченные Go-template actions как исходник."""
        source = TextSource(path, options)
        if '{{' not in source.text and path.suffix.lower() in {'.yaml', '.yml'}:
            from .provider_yaml import YamlProvider
            result = YamlProvider().parse(path, options, context)
            role = self.cache_context(path, options, context)['sourceRole']
            result['nodes'][0]['attributes'].update(profile='helm-source', rendered=False, sourceRole=role)
            self._yaml_annotations(result, role)
            return result
        tree = BoundedTree(context.get('logicalPath', path.name), options)
        tree.nodes[0]['attributes'] = {'profile': 'helm-source', 'rendered': False}
        tree.add('code', parent_id=tree.root_id, text=source.text,
                 source_locator=source.span(0, len(source.text)), attributes={'syntax': 'helm-template'})
        self._actions(source, tree)
        return tree.build({'status': 'partial', 'reasonCodes': ['STATIC_TEMPLATE_ONLY', 'CROSS_FILE_UNRESOLVED']})

    def cache_context(self, path, options, context):
        """Кэш учитывает семантическую роль source, но не произвольное имя binding."""
        logical = PurePosixPath(context.get('sourcePath', context.get('logicalPath', str(path))))
        role = 'source'
        if logical.name == 'Chart.yaml':
            role = 'chart'
        elif logical.name in {'values.yaml', 'values.yml'}:
            role = 'values'
        elif 'templates' in logical.parts or logical.suffix == '.tpl':
            role = 'template'
        return {'sourceRole': role, 'chartContext': bool(context.get('chartContext'))}

    def _yaml_annotations(self, result, role):
        """Добавляет literal dependency edges и value paths без rendered resource inference."""
        children = {}
        for node in result['nodes']:
            children.setdefault(node.get('parentId'), []).append(node)
        pending = []
        for node in result['nodes']:
            if node['attributes'].get('collection') != 'mapping':
                continue
            parent = result['nodes'][int(node['parentId'])]
            if parent['attributes'].get('streamIndex') is not None:
                pending.append((node, []))
        while pending:
            mapping, prefix = pending.pop()
            entries = children.get(mapping['id'], [])
            for offset in range(0, len(entries) - 1, 2):
                key, value = entries[offset:offset + 2]
                if key['kind'] != 'key':
                    continue
                value_path = [*prefix, key['text']]
                if role == 'values':
                    key['attributes']['valuePath'] = value_path
                    value['attributes']['valuePath'] = value_path
                if value['attributes'].get('collection') == 'mapping':
                    pending.append((value, value_path))
                if role == 'chart' and not prefix and key['text'] == 'dependencies':
                    for dependency in children.get(value['id'], []):
                        if dependency['attributes'].get('collection') != 'mapping':
                            continue
                        self._dependency_fields(dependency, children.get(dependency['id'], []))
                        dependency['attributes']['availability'] = 'unresolved'
                        result['edges'].append({'source': mapping['id'], 'target': dependency['id'],
                                                'relation': 'declares_dependency', 'certainty': 'literal'})

    @staticmethod
    def _dependency_fields(dependency, fields):
        """Не сводит повторные declaration fields к одному якобы однозначному значению."""
        declared, duplicates = {}, set()
        for index in range(0, len(fields) - 1, 2):
            field, scalar = fields[index:index + 2]
            if field['kind'] != 'key' or scalar['kind'] != 'value':
                continue
            name = field['text']
            if name in declared or name in duplicates:
                duplicates.add(name)
                declared.pop(name, None)
            else:
                declared[name] = scalar['text']
        dependency['attributes']['declaredDependency'] = declared
        dependency['attributes']['ambiguousFields'] = sorted(duplicates)

    def _actions(self, source, tree):
        """Следит за lexical dot scope, не пытаясь интерпретировать Go expressions."""
        scopes = []
        root_rebound = False
        offset, actions = 0, 0
        while True:
            start = source.text.find('{{', offset)
            if start < 0:
                break
            end = self._action_end(source.text, start + 2)
            if end < 0:
                tree.warnings.append({'code': 'UNCLOSED_TEMPLATE', 'characterStart': start})
                break
            end += 2
            expression = source.text[start + 2:end - 2].strip()
            if expression.startswith('-') and len(expression) > 1 and expression[1].isspace():
                expression = expression[1:].lstrip()
            if expression.endswith('-') and len(expression) > 1 and expression[-2].isspace():
                expression = expression[:-1].rstrip()
            offset = end
            actions += 1
            if actions > 100000:
                raise DocumentError('TOKEN_LIMIT', 'Превышен предел Helm actions.')
            if expression.startswith('/*'):
                continue
            command = expression.split(None, 1)[0] if expression else ''
            # Arguments of with/range evaluate in the enclosing scope; the body uses a new dot.
            if command == 'end':
                if scopes:
                    scopes.pop()
                else:
                    tree.warnings.append({'code': 'UNMATCHED_TEMPLATE_END'})
            lexical_expression = self._mask_strings(expression)
            matches = list(VALUE_REFERENCE.finditer(lexical_expression))
            for match in matches:
                root_scope = not root_rebound and 'define' not in scopes
                dot_scope = not any(scope in {'with', 'range', 'define'} for scope in scopes)
                lexical = root_scope if match['root'] else dot_scope
                node = tree.add('reference', parent_id=tree.root_id, label=match[0],
                                source_locator=source.span(start, end), attributes={
                                    'referenceKind': 'helm-values', 'valuePath': match['path'][1:].split('.'),
                                    'expression': expression, 'scope': 'chart-root' if lexical else 'unresolved-dot',
                                    'certainty': 'literal' if lexical else 'unresolved', 'resolved': False,
                                    'reason': 'cross-file-binding-unavailable' if lexical else 'dot-rebound'})
                tree.edges.append({'source': tree.root_id, 'target': node,
                                   'relation': 'template_reference', 'certainty': 'literal' if lexical else 'unresolved'})
            if command in {'include', 'template', 'tpl', 'lookup', 'index', 'default'} or ('.Values' in lexical_expression and not matches):
                tree.add('reference', parent_id=tree.root_id, label=command,
                         source_locator=source.span(start, end), attributes={'expression': expression,
                         'referenceKind': 'helm-expression', 'certainty': 'unresolved', 'resolved': False})
            if command in {'if', 'with', 'range', 'define', 'block'}:
                scopes.append('define' if command == 'block' else command)
                if len(scopes) > tree.depth_limit:
                    raise DocumentError('DEPTH_LIMIT', 'Превышена глубина Helm scope.')
            if re.match(r'^\$\s*:?=', lexical_expression):
                # Restoring shadowed variables requires a full Go binder; retain uncertainty
                # for the remaining source rather than claiming a chart-root reference.
                root_rebound = True
        if scopes:
            tree.warnings.append({'code': 'UNCLOSED_TEMPLATE_SCOPE'})

    @staticmethod
    def _action_end(text, offset):
        """Находит closing delimiter вне quoted Go literals и block comments."""
        quote = None
        escaped, comment = False, False
        while offset < len(text) - 1:
            char, pair = text[offset], text[offset:offset + 2]
            if comment:
                if pair == '*/':
                    comment = False
                    offset += 2
                    continue
            elif quote:
                if escaped:
                    escaped = False
                elif char == '\\' and quote != '`':
                    escaped = True
                elif char == quote:
                    quote = None
            elif pair == '/*':
                comment = True
                offset += 2
                continue
            elif char in {'"', "'", '`'}:
                quote = char
            elif pair == '}}':
                return offset
            offset += 1
        return -1

    @staticmethod
    def _mask_strings(expression):
        """Литералы не создают lexical value references; длина выражения сохраняется."""
        result = list(expression)
        quote, escaped = None, False
        for offset, char in enumerate(expression):
            if quote:
                result[offset] = ' '
                if escaped:
                    escaped = False
                elif char == '\\' and quote != '`':
                    escaped = True
                elif char == quote:
                    quote = None
            elif char in {'"', "'", '`'}:
                quote = char
                result[offset] = ' '
        return ''.join(result)
