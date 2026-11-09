/** Исходные пары словаря образуют отдельные полосы как switch; это данные, не выполненные ветви. */
export function staticDataScene(node) {
  const facts = node.staticData || node.dataFacts;
  if (!facts) return { visuals: [], edges: [] };
  const root = `${node.id}::static-data`;
  const visuals = [], edges = [];
  if (facts.kind === 'dictionary' && facts.entries?.length) {
    visuals.push({ id: root, ownerId: node.id, decision: true, kind: 'dictionary', label: 'Словарь',
      condition: node.label || node.id, line: facts.line, dataSelection: true });
    edges.push({ source: node.id, target: root, caption: 'объявленные пары' });
    for (const [index, entry] of facts.entries.slice(0, 60).entries()) {
      const id = `${root}:${index}`;
      visuals.push({ id, ownerId: node.id, exit: true, label: entry.key, condition: entry.value, line: entry.line, staticValue: true });
      edges.push({ source: root, target: id, caption: entry.key.slice(0, 42), armId: id });
    }
  } else {
    visuals.push({ id: root, ownerId: node.id, exit: true, label: facts.kind === 'constant' ? 'Константа' : 'Инициализатор',
      condition: facts.initializer || 'Значение не задано', line: facts.line, staticValue: true });
    edges.push({ source: node.id, target: root, caption: 'выражение из исходника' });
  }
  if (facts.truncated) {
    const id = `${root}:limited`;
    visuals.push({ id, ownerId: node.id, exit: true, omission: true, label: 'Данные ограничены',
      condition: 'Откройте исходник для полного объявления', line: facts.line });
    edges.push({ source: node.id, target: id });
  }
  return { visuals, edges };
}
