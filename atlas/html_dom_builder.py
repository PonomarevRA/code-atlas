"""Адаптер pinned html5lib 1.1: HTML5 recovery с source provenance и бюджетами."""
from html5lib import HTMLParser, treebuilders
from html5lib._tokenizer import HTMLTokenizer
from html5lib.constants import tokenTypes

from .html_source_ranges import HtmlLimit

BaseTree = treebuilders.getTreeBuilder('etree')


class LocatedTokenizer(HTMLTokenizer):
    """Помечает реальный start token; синтетические токены остаются без метки."""
    def __iter__(self):
        """Сопоставляет конец токена с source index без эвристики по имени DOM-узла."""
        count = 0
        for token in super().__iter__():
            count += 1
            if count > 50000:
                raise HtmlLimit('Исчерпан бюджет токенов HTML5.')
            if token['type'] == tokenTypes['StartTag']:
                # Some HTML5 insertion modes discard the template node. The token
                # still distinguishes real markup from literal RCDATA or raw text.
                if token['name'] == 'template':
                    self.parser.saw_template = True
                line, column = self.stream.position()
                ending = self.parser.source_ranges.offsets[line - 1] + column
                record = self.parser.source_ranges.starts.get(ending)
                if record and record['tag'] == token['name']:
                    token['_atlas_source'] = record
            yield token
            # html5lib создаёт root неявным токеном, затем применяет реальный
            # <html>. Связываем только первое такое объявление, не поздние дубли.
            if token['type'] == tokenTypes['StartTag'] and token['name'] == 'html':
                root = self.parser.tree.getDocument()
                if token.get('_atlas_source') and root not in self.parser.tree.locations:
                    self.parser.tree.locations[root] = token['_atlas_source']


class LocatedTree(BaseTree):
    """Сохраняет создание, переносы и исправление дерева в реализации html5lib."""
    def __init__(self, namespaceHTMLElements):
        """Счётчик включает клоны formatting elements и не сбрасывается при recovery."""
        self.created, self.limit, self.locations = 0, 5000, {}
        owner = self
        base_element = self.elementClass

        class BoundedElement(base_element):
            """Ограничивает также узлы, созданные напрямую adoption-алгоритмом."""
            def __init__(self, *args, **kwargs):
                """Отказывает до создания узла сверх общего бюджета документа."""
                owner.created += 1
                if owner.created > owner.limit:
                    raise HtmlLimit('Исчерпан бюджет узлов HTML5-дерева.')
                super().__init__(*args, **kwargs)

        self.elementClass = BoundedElement
        base_comment = self.commentClass

        class BoundedComment(base_comment):
            """Комментарии также потребляют память parser, хотя не рисуются карточками."""
            def __init__(self, *args, **kwargs):
                """Останавливает comment-heavy документ до превышения общего бюджета."""
                owner.created += 1
                if owner.created > owner.limit:
                    raise HtmlLimit('Исчерпан бюджет узлов HTML5-дерева.')
                super().__init__(*args, **kwargs)

        self.commentClass = BoundedComment
        super().__init__(namespaceHTMLElements)

    def locate(self, node, token):
        """Привязывает только непосредственно созданный из source token элемент."""
        record = token.get('_atlas_source')
        if record:
            self.locations[node._element] = record
        if len(self.openElements) > 128:
            raise HtmlLimit('Исчерпан бюджет глубины HTML5-дерева.')
        return node

    def createElement(self, token):
        """Поддерживает root/table insertion без изменения правил вставки html5lib."""
        return self.locate(super().createElement(token), token)

    def insertElementNormal(self, token):
        """Обычная вставка обходит createElement в html5lib, поэтому тоже помечается."""
        return self.locate(super().insertElementNormal(token), token)


class LocatedParser(HTMLParser):
    """Private tokenizer hook pinned версии покрыт отдельными contract tests."""
    def __init__(self, ranges, max_elements):
        """Один экземпляр на документ; данные не разделяются между потоками."""
        self.source_ranges = ranges
        super().__init__(tree=LocatedTree, namespaceHTMLElements=True)
        self.tree.limit = max_elements
        self.error_count = 0

    def parseError(self, errorcode='XXX-undefined-error', datavars=None):
        """Хранит до восьми примеров ошибок; pathological recovery ограничен 4096 ошибками."""
        self.error_count += 1
        if self.error_count > 4096:
            raise HtmlLimit('Исчерпан бюджет ошибок HTML5 recovery.')
        if len(self.errors) < 8:
            super().parseError(errorcode, datavars)

    def _parse(self, stream, innerHTML=False, container='div', scripting=False, **kwargs):
        """Малый hook 1.1: Unicode-вход не требует повторного определения encoding."""
        self.innerHTMLMode, self.container, self.scripting = innerHTML, container, scripting
        self.saw_template = False
        self.tokenizer = LocatedTokenizer(stream, parser=self, **kwargs)
        self.reset()
        self.mainLoop()
