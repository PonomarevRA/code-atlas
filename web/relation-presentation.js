const RELATIONS = {
  reads: ['Читает поле', 'dependencies'], writes: ['Записывает поле', 'dependencies'], initializes_from: ['Инициализируется из', 'dependencies'],
  calls: ['Вызов', 'calls'], dispatches_to: ['Возможная реализация', 'calls'],
  method: ['Владелец метода', 'ownership'], defines: ['Объявление', 'ownership'],
  contains: ['Содержит', 'ownership'], field: ['Поле', 'ownership'], property: ['Свойство', 'ownership'],
  has_attribute: ['Атрибут', 'attributes'], applies_to: ['Применение атрибута', 'attributes'],
  has_parameter_attribute: ['Атрибут параметра', 'attributes'], has_return_attribute: ['Атрибут результата', 'attributes'],
  uses_test_data: ['Источник тестовых данных', 'dependencies'],
  duplicates: ['Повтор разметки', 'dependencies'],
  references: ['Ссылка', 'dependencies'], imports: ['Импорт', 'dependencies'],
  inherits: ['Наследование', 'dependencies'], implements: ['Реализует интерфейс', 'dependencies'],
};

/** Переводит подпись факта графа, сохраняя raw relation и отдельную оговорку об уверенности. */
export function presentRelation(edge) {
  const raw = edge.relation || 'не указана';
  const [label, group] = RELATIONS[raw] || [raw, 'dependencies'];
  const confidence = edge.confidence ?? edge.graphConfidence;
  const normalized = typeof confidence === 'string' ? confidence.toLowerCase() : confidence;
  const qualifier = normalized === 'candidate' || raw === 'dispatches_to' ? 'кандидат'
    : normalized === 'inferred' ? 'предположение анализа'
    : typeof confidence === 'number' ? `уверенность: ${confidence}` : '';
  const file = edge.source_file || edge.sourceFile;
  const line = edge.sourceLine || edge.line;
  return { label, group, raw, qualifier, location: file ? `${file}${line ? `:${line}` : ''}` : '' };
}
