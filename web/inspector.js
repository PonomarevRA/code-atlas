import { $, el } from './dom.js';
import { presentRelation } from './relation-presentation.js';
import { renderHtmlFacts } from './html-facts.js';
/** Показывает контекст символа и два направления связей, используя готовый индекс инцидентности. */
export function renderInspector(state, model, nodes, relation, onSelect, extras = {}) {
  const id = state.selected;
  const node = model.nodes.get(id);
  if (!node) return;
  const panel = $('selection');
  const owner = (state.graph.codeProjects || []).find(item => String(item.id) === String(node.codeProjectId));
  panel.replaceChildren(el('h2', node.label || id), el('span', node.kind || 'Узел', 'pill'));
  panel.append(el('p', owner?.label || 'Без установленного проекта', 'small muted'));
  panel.append(el('code', `${node.file || 'Исходный файл не указан'}${node.line ? `:${node.line}` : ''}`, 'source'));
  if (node.communityAssociation === 'inferred') panel.append(el('p', node.communityAssociationAmbiguous ? 'Группа предположена по связям; есть несколько вариантов. Владение файлом из этого не следует.' : 'Группа предположена по связям. Владение файлом из этого не следует.', 'small muted'));
  if (node.description) panel.append(el('p', node.description));
  renderHtmlFacts(panel, node, onSelect, {projectId: state.project?.id, snapshotId: state.graph?.metadata?.snapshotId,
    nodeLabel: nodeId => model.nodes.get(nodeId)?.label});
  if (state.comparison?.changedNodeIds.includes(id)) panel.append(el('p', 'Узел относится к изменённому файлу; изменение символа не определялось.', 'change-note'));
  const incident = model.incident(id, nodes, relation);
  const incoming = [];
  const outgoing = [];
  for (const edge of incident) {
    if (edge.target === id) incoming.push(edge);
    if (edge.source === id) outgoing.push(edge);
  }
  const entries = extras.entries || [];
  if (entries.length) {
    panel.append(el('h3', 'Точка входа'));
    panel.append(el('p', 'Выберите вход, чтобы схема пути началась с маршрута, а не только с метода.', 'small muted'));
    for (const entry of entries) {
      const button = el('button', entry.label || entry.route || entry.id);
      button.type = 'button';
      button.addEventListener('click', () => extras.onEntryPath?.(entry));
      panel.append(button);
    }
  }
  for (const [title, edges, direction] of [['Входящие связи', incoming, 'incoming'], ['Исходящие связи', outgoing, 'outgoing']]) {
    panel.append(el('h3', title), el('p', `${edges.length} связей${edges.length > 50 ? ' · показаны 50: сначала вызовы и реализации' : ''}`, 'small muted'));
    const list = el('div', undefined, 'connections');
    const buckets = new Map();
    for (const edge of edges) {
      const presentation = presentRelation(edge);
      if (!buckets.has(presentation.group)) buckets.set(presentation.group, []);
      buckets.get(presentation.group).push({ edge, presentation });
    }
    let remaining = 50;
    for (const [category, name] of [['calls', 'Вызовы и реализации'], ['ownership', 'Структура и владение'], ['dependencies', 'Зависимости и ссылки'], ['attributes', 'Атрибуты']]) {
      const items = buckets.get(category);
      if (!items?.length || !remaining) continue;
      list.append(el('h4', name));
      for (const { edge, presentation } of items.slice(0, remaining)) {
        remaining--;
        const neighborId = direction === 'incoming' ? edge.source : edge.target;
        const neighbor = model.nodes.get(neighborId);
        const button = el('button', `${direction === 'incoming' ? '←' : '→'} ${presentation.label} · ${neighbor?.label || neighborId}`, 'connection');
        button.type = 'button';
        button.append(el('small', `${presentation.raw}${presentation.qualifier ? ` · ${presentation.qualifier}` : ''}${presentation.location ? ` · ${presentation.location}` : ''}`));
        button.title = neighbor?.file || 'Файл не указан';
        button.addEventListener('click', () => onSelect(neighborId));
        list.append(button);
      }
    }
    panel.append(list);
  }
}
