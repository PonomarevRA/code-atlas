/** Индексирует только нарисованные связи; наведение обновляет прежние и новые инцидентные элементы. */
export function edgeHighlighter(lines, className) {
  const byNode = new Map();
  for (const item of lines) {
    for (const id of new Set([item.source, item.target])) {
      if (!byNode.has(id)) byNode.set(id, []);
      byNode.get(id).push(item.line);
    }
  }
  let active = [];
  return id => {
    for (const line of active) line.classList.toggle(className, false);
    active = byNode.get(id) || [];
    for (const line of active) line.classList.toggle(className, true);
  };
}
