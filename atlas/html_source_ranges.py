"""Лексические диапазоны исходника отдельно от исправленного HTML5-дерева."""
from html.parser import HTMLParser
from bisect import bisect_right
from collections import Counter

VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}


class HtmlLimit(ValueError):
    """Бюджет останавливает анализ, а не публикует выдуманное полное дерево."""


def normalize_newlines(text):
    """Нормализует CR/CRLF для HTML5, сохраняя карту границ к исходным символам."""
    characters, boundaries = [], [0]
    index = 0
    while index < len(text):
        character = text[index]
        if character == '\r':
            index += 2 if text[index:index + 2] == '\r\n' else 1
            characters.append('\n')
        else:
            index += 1
            characters.append(character)
        boundaries.append(index)
    return ''.join(characters), boundaries


class SourceRanges(HTMLParser):
    """Индексирует явные start tags; не решает, кто кому родитель в DOM."""
    def __init__(self, text, boundaries, max_elements):
        """Принимает нормализованный текст и карту к исходнику; ограничивает токены."""
        super().__init__(convert_charrefs=False)
        self.text, self.boundaries, self.limit = text, boundaries, max_elements
        self.offsets = [0] + [i + 1 for i, char in enumerate(text) if char == '\n']
        self.starts, self.stack, self.records = {}, [], []
        self.open_tags = Counter()
        self.template = False

    def position(self):
        """Возвращает смещение в нормализованном тексте по позиции HTMLParser."""
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        """Сохраняет start tag; точный полный диапазон требует явной пары."""
        if len(self.records) >= self.limit:
            raise HtmlLimit('Исчерпан бюджет HTML-токенов.')
        start = self.position()
        ending = start + len(self.get_starttag_text())
        record = {'tag': tag, 'start': self.boundaries[start], 'line': self.getpos()[0],
                  'startTagEnd': self.boundaries[ending], 'end': None, 'endLine': None,
                  'rangeStatus': 'start-tag-only'}
        self.starts[ending] = record
        self.records.append(record)
        if tag in VOID:
            record.update(end=record['startTagEnd'], endLine=bisect_right(self.offsets, ending - 1), rangeStatus='exact')
        else:
            self.stack.append(record)
            self.open_tags[tag] += 1
        self.template |= any(value and any(marker in value for marker in ('{{', '{%', '<%')) for _, value in attrs)

    def handle_startendtag(self, tag, attrs):
        """Сохраняет XML-подобный тег; self-closing семантику определяет HTML5-парсер."""
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            record = self.stack.pop()
            self.open_tags[tag] -= 1
            record.update(end=record['startTagEnd'], endLine=bisect_right(self.offsets, self.position() + len(self.get_starttag_text()) - 1), rangeStatus='self-closing-token')

    def handle_endtag(self, tag):
        """Неоднозначные пары не получают точного полного source range."""
        if not self.stack or not self.open_tags[tag]:
            return
        if self.stack[-1]['tag'] != tag:
            # Recovery принадлежит html5lib; неоднозначные source ranges неизвестны.
            matching = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i]['tag'] == tag), None)
            if matching is not None:
                for record in self.stack[matching:]:
                    self.open_tags[record['tag']] -= 1
                del self.stack[matching:]
            return
        record = self.stack.pop()
        self.open_tags[tag] -= 1
        ending = self.text.find('>', self.position())
        if ending >= 0:
            record.update(end=self.boundaries[ending + 1], endTagStart=self.boundaries[self.position()],
                          endLine=bisect_right(self.offsets, ending), rangeStatus='exact')

    def handle_data(self, data):
        """Шаблонный текст запрещает доказательство дублей, но не DOM-навигацию."""
        tag = self.stack[-1]['tag'] if self.stack else ''
        if tag not in {'script', 'style'}:
            self.template |= any(marker in data for marker in ('{{', '{%', '<%'))
