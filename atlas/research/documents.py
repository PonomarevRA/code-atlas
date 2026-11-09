"""Compose traceable factual Markdown, without model prose or inferred business rules."""
from urllib.parse import urlencode
import re
from .storage import now


def _prose(value):
    """Escape untrusted prose in exported Markdown; code excerpts use length-safe fences."""
    return re.sub(r'([\\`*_{}\[\]<>#!|~])', r'\\\1', str(value or '')).replace('\n', ' ')


def _flow_lines(steps, depth=0, budget=None):
    """Print source control structure literally; alternate arms and deferred bodies never become executed steps."""
    budget = budget if budget is not None else [60]
    lines = []
    for step in steps:
        if budget[0] <= 0 or depth > 6:
            lines.append('  ' * depth + '- … структура сокращена; смотрите исходник.')
            break
        budget[0] -= 1
        condition = step.get('condition')
        label = str(condition if isinstance(condition, str) else step.get('label') or step.get('expression') or step.get('kind', 'шаг'))[:400]
        lines.append('  ' * depth + '- ' + _prose(label) + f" (строка {step.get('line', '?')})")
        for key in ('steps', 'condition', 'body', 'update', 'then', 'else', 'branches', 'arms', 'cases', 'children'):
            children = step.get(key)
            if isinstance(children, dict):
                children = [children]
            if isinstance(children, list) and children:
                lines.append('  ' * (depth + 1) + '- ' + {'then': 'Если условие истинно', 'else': 'Иначе', 'body': 'Тело', 'arms': 'Варианты'}.get(key, key) + ':')
                lines.extend(_flow_lines([child for child in children if isinstance(child, dict)], depth + 2, budget))
    return lines


def compose_document(run: dict, evidence: list[dict], warnings: list[str]) -> dict:
    """Use only captured comments, literal AST controls, relations and excerpts; the question remains a research scope."""
    title = 'Документация по локальным фактам' if run['workflowKind'] == 'documentation' else 'Собранные факты о проекте'
    lines = [f'# {title}', '', f"Вопрос: {_prose(run['question']) or 'Объяснить выбранный символ'}", '',
             'Режим: локальные факты, без LLM. Текст составлен по правилам; смысловой ответ на свободный вопрос моделью не формировался.',
             f"Снимок карты: {run['snapshotId']}", '', '## Что удалось найти', '']
    codes = [item for item in evidence if item['kind'] == 'code']
    if not codes:
        lines.append('Совпадения не найдены. Укажите имя символа или выберите его на карте. Отсутствие совпадений не доказывает отсутствие логики.')
    for item in codes:
        ref = f"[{item['id']}]"
        lines.extend([f"### {_prose(item['label'])} {ref}", '', f"Исходник: {_prose(item.get('file')) or 'недоступен'}, строка {item.get('line') or '?' }."])
        description = item.get('description', {})
        doc = description.get('documentation') or {}
        purpose = doc.get('summary')
        lines.extend(['', 'Описание автора этого объявления: ' + _prose(purpose) if purpose else 'Самостоятельное описание назначения не найдено.'])
        if not purpose and item.get('capturedComment'):
            lines.append(f"Комментарий из снимка (область: {item.get('commentScope', 'unknown')}): {_prose(item['capturedComment'])}")
        if doc.get('returns'):
            lines.append('Результат по комментарию автора: ' + _prose(doc['returns']))
        flow = description.get('methodFlow') or {}
        if flow.get('steps'):
            lines.extend(['', 'Структура локального тела (варианты условий не вычисляются):', *_flow_lines(flow['steps'])])
        else:
            lines.append('Структурированное дерево этого тела недоступно; ниже приведена выдержка файла.')
        if flow.get('truncated') or flow.get('unsupported') or item.get('descriptionTruncated'):
            lines.append('Часть синтаксиса или структуры сокращена / не поддержана.')
        if item['relations']:
            lines.extend(['', 'Связи в статической карте (не порядок исполнения):'])
            for edge in item['relations']:
                meaning = {'dispatches_to': 'возможная реализация', 'method': 'объявление метода'}.get(edge['relation'], 'статический вызов')
                if edge.get('confidence') in {'INFERRED', 'candidate'}:
                    meaning += ' (кандидат)'
                lines.append(f"- {meaning}: {_prose(edge['label'])}")
        if item.get('relationsTruncated'):
            lines.append('Список связей сокращён.')
        for notice in description.get('unresolved') or []:
            lines.append('Не установлено: ' + _prose(notice))
        if item.get('source'):
            source = item['source']
            fence = '`' * max(3, max((len(part) for part in re.findall(r'`+', source)), default=0) + 1)
            lines.extend(['', 'Выдержка исходника:', fence, source, fence])
        if item.get('sourceTruncated'):
            lines.append('Выдержка сокращена; это не полный файл.')
        lines.append('')
    lines.extend(['## Локальные языковые и библиотечные справочники', ''])
    references = [item for item in evidence if item['kind'] != 'code']
    if not references:
        lines.append('Подходящие выдержки в уже импортированных справочниках не найдены. Импорт и индексация не запускались автоматически.')
    for item in references:
        lines.extend([f"### {_prose(item['label'])} [{item['id']}]", f"Версия: {_prose(item.get('version')) or 'не указана'}; редакция: {_prose(item.get('edition')) or 'не указана'}.",
                      '', _prose(item.get('text', '')), '', item['warning'], ''])
    lines.extend(['## Границы и неизвестные сведения', '',
                  '- Карта содержит статические факты; фактические ветви, значения параметров и выбор DI не установлены.',
                  '- Комментарии являются утверждениями автора, их смысловая корректность не проверена.',
                  '- Текущие файлы могут отличаться от сохранённой карты; документ сохраняет прочитанные выдержки и хэши.',
                  '- Лексический поиск не понимает синонимы. Для точного исследования выберите символ.',
                  *['- ' + _prose(warning) for warning in dict.fromkeys(warnings)]])
    citations = []
    for item in evidence:
        link = '/?' + urlencode({'repo': run['projectId'], 'node': item['nodeId'], 'display': 'flow'}) if item['kind'] == 'code' else '/language-docs.html' if item['kind'] == 'language' else '/library-docs.html?' + urlencode({'repo': run['projectId']})
        citations.append({'id': item['id'], 'label': item['label'], 'url': link, 'sourceHash': item.get('sourceHash'),
                          'kind': item['kind'], 'file': item.get('file'), 'line': item.get('line')})
    lines.extend(['', '## Источники', *[f"- [{cite['id']}] {_prose(cite['label'])} — {cite.get('sourceHash') or 'хэш недоступен'}" for cite in citations]])
    return {'title': title, 'markdown': '\n'.join(lines), 'citations': citations, 'createdAt': now(),
            'generationMode': 'local-facts', 'semanticReview': 'not-performed', 'warnings': list(dict.fromkeys(warnings))}
