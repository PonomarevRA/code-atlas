/** Present the common infrastructure contract without language-specific rendering or runtime assumptions. */
export function infrastructureCard(fact) {
  const system = { kafka: 'Kafka', rabbit: 'RabbitMQ', database: 'БД' }[fact.kind] || fact.kind;
  const resource = { topic: 'топик', queue: 'очередь', exchange: 'exchange', table: 'таблица' }[fact.resourceKind] || 'ресурс';
  const action = { publishes: 'Публикация', consumes: 'Получение', reads: 'Чтение', writes: 'Запись', uses: 'Использование' }[fact.operation] || fact.operation;
  let context = `${action} · ${fact.status === 'resolved' ? 'статическое имя' : 'имя вычисляется, не разрешено'}`;
  if (fact.status === 'configured') context = `${action} · имя из связанной конфигурации; runtime-среда неизвестна`;
  if (fact.status === 'candidate') context = `${action} · возможная таблица по generic-регистрации; конкретный тип вызова неизвестен`;
  const variants = [...new Set((fact.configurationCandidates || []).map(c => `${c.name}${c.engine ? ` (${c.engine})` : ''}`))];
  if (variants.length) context += ` · варианты конфигурации: ${variants.slice(0, 3).join(', ')}${variants.length > 3 || fact.configurationCandidatesTruncated ? '…' : ''}; ${fact.configurationBindingKnown ? 'привязка по регистрации' : 'привязка не установлена'}`;
  return { label: `${system} · ${resource}: ${fact.name || '?'}`, context,
    evidence: `${fact.file}:${fact.line}\n${fact.provider} · ${fact.basis}\n${(fact.evidence || []).map(e => `${e.file}:${e.line}`).join('\n')}\n${(fact.configurationCandidates || []).map(c => `${c.file}:${c.line}${c.bindingFile ? ` ← ${c.bindingFile}:${c.bindingLine}` : ''}`).join('\n')}\nАктивное окружение и runtime-использование неизвестны.` };
}
