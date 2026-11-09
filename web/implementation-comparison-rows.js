/** Стабильное разрешение равенств не зависит от порядка связей снимка. */
const compareIds = (a, b) => a < b ? -1 : a > b ? 1 : 0;

/** Индексирует источники dispatches_to по целям один раз для всех рангов раскладки. */
export function implementationComparisonGroups(edges) {
  const sourcesByTarget = new Map();
  for (const edge of edges) {
    if (edge.relation !== 'dispatches_to' || edge.target === edge.source) continue;
    if (!sourcesByTarget.has(edge.target)) sourcesByTarget.set(edge.target, new Set());
    sourcesByTarget.get(edge.target).add(edge.source);
  }
  return sourcesByTarget;
}

/**
 * Делит упорядоченный ранг на строки. Цели dispatches_to одного интерфейса получают
 * общую вертикальную колонку без переноса; обычные вызовы сохраняют заданное число
 * колонок. Общий метод рисуется один раз, принадлежит первой группе по ID источника.
 * Скрытые цели и одиночные кандидаты не создают группы. Результат содержит исходные
 * элементы; связи, ранги и входные коллекции не изменяются и не задают порядок исполнения.
 */
export function implementationComparisonRows(layer, sourcesByTarget, columns = 1) {
  const groups = new Map();
  for (const node of layer) {
    for (const source of sourcesByTarget.get(node.id) || []) {
      if (!groups.has(source)) groups.set(source, new Set());
      groups.get(source).add(node.id);
    }
  }
  const membership = new Map(), candidates = new Map();
  for (const source of [...groups.keys()].sort(compareIds)) {
    const available = [];
    for (const id of groups.get(source)) if (!membership.has(id)) available.push(id);
    if (available.length < 2) continue;
    for (const id of available) membership.set(id, source);
    candidates.set(source, []);
  }
  // Порядок внутри сравнения наследуется от ранга, а не от порядка рёбер.
  for (const node of layer) {
    const source = membership.get(node.id);
    if (source !== undefined) candidates.get(source).push(node);
  }
  const rows = [], emitted = new Set();
  let ordinary = [];
  for (const node of layer) {
    const source = membership.get(node.id);
    if (source === undefined) {
      ordinary.push(node);
      if (ordinary.length === columns) { rows.push(ordinary); ordinary = []; }
      continue;
    }
    if (!emitted.has(source)) {
      if (ordinary.length) { rows.push(ordinary); ordinary = []; }
      for (const candidate of candidates.get(source)) rows.push([candidate]);
      emitted.add(source);
    }
  }
  if (ordinary.length) rows.push(ordinary);
  return rows;
}
