"""CommonMark/table дерево без исполнения HTML, ссылок и fenced кода."""
from .models import DocumentError
from .text_source import BoundedTree, TextSource


class MarkdownProvider:
    """markdown-it сохраняет block spans; inline spans честно отмечены structural."""
    id = 'document-markdown'
    version = '1'
    capabilities = {'formats': ['.md', '.markdown'], 'tree': True, 'tables': True,
                    'references': True, 'executesContent': False, 'offlineCertified': False}

    @property
    def runtime_fingerprint(self):
        """Версии parser и host adapter входят в ключ неизменяемого результата."""
        from .providers import runtime_fingerprint
        return runtime_fingerprint(['markdown-it-py', 'mdurl'], __file__)

    def parse(self, path, options, context):
        """Разбирает ограниченный captured UTF-8 файл, не читая внешние ресурсы."""
        try:
            from markdown_it import MarkdownIt
        except ImportError as error:
            raise DocumentError('CAPABILITY_UNAVAILABLE', 'markdown-it-py не установлен.') from error
        source = TextSource(path, options)
        tree = BoundedTree(context.get('logicalPath', path.name), options)
        parser = MarkdownIt('commonmark', {'html': True, 'maxNesting': tree.depth_limit}).enable('table')
        environment = {}
        markdown = self._front_matter(source, tree)
        tokens = parser.parse(markdown, environment)
        if len(tokens) > 300000:
            raise DocumentError('TOKEN_LIMIT', 'Превышен предел Markdown tokens.')
        if any(token.level >= tree.depth_limit - 1 for token in tokens):
            raise DocumentError('DEPTH_LIMIT', 'Markdown достиг предела вложенности parser.')
        self._blocks(tokens, source, tree)
        for name, definition in environment.get('references', {}).items():
            tree.add('reference', parent_id=tree.root_id, label=name,
                     attributes={'referenceKind': 'definition', 'target': definition['href']},
                     source_locator=source.lines(*definition['map']))
        return tree.build()

    def _front_matter(self, source, tree):
        """Сохраняет opaque YAML front matter, не меняя parser line coordinates."""
        lines = source.text.splitlines(keepends=True)
        if not lines or lines[0].strip() != '---':
            return source.text
        for index, line in enumerate(lines[1:], 1):
            if line.strip() not in {'---', '...'}:
                continue
            end = sum(len(value) for value in lines[:index + 1])
            tree.add('code', parent_id=tree.root_id, text=source.text[:end],
                     source_locator=source.span(0, end), attributes={'syntax': 'yaml-front-matter',
                     'interpretation': 'opaque'})
            padding = ''.join(char if char in '\r\n' else ' ' for char in source.text[:end])
            return padding + source.text[end:]
        return source.text

    def _blocks(self, tokens, source, tree):
        """Проецирует parser tokens в containment, сохраняя повторы и skipped headings."""
        section_scopes, containers = {}, []
        heading_level = None
        row_number, column_number = -1, 0
        for token in tokens:
            scope = containers[-1][1] if containers else tree.root_id
            sections = section_scopes.setdefault(scope, [])
            parent = sections[-1][1] if sections else scope
            locator = source.lines(*token.map) if token.map else {'kind': 'text', 'accuracy': 'structural'}
            if token.type == 'heading_open':
                heading_level = int(token.tag[1:])
                while sections and sections[-1][0] >= heading_level:
                    sections.pop()
                continue
            if token.type == 'heading_close':
                heading_level = None
                continue
            if token.type == 'inline':
                if heading_level is not None:
                    parent = sections[-1][1] if sections else scope
                    node = tree.add('section', parent_id=parent, label=token.content, text=token.content,
                                    source_locator=locator, attributes={'headingLevel': heading_level})
                    sections.append((heading_level, node))
                elif containers and containers[-1][0] in {'th_open', 'td_open'}:
                    node = containers[-1][1]
                    tree.nodes[int(node)]['text'] = token.content
                else:
                    node = tree.add('text', parent_id=parent, text=token.content, source_locator=locator)
                self._references(token.children or [], node, tree)
                continue
            if token.type.endswith('_close'):
                expected = token.type.removesuffix('_close') + '_open'
                if containers and containers[-1][0] == expected:
                    containers.pop()
                continue
            kinds = {'bullet_list_open': 'list', 'ordered_list_open': 'list', 'list_item_open': 'item',
                     'table_open': 'table', 'tr_open': 'row', 'th_open': 'cell', 'td_open': 'cell',
                     'blockquote_open': 'section'}
            if token.type in kinds:
                attributes = {}
                if token.type == 'table_open':
                    row_number = -1
                    attributes = {'headerStatus': 'explicit', 'indexBasis': 0}
                elif token.type == 'tr_open':
                    row_number += 1
                    column_number = 0
                    attributes = {'row': row_number}
                elif token.type in {'th_open', 'td_open'}:
                    attributes = {'row': row_number, 'column': column_number, 'rowSpan': 1,
                                  'columnSpan': 1, 'header': token.type == 'th_open'}
                    column_number += 1
                elif token.type.endswith('list_open'):
                    attributes = {'ordered': token.type == 'ordered_list_open', 'start': token.attrGet('start') or 1}
                node = tree.add(kinds[token.type], parent_id=parent, source_locator=locator, attributes=attributes)
                containers.append((token.type, node))
            elif token.type in {'fence', 'code_block', 'html_block'}:
                kind = 'code' if token.type != 'html_block' else 'text'
                tree.add(kind, parent_id=parent, text=token.content, source_locator=locator,
                         attributes={'syntax': token.info, 'rawHtml': token.type == 'html_block'})

    def _references(self, tokens, parent, tree):
        """Сохраняет literal links/images как данные; inline offsets не выдумываются."""
        for token in tokens:
            if token.type not in {'link_open', 'image'}:
                continue
            target = token.attrGet('href') if token.type == 'link_open' else token.attrGet('src')
            node = tree.add('reference', parent_id=parent, label=token.content, source_locator={
                'kind': 'text', 'accuracy': 'structural'}, attributes={'target': target,
                'referenceKind': 'image' if token.type == 'image' else 'link', 'certainty': 'literal'})
            tree.edges.append({'source': parent, 'target': node, 'relation': 'references', 'certainty': 'literal'})
