"""Явные AST-контракты языков для объявлений членов, без загрузки кода проекта."""
from dataclasses import dataclass


@dataclass(frozen=True)
class MemberLanguage:
    """Имена узлов грамматики отделены от извлечения и публикации графа."""
    name: str
    module: str
    classes: frozenset[str]
    methods: frozenset[str]
    fields: frozenset[str]
    language_function: str = 'language'


def spec(name, classes, methods, fields, function='language'):
    """Создаёт неизменяемую конфигурацию установленной Tree-sitter грамматики."""
    return MemberLanguage(name, 'tree_sitter_' + name, frozenset(classes.split()), frozenset(methods.split()), frozenset(fields.split()), function)


PYTHON = spec('python', 'class_definition', 'function_definition', 'assignment augmented_assignment')
JAVA = spec('java', 'class_declaration interface_declaration record_declaration enum_declaration', 'method_declaration constructor_declaration compact_constructor_declaration', 'field_declaration constant_declaration')
JS = spec('javascript', 'class_declaration class', 'function_declaration generator_function_declaration method_definition', 'field_definition variable_declarator')
TS = spec('typescript', 'class_declaration abstract_class_declaration interface_declaration', 'function_declaration method_definition method_signature abstract_method_signature', 'public_field_definition property_signature variable_declarator', 'language_typescript')
TSX = spec('typescript', 'class_declaration abstract_class_declaration interface_declaration', 'function_declaration method_definition method_signature abstract_method_signature', 'public_field_definition property_signature variable_declarator', 'language_tsx')
GO = spec('go', 'type_spec', 'function_declaration method_declaration', 'field_declaration const_spec var_spec')
CPP = spec('cpp', 'class_specifier struct_specifier', 'function_definition', 'field_declaration declaration')
KOTLIN = spec('kotlin', 'class_declaration object_declaration', 'function_declaration secondary_constructor', 'property_declaration')
PHP = spec('php', 'class_declaration interface_declaration trait_declaration', 'method_declaration function_definition', 'property_declaration const_declaration', 'language_php')
RUST = spec('rust', 'struct_item enum_item trait_item impl_item', 'function_item function_signature_item', 'field_declaration const_item static_item')
SWIFT = spec('swift', 'class_declaration', 'function_declaration init_declaration', 'property_declaration')
LANGUAGES = {'.py': PYTHON, '.java': JAVA, '.js': JS, '.mjs': JS, '.cjs': JS, '.jsx': JS,
             '.ts': TS, '.tsx': TSX, '.go': GO, '.cpp': CPP, '.cc': CPP, '.cxx': CPP, '.hpp': CPP,
             '.kt': KOTLIN, '.php': PHP, '.rs': RUST, '.swift': SWIFT}
