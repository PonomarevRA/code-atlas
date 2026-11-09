"""
Fail fast before native packaging when the pinned offline SQL grammar cannot actually parse SQL.
"""
from importlib import import_module, metadata

SQL_DISTRIBUTION = 'tree-sitter-sql'
SQL_VERSION = '0.3.11'


def check_sql_grammar():
    """
    Load the pinned grammar and parse a tiny in-memory schema without network or filesystem writes.

    Returns:
        Distribution/version metadata after a real Language/Parser compatibility check succeeds.
    Raises:
        RuntimeError: Required package/version is absent, native bindings fail to load, or parsing fails.
            The caller must synchronize requirements.lock before producing an offline application.
    """
    try:
        version = metadata.version(SQL_DISTRIBUTION)
        if version != SQL_VERSION:
            raise ValueError('Expected version ' + SQL_VERSION + ', found ' + version)
        grammar = import_module('tree_sitter_sql')
        runtime = import_module('tree_sitter')
        language = runtime.Language(grammar.language())
        parser = runtime.Parser(language)
        tree = parser.parse(b'CREATE TABLE atlas_sql_smoke (id INTEGER PRIMARY KEY);')
        if tree.root_node.has_error or tree.root_node.named_child_count == 0:
            raise ValueError('SQL grammar rejected the build smoke schema')
    except Exception as error:
        raise RuntimeError('Required offline SQL grammar tree-sitter-sql==' + SQL_VERSION +
                           ' is unavailable or incompatible. Synchronize requirements.lock in the target build environment.') from error
    return {'distribution': SQL_DISTRIBUTION, 'version': version}
