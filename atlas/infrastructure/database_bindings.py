"""Database provider evidence is scoped to registration sources, never inferred from table spelling."""
import re

ENGINES = {'UseNpgsql': 'PostgreSQL', 'UseSqlServer': 'SQL Server', 'UseSqlite': 'SQLite', 'UseMySql': 'MySQL'}


def database_candidates(syntaxes, configurations):
    """Annotate configuration alternatives when a project explicitly registers that engine and settings section."""
    result = [dict(c) for c in configurations if c['kind'] == 'database']
    for syntax in syntaxes:
        if syntax.language != 'csharp':
            continue
        calls = list(syntax.calls())
        engines = [(engine, node) for node, callee, _ in calls for method, engine in ENGINES.items() if callee.endswith('.' + method)]
        sections = set()
        for _, callee, values in calls:
            if callee.endswith('.GetSection') and values:
                match = re.fullmatch(r'\s*(?:nameof\((\w+)\)|"(\w+)")\s*', values[0])
                if match:
                    sections.add(match[1] or match[2])
        if not sections or len({engine for engine, _ in engines}) != 1:
            continue
        engine, node = engines[0]
        for candidate in result:
            section = candidate.get('key', candidate['name']).split('.', 1)[0]
            directory = candidate['file'].rsplit('/', 1)[0] + '/' if '/' in candidate['file'] else ''
            if section in sections and syntax.source['file'].startswith(directory):
                candidate['engine'] = engine
                candidate['providerFile'], candidate['providerLine'] = syntax.source['file'], node.start_point.row + 1
    return result
