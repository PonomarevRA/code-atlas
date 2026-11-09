"""Connector recognizers return one public contract, independently of the source language."""
import re
from .syntax import arguments, literal

VERSION = 'infrastructure-v1'
LIBRARIES = {'kafka': ('confluent.kafka', 'kafka', 'rdkafka', 'kafkajs', 'sarama'),
             'rabbit': ('rabbitmq', 'pika', 'amqplib', 'amqp', 'masstransit'),
             'database': ('entityframework', 'npgsql', 'sqlalchemy', 'sqlite', 'psycopg', 'jdbc', 'database/sql', 'sqlx', 'dapper', 'prisma')}
SQL_TABLE = re.compile(r'\b(FROM|JOIN|INTO|UPDATE)\s+([\w.]+|"[\w]+"(?:\."[\w]+")?)', re.I)
SQL_IGNORED = re.compile(r"'(?:''|[^'])*'|--[^\n]*|/\*[\s\S]*?\*/|\$(?P<tag>\w*)\$[\s\S]*?\$(?P=tag)\$")


def library_hints(syntax):
    """A namespace/import is only a detector hint, never proof of a running broker or database."""
    imports = ' '.join(syntax.content[n.start_byte:n.end_byte].decode('utf-8').lower()
                       for n in syntax.nodes if n.type in {'using_directive', 'import_statement', 'import_from_statement',
                       'import_declaration', 'import_spec', 'use_declaration'})
    return {kind for kind, names in LIBRARIES.items() if any(name in imports for name in names)}


def _envelope(values, key):
    """Accept a literal named resource in JS/Go message envelopes; message bodies are not returned."""
    for value in values:
        match = re.search(r'\b' + key + r'\s*:\s*((?:"[^"\n]*")|(?:\x27[^\x27\n]*\x27))', value, re.I)
        if match:
            return match[1]
    return None


def detect_calls(syntax):
    """Extract operations with library/receiver evidence, retaining unresolved destinations as unknown."""
    hints = library_hints(syntax)
    for node, callee, values in syntax.calls():
        name = re.sub(r'<.*>', '', callee, flags=re.S).rsplit('.', 1)[-1].lower()
        named, positional = arguments(values)
        first = positional[0] if positional else None
        hint = callee.lower()
        kind, resource, operation, expression = None, None, None, None
        # Ambiguous Send/Consume spellings need a messaging receiver as well as an import.
        kafka_receiver = bool(re.search(r'kafka|producer|consumer|template|writer|futurerecord', hint))
        if ('kafka' in hints and (name not in {'send', 'consume'} or kafka_receiver)) or 'kafka' in hint or 'futurerecord::to' in hint:
            if name in {'produceasync', 'produce', 'send', 'writemessages', 'futurerecord::to', 'totopic'}:
                kind, resource, operation = 'kafka', 'topic', 'publishes'
                expression = named.get('topic') or _envelope(values, 'topic') or first
            elif name in {'subscribe', 'consume', 'readmessage'}:
                kind, resource, operation, expression = 'kafka', 'topic', 'consumes', named.get('topic') or first
        if 'rabbit' in hints or 'rabbit' in hint:
            if name in {'basicconsume', 'basic_consume', 'consume', 'addfintechjsonconsumer', 'addfintechjsonbatchconsumer', 'receiveendpoint', 'consumefrom'}:
                kind, resource, operation, expression = 'rabbit', 'queue', 'consumes', named.get('queue') or first
            elif name in {'basicpublish', 'basic_publish', 'publish'}:
                kind, resource, operation = 'rabbit', 'exchange', 'publishes'
                expression = named.get('exchange') or first
        if kind:
            handler = re.search(r'AddFintechJson(?:Batch)?Consumer\s*<\s*([\w.]+)', callee) if kind == 'rabbit' else None
            yield {'kind': kind, 'resourceKind': resource, 'operation': operation, 'expression': expression,
                   'line': node.start_point.row + 1, 'column': node.start_point.column + 1,
                   **({'handlerType': handler[1]} if handler else {}), 'callee': callee, 'language': syntax.language,
                   'provider': VERSION, 'basis': 'library-call' if kind in hints else 'receiver-name-call'}
        if 'database' in hints and name in {'query', 'queryasync', 'querysingleasync', 'execute', 'executeasync', 'executereaderasync', 'exec', 'querycontext'}:
            sql = literal(first or '')
            if sql is None:
                continue
            masked = SQL_IGNORED.sub(' ', sql)
            aliases = set(re.findall(r'(?:\bWITH|,)\s*(\w+)\s+AS\s*\(', masked, re.I))
            for match in SQL_TABLE.finditer(masked):
                if match[2] in aliases:
                    continue
                writes = match[1].upper() in {'INTO', 'UPDATE'} or bool(re.match(r'\s*DELETE\b', sql, re.I))
                yield {'kind': 'database', 'resourceKind': 'table', 'operation': 'writes' if writes else 'reads',
                       'expression': repr(match[2].replace('"', '')), 'line': node.start_point.row + 1,
                       'language': syntax.language, 'provider': VERSION, 'basis': 'sql-literal'}
