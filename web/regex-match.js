/** Ищет по имени узла без изменения набора узлов; запускать в Worker для недоверенных шаблонов. */
export function matchNodeLabels(nodes, pattern) {
  if (!pattern) return [];
  const regex = new RegExp(pattern, 'iu');
  const matches = [];
  for (const node of nodes) if (regex.test(String(node.label || ''))) matches.push(node.id);
  return matches;
}
