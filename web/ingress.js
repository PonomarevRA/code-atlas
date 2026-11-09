/** Отбирает входы для текущей области, не добавляя их в граф символов или его счётчики. */
export function ingressOverlay(entries, architecture, { level = 'projects', projectId = null, file = null, kind = '', page = 0, pageSize = 6 } = {}) {
  const visible = new Set(level === 'symbols' ? architecture.nodes.map(node => node.id) : architecture.groups.map(group => group.id));
  const visibleProjects = new Set(architecture.groups.map(group => group.targetProjectId));
  const scoped = [];
  for (const entry of entries) {
    if (kind && entry.kind !== kind) continue;
    const targets = new Set();
    for (const handler of entry.handlerNodeIds || []) {
      const target = architecture.membership.get(handler);
      if (target !== undefined && visible.has(target)) targets.add(target);
    }
    // Несопоставленный вход можно изучить в списке, но ему не рисуется выдуманная стрелка.
    const unresolvedInScope = !(entry.handlerNodeIds || []).length && (level === 'projects' ? visibleProjects.has(entry.codeProjectId) : entry.codeProjectId === projectId && (level !== 'symbols' || (entry.evidence || []).some(item => item.file === file)));
    if (targets.size || unresolvedInScope) scoped.push({ ...entry, targets: [...targets] });
  }
  scoped.sort((a, b) => String(a.label).localeCompare(String(b.label), 'ru') || String(a.id).localeCompare(String(b.id), 'en'));
  const size = Math.max(1, Math.min(6, Math.trunc(pageSize) || 6));
  const pageCount = Math.max(1, Math.ceil(scoped.length / size));
  const currentPage = Math.max(0, Math.min(pageCount - 1, Math.trunc(page) || 0));
  return { entries: scoped.slice(currentPage * size, (currentPage + 1) * size), total: scoped.length, page: currentPage, pageCount };
}
/** Краткая подпись сохраняет метод и отличающийся хвост маршрута/топика; полное значение доступно в деталях. */
export function ingressLabel(entry) {
  const value = String(entry.route || entry.topic || entry.queue || entry.label);
  const tail = entry.kind === 'http' ? value.split('/').filter(Boolean).slice(-2).join('/') : value;
  const shortened = tail.length > 27 ? `…${tail.slice(-26)}` : tail;
  return entry.kind === 'http' ? `${entry.httpMethod || 'HTTP'} ${value !== tail ? '…/' : ''}${shortened}` : shortened;
}

/** Unknown entry kinds keep their name instead of masquerading as a message broker. */
export function ingressKindLabel(entry) {
  return { http: 'HTTP API', mcp: 'MCP', rabbit: 'RabbitMQ', kafka: 'Kafka', test: 'Тест-кейс' }[entry.kind] || entry.kind || 'Точка входа';
}
