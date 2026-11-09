"""Explicit C# producer constructor/DI bindings narrow configuration candidates without choosing an environment."""
import re
from collections import defaultdict
from .syntax import text


def type_key(value):
    """Normalize qualified type spellings; multiple matching registrations remain candidates."""
    return re.sub(r'\b(?:\w+\.)+(\w+)', r'\1', re.sub(r'\s+', '', value))


def _producer_classes(syntaxes):
    """Index non-generic producer declarations by namespace; generic namesakes cannot win a name match."""
    result = defaultdict(list)
    for syntax in syntaxes:
        namespace = re.search(r'\bnamespace\s+([\w.]+)', syntax.source['text'])
        if syntax.language != 'csharp' or not namespace:
            continue
        for node in syntax.nodes:
            if node.type == 'class_declaration' and node.child_by_field_name('type_parameters') is None:
                name = text(node.child_by_field_name('name'), syntax.content)
                result[(namespace[1], name)].append((syntax.source['file'], name))
    return result


def _topic_records(syntax, node, configurations):
    """Find only constructor arguments explicitly ending in a settings-section TopicName property."""
    args = node.child_by_field_name('arguments')
    if args is None:
        return
    for argument in args.named_children:
        suffix = re.search(r'(\w+\.TopicName)\s*$', text(argument, syntax.content).strip())
        if not suffix:
            continue
        matches = [c for c in configurations if c['kind'] == 'kafka' and c.get('key', '').endswith(suffix[1])]
        if matches:
            yield {'candidates': matches, 'file': syntax.source['file'], 'line': node.start_point.row + 1}


def _registration_services(syntax, node):
    """Read enclosing DI service types while retaining the exact producer-construction evidence."""
    parent = node.parent
    while parent and parent.type not in {'method_declaration', 'class_declaration'}:
        if parent.type == 'invocation_expression':
            function = text(parent.child_by_field_name('function'), syntax.content)
            service = re.search(r'Add(?:Singleton|Scoped|Transient)\s*<\s*([\w.]*IKafkaProducer\s*<[^>]+>)', function)
            if service:
                yield type_key(service[1])
        parent = parent.parent


class ProducerBindings:
    """Only constructor TopicName arguments and explicit IKafkaProducer registrations establish a binding."""
    def __init__(self, syntaxes, configurations):
        """Index declared producer classes and service registrations once for the bounded corpus."""
        self.by_class, self.by_service = defaultdict(list), defaultdict(list)
        classes = _producer_classes(syntaxes)
        for syntax in syntaxes:
            if syntax.language == 'csharp':
                self._bind_constructors(syntax, configurations, classes)

    def _bind_constructors(self, syntax, configurations, classes):
        """Keep every visible constructor/configuration alternative; no environment is selected."""
        namespaces = re.findall(r'\busing\s+([\w.]+)\s*;', syntax.source['text'])
        namespace = re.search(r'\bnamespace\s+([\w.]+)', syntax.source['text'])
        if namespace:
            namespaces.append(namespace[1])
        for node in syntax.nodes:
            if node.type != 'object_creation_expression':
                continue
            constructor = text(node.child_by_field_name('type'), syntax.content)
            if not constructor.endswith('KafkaProducer'):
                continue
            for record in _topic_records(syntax, node, configurations):
                for scope in namespaces:
                    for key in classes.get((scope, constructor), []):
                        self.by_class[key].append(record)
                for service in _registration_services(syntax, node):
                    self.by_service[service].append(record)

    def candidates(self, syntax, fact):
        """Bind a producer's own body or a declared receiver service; unresolved receivers return no binding."""
        records = []
        for cls in syntax.nodes:
            if cls.type != 'class_declaration' or not cls.start_point.row + 1 <= fact['line'] <= cls.end_point.row + 1:
                continue
            name = text(cls.child_by_field_name('name'), syntax.content)
            records.extend(self.by_class.get((syntax.source['file'], name), []))
            raw = text(cls, syntax.content)
            receiver = (fact.get('callee') or '').split('.', 1)[0]
            declared = re.search(r'([\w.]*IKafkaProducer\s*<[^>]+>)\s+' + re.escape(receiver) + r'\b', raw)
            if declared:
                records.extend(self.by_service.get(type_key(declared[1]), []))
        candidates, seen = [], set()
        for record in records:
            for candidate in record['candidates']:
                key = (candidate['file'], candidate.get('key'), candidate['name'])
                if key not in seen:
                    candidates.append({**candidate, 'bindingFile': record['file'], 'bindingLine': record['line']})
                    seen.add(key)
        return candidates
