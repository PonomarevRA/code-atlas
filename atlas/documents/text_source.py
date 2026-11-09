"""Ограниченный UTF-8 ввод и точные независимые byte/character координаты."""
from array import array
from bisect import bisect_right

from .models import DocumentError, MAX_NODES, MAX_DEPTH, TreeBuilder

MAX_TEXT_INPUT_BYTES = 5 * 1024 * 1024


class TextSource:
    """Читает только captured файл и строит линейный индекс UTF-8 смещений."""

    def __init__(self, path, options):
        """Отклоняет вход больше 5 MiB и явно требует UTF-8 без замены повреждённых байтов."""
        limit = min(int(options.get('maxInputBytes', MAX_TEXT_INPUT_BYTES)), MAX_TEXT_INPUT_BYTES)
        with path.open('rb') as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise DocumentError('INPUT_LIMIT', 'Документ превышает предел текстового профиля.')
        try:
            self.text = raw.decode('utf-8')
        except UnicodeDecodeError as error:
            raise DocumentError('ENCODING_UNSUPPORTED', 'Требуется UTF-8 документ.') from error
        self.byte_offsets = array('I', [0])
        self.line_offsets = [0]
        offset = 0
        for index, char in enumerate(self.text):
            offset += len(char.encode('utf-8'))
            self.byte_offsets.append(offset)
            if char == '\n':
                self.line_offsets.append(index + 1)

    def span(self, start, end, **extra):
        """Возвращает полуоткрытый exact span с 0-based offsets и 1-based lines."""
        start, end = max(0, start), min(len(self.text), end)
        return {'kind': 'text', 'accuracy': 'exact', 'encoding': 'utf-8',
                'offsetBasis': 0, 'endExclusive': True, 'lineBasis': 1,
                'characterStart': start, 'characterEnd': end,
                'byteStart': self.byte_offsets[start], 'byteEnd': self.byte_offsets[end],
                'lineStart': bisect_right(self.line_offsets, start),
                'lineEnd': bisect_right(self.line_offsets, max(start, end - 1)), **extra}

    def lines(self, start, end):
        """Преобразует полуоткрытый parser line range в исходные координаты."""
        begin = self.line_offsets[min(start, len(self.line_offsets) - 1)]
        finish = self.line_offsets[end] if end < len(self.line_offsets) else len(self.text)
        return self.span(begin, finish)


class BoundedTree(TreeBuilder):
    """Проверяет budgets при добавлении, до накопления большого дерева."""

    def __init__(self, label, options):
        """Фиксирует ограниченные node/depth budgets до создания корня."""
        self.node_limit = min(int(options.get('maxNodes', MAX_NODES)), MAX_NODES)
        self.depth_limit = min(int(options.get('maxDepth', MAX_DEPTH)), MAX_DEPTH)
        self.depths = {}
        super().__init__(label)

    def add(self, kind, parent_id=None, **values):
        """Отклоняет узел, превышающий ограничение размера или глубины."""
        depth = self.depths.get(parent_id, -1) + 1
        if len(self.nodes) >= self.node_limit or depth > self.depth_limit:
            raise DocumentError('STRUCTURE_LIMIT', 'Превышен предел структуры документа.')
        result = super().add(kind, parent_id=parent_id, **values)
        self.depths[result] = depth
        return result
