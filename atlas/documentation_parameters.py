"""Имена формальных входов и наличие собственных описаний; AST не выполняется."""
import re


def _text(node, source: bytes) -> str:
    """Читает доказанный AST диапазон UTF-8 для проверки имени или декоратора."""
    return source[node.start_byte:node.end_byte].decode('utf-8')


def parameter_described(name: str, contract: dict, language: str) -> bool:
    """Засчитывает собственное описание, исключая пустые теги, заглушки и повтор имени.

    GoDoc/Rustdoc могут описывать входы прозой. Проверяется только наличие
    связанного текста, а не его смысловая точность; объём текста баллов не даёт.

    :param name: Доказанное формальное имя входа.
    :param contract: Только принадлежащий объявлению разобранный комментарий.
    :param language: Язык, определяющий допустимость GoDoc/Rustdoc прозы.
    :returns: Есть ли непустое описание, отличающееся от заглушки и имени.
    """
    description = contract['params'].get(name, '').strip()
    if not description and language in {'go', 'rust'}:
        purpose = contract['purpose']
        if re.search(r'\b' + re.escape(name) + r'\b', purpose) and len(purpose.split()) >= 3:
            description = purpose
    normalized = re.sub(r'[\W_]', '', description).casefold()
    if not normalized or normalized == re.sub(r'[\W_]', '', name).casefold():
        return False
    return not bool(re.match(r'^(?:TODO|FIXME|TBD)\b', description, re.I) or
                    re.fullmatch(r'(?:N/?A|description|parameter|описание|параметр)[.!\s]*', description, re.I))


def _implicit_python_receiver(declaration, source: bytes) -> bool:
    """Признаёт self/cls неявным входом лишь у метода класса без @staticmethod.

    :param declaration: Объявление Python-функции с исходными lexical родителями.
    :param source: Исходник для проверки непосредственно принадлежащих декораторов.
    :returns: Контекст обычного/class метода; свободные и static-функции исключены.
    """
    owner = declaration.parent
    if owner and owner.type == 'decorated_definition':
        decorators = [_text(node, source).strip() for node in owner.named_children if node.type == 'decorator']
        if any(value in {'@staticmethod', '@builtins.staticmethod'} for value in decorators):
            return False
        owner = owner.parent
    return bool(owner and owner.type == 'block' and owner.parent and owner.parent.type == 'class_definition')


def _parameter_binding(node):
    """Разворачивает только оболочки binding, не читая default/type как имя входа.

    :param node: AST одного формального параметра из поставляемой грамматики.
    :returns: Простой identifier либо None для неизвестного/сложного pattern; до 12 шагов.
    """
    wrappers = {'assignment_pattern', 'rest_pattern', 'required_parameter', 'optional_parameter',
                'typed_parameter', 'list_splat_pattern', 'dictionary_splat_pattern', 'spread_parameter'}
    for _ in range(12):
        if node.type == 'identifier':
            return node
        selected = node.child_by_field_name('name') or node.child_by_field_name('pattern') or node.child_by_field_name('left')
        if selected is not None:
            node = selected
        elif node.type in wrappers and node.named_children:
            node = node.named_children[0]
        else:
            return None
    return None


def parameter_inventory(declaration, source: bytes, language: str) -> tuple[list[str], bool]:
    """Возвращает формальные имена и полноту без угадывания сложных patterns.

    :param declaration: AST метода/функции, к которому принадлежат параметры.
    :param source: UTF-8 исходник с совпадающими AST диапазонами.
    :param language: Имя поставляемой грамматики, определяющее receivers и группы Go.
    :returns: Имена в порядке объявления и признак полного разбора binding.
    """
    parameters = declaration.child_by_field_name('parameters') or declaration.child_by_field_name('parameter')
    if parameters is None:
        return [], True
    if parameters.type == 'identifier':
        nodes = [parameters]
    else:
        nodes = parameters.named_children
    names, complete = [], True
    for node in nodes:
        if node.type in {'comment', 'line_comment', 'block_comment', 'positional_separator', 'keyword_separator'}:
            continue
        if language == 'rust' and node.type == 'self_parameter':
            continue
        if language == 'go':
            group = node.children_by_field_name('name')
            names.extend(_text(item, source) for item in group)
            complete &= bool(group)
            continue
        binding = _parameter_binding(node)
        if binding is None:
            complete = False
        else:
            name = _text(binding, source)
            if language == 'csharp':
                name = name.removeprefix('@')  # XML param name использует имя без lexical escape.
            names.append(name)
    if language == 'python' and names and names[0] in {'self', 'cls'} and _implicit_python_receiver(declaration, source):
        names = names[1:]
    return names, complete


def parameter_names(declaration, source: bytes, language: str) -> list[str]:
    """Совместимый список имён; метрика использует также полноту inventory.

    :param declaration: AST объявления с формальными параметрами.
    :param source: UTF-8 исходник этого AST.
    :param language: Грамматика для правил receiver/binding.
    :returns: Только доказанные формальные имена; сложные patterns не угадываются.
    """
    names, _ = parameter_inventory(declaration, source, language)
    return names
