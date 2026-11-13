"""Project proven consumer registrations into the existing ingress-card contract."""
import hashlib


def registered_entries(index, nodes):
    """Keep alternative consumers separate; handlers require a matching declared class/file namespace."""
    result = []
    for facts in index.facts.values():
        for fact in facts:
            handler = fact.get('handlerType')
            if fact['operation'] != 'consumes' or (not handler and fact['basis'] != 'consumer-registration'):
                continue
            matches = index.handlers.get(fact['id'], [])
            handlers = [n['id'] for n in matches]
            address = fact['name'] or 'адрес не определён'
            label = f"{fact['kind'].upper()} {address}"
            if handler:
                label += f" · {handler}"
            result.append({'id': 'entry-' + hashlib.sha256(fact['id'].encode()).hexdigest()[:20],
                'kind': fact['kind'], 'label': label, 'handler': handler,
                'origin': 'Статическая регистрация потребителя; runtime-выбор и производитель неизвестны.',
                'route': None, 'topic': fact['name'] if fact['kind'] == 'kafka' else None,
                'queue': fact['name'] if fact['kind'] == 'rabbit' else None, 'httpMethod': None,
                'handlerNodeIds': handlers, 'codeProjectId': matches[0].get('codeProjectId') if matches else None,
                'evidence': [{'file': fact['file'], 'line': fact['line']}, *fact['evidence']],
                'confidence': 'candidate', 'unresolved': ['Условие регистрации и активное окружение не вычисляются.']
                    + ([] if handlers else ['Однозначная реализация обработчика отсутствует в снимке.']),
                'configFile': None, 'infrastructure': fact})
    return result
