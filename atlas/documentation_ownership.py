"""Comment ownership shared by source cards and documentation coverage."""


def comment_anchor(declaration):
    """Cross only declaration wrappers; never borrow comments from a containing class."""
    if declaration.type in {"arrow_function", "function_expression"}:
        parent = declaration.parent
        if parent and parent.type == "variable_declarator":
            declaration = parent.parent or parent
    parent = declaration.parent
    if parent and parent.type in {"export_statement", "decorated_definition"}:
        declaration = parent
    return declaration
