import {architectureBlocks} from './architecture-blocks.js';
/**
 * Ограниченное состояние обхода; модель не читает DOM, исходники или storage.
 */
export const ARCHITECTURE_LIMITS = Object.freeze({contexts: 4, visits: 64, pins: 24, nodes: 240, edges: 1500});
const ownerNode = node => node.kind === 'project' || node.kind === 'code-scope';

/**
 * Версии связанных снимков отделены от набора выбранных репозиториев.
 */
function revisionKey(data) {
  return JSON.stringify((data.repositories || []).map(item => [item.id || item.projectId, item.snapshotId]).sort());
}

/**
 * Одна scope-bound сессия с явными лимитами посещений, закреплений и геометрии.
 */
export class ArchitectureSession {
  constructor() {
    this.visited = new Set(); this.revealed = new Set(); this.pinned = new Set();
    this.expandedBlocks = new Set(); this.layouts = new Map(); this.cameras = new Map(); this.data = null;
    this.visitRevision = 0; this.loadedRevision = -1; this.limitMessage = '';
  }
  /**
   * Добавляет raw ID один раз; превышение не вытесняет уже выбранные узлы.
   */
  visit(id) {
    if (typeof id !== 'string' || !id || id.length > 4096 || this.visited.has(id)) return false;
    if (this.visited.size >= ARCHITECTURE_LIMITS.visits) { this.limitMessage = 'Достигнут лимит 64 посещений. Сбросьте обход для нового исследования.'; return false; }
    this.visited.add(id); this.visitRevision++; return true;
  }
  /**
   * Принимает актуальную canonical модель; исчезнувшие ID не остаются закреплёнными.
   * @param {object} data Ответ API с nodes/edges и версиями related snapshots.
   * @param {number} revision Версия посещений на момент запроса, для последующей проверки догрузки.
   * @throws {Error} Некорректные nodes/edges отклоняются до изменения сессии.
   * @returns {void} Related revision очищает раскрытие/геометрию, сохраняя primary visits.
   */
  accept(data, revision = this.visitRevision) {
    if (!Array.isArray(data?.nodes) || !Array.isArray(data?.edges)
        || data.nodes.some(node => !node || typeof node.id !== 'string')
        || data.edges.some(edge => !edge || typeof edge.source !== 'string' || typeof edge.target !== 'string')) {
      throw new Error('Сервер вернул некорректную архитектурную модель. Повторите запрос.');
    }
    const signature = revisionKey(data);
    if (this.data && signature !== this.relatedRevision) this.clearExploration(false);
    this.relatedRevision = signature; this.data = data; this.loadedRevision = revision;
    const valid = this.canonicalIds();
    for (const id of this.revealed) if (!valid.has(id)) this.revealed.delete(id);
    for (const id of this.pinned) if (!valid.has(id)) this.pinned.delete(id);
    for (const id of this.expandedBlocks) if (!valid.has(id)) this.expandedBlocks.delete(id);
    for (const registry of this.layouts.values()) for (const id of registry.keys()) if (!valid.has(id)) registry.delete(id);
    for (const node of data.nodes) if (ownerNode(node) || node.resourceKind) {
      this.revealed.add(node.id);
      if (node.ownerId) this.revealed.add(node.ownerId);
    }
  }
  /**
   * Синтетические repository IDs также принадлежат текущей проекции canonical модели.
   */
  canonicalIds() {
    const ids = new Set((this.data?.nodes || []).map(node => node.id));
    for (const node of this.data?.nodes || []) if (node.kind === 'project' && node.repositoryId) ids.add('solution:' + node.repositoryId);
    for (const id of architectureBlocks(this.data || {nodes: [], edges: []}).blocks.keys()) ids.add(id);
    return ids;
  }
  /**
   * Раскрывает только прямые соседи выбранных владельцев, без транзитивного разрастания.
   */
  revealOwners(ids) {
    const owners = new Set(ids); let changed = false;
    for (const edge of this.data?.edges || []) if (owners.has(edge.source) || owners.has(edge.target)) {
      for (const id of [edge.source, edge.target]) if (!this.revealed.has(id)) { this.revealed.add(id); changed = true; }
    }
    return changed;
  }
  /**
   * Переключает допустимое закрепление; превышение сохраняет прежние pins.
   */
  togglePin(id) {
    if (!this.canonicalIds().has(id)) return false;
    if (this.pinned.delete(id)) return true;
    if (this.pinned.size >= ARCHITECTURE_LIMITS.pins) { this.limitMessage = 'Достигнут лимит 24 закреплений.'; return false; }
    this.pinned.add(id); this.revealed.add(id); return true;
  }
  /**
   * Возвращает факты раскрытой области; конфигурационные альтернативы не превращаются в новые рёбра.
   */
  graph() {
    const data = this.data || {nodes: [], edges: []};
    const nodes = data.nodes.filter(node => this.revealed.has(node.id));
    const ids = new Set(nodes.map(node => node.id));
    return {...data, nodes, edges: data.edges.filter(edge => ids.has(edge.source) && ids.has(edge.target))};
  }
  /**
   * Две шкалы и два renderer имеют отдельные ограниченные registries геометрии.
   */
  layout(key) {
    if (!this.layouts.has(key)) this.layouts.set(key, new Map());
    const layout = this.layouts.get(key); this.layouts.delete(key); this.layouts.set(key, layout);
    while (this.layouts.size > 4) { const first = this.layouts.keys().next().value; this.layouts.delete(first); this.cameras.delete(first); }
    return layout;
  }
  /**
   * Явный сброс очищает обход/закрепления; смена related revision сохраняет валидные primary visits.
   */
  clearExploration(clearVisits = true) {
    if (clearVisits) { this.visited.clear(); this.visitRevision++; }
    this.revealed.clear(); this.pinned.clear(); this.expandedBlocks.clear(); this.layouts.clear(); this.cameras.clear(); this.limitMessage = '';
    if (this.data) for (const node of this.data.nodes) if (ownerNode(node)) this.revealed.add(node.id);
  }
}

/**
 * LRU не более четырёх контекстов project/snapshot/related scope.
 */
export class ArchitectureSessions {
  constructor() { this.entries = new Map(); }
  get(key) {
    const entry = this.entries.get(key) || new ArchitectureSession();
    this.entries.delete(key); this.entries.set(key, entry);
    while (this.entries.size > ARCHITECTURE_LIMITS.contexts) this.entries.delete(this.entries.keys().next().value);
    return entry;
  }
  invalidate(key) { this.entries.delete(key); }
}

/**
 * Ограничивает filtered область до пагинации; повторяет pins и repository anchors на каждой странице.
 * @param {object} projection Проекция с allNodes/allEdges и нулевым page.
 * @param {Set<string>} pinned Приоритетные ID; для ребёнка сохраняется цепочка logical block → owner.
 * @returns {object} До240 узлов/1500 связей всего, до160 узлов на странице и точные omittedNodes/Edges.
 * Дополнительные владельцы берутся только из входной области: фильтры не расширяются.
 */
export function boundArchitectureProjection(projection, pinned = new Set()) {
  const byId = new Map(projection.allNodes.map(node => [node.id, node]));
  const pinnedAnchors = new Set(projection.allNodes.filter(node => pinned.has(node.id)).map(node => node.id));
  // A pinned child retains its logical block and owner, without arbitrary transitive closure.
  for (let level = 0; level < 2; level++) for (const edge of projection.allEdges) {
    if (pinnedAnchors.has(edge.target) && (byId.get(edge.source)?.resourceBlock || ownerNode(byId.get(edge.source) || {}))) pinnedAnchors.add(edge.source);
    if (pinnedAnchors.has(edge.source) && ownerNode(byId.get(edge.target) || {})) pinnedAnchors.add(edge.target);
  }
  const anchors = new Set([...pinnedAnchors, ...projection.allNodes.filter(node => node.solution || ownerNode(node)).map(node => node.id)]);
  const priority = node => pinned.has(node.id) ? 3 : pinnedAnchors.has(node.id) ? 2 : anchors.has(node.id) ? 1 : 0;
  const ordered = [...projection.allNodes].sort((a, b) => priority(b) - priority(a));
  const keep = new Set(ordered.slice(0, ARCHITECTURE_LIMITS.nodes).map(node => node.id));
  const allNodes = projection.allNodes.filter(node => keep.has(node.id));
  const eligible = projection.allEdges.filter(edge => keep.has(edge.source) && keep.has(edge.target));
  const allEdges = eligible.slice(0, ARCHITECTURE_LIMITS.edges);
  const persistent = ordered.filter(node => keep.has(node.id) && anchors.has(node.id)).slice(0, 159);
  const persistentIds = new Set(persistent.map(node => node.id));
  const remaining = allNodes.filter(node => !persistentIds.has(node.id));
  const pageSize = 160 - persistent.length;
  const pageCount = Math.max(1, Math.ceil(remaining.length / pageSize));
  const page = Math.min(projection.page, pageCount - 1);
  const nodes = [...persistent, ...remaining.slice(page * pageSize, (page + 1) * pageSize)];
  const visible = new Set(nodes.map(node => node.id));
  return {...projection, allNodes, allEdges, nodes, edges: allEdges.filter(edge => visible.has(edge.source) && visible.has(edge.target)),
    page, pageCount, totalNodes: allNodes.length, totalEdges: allEdges.length,
    omittedNodes: projection.allNodes.length - allNodes.length, omittedEdges: projection.allEdges.length - allEdges.length};
}

/**
 * Ограничивает canonical экспорт, сохраняя владельца каждого принятого ресурса и валидные концы связей.
 * @param {object} graph Раскрытые canonical nodes/edges, включая закрытые в проекции группы.
 * @param {Set<string>} pinned Приоритетные владельцы, ресурсы или логические блоки.
 * @returns {object} До240 узлов/1500 связей, sceneLimits с потерями и coverage.limited/complete при усечении.
 * Отсутствующие во входе владельцы не создаются; их ресурсы пропускаются с учётом в omittedNodes.
 */
export function boundCanonicalArchitecture(graph, pinned = new Set()) {
  const selectedResource = node => pinned.has(node.id) || pinned.has('architecture-block:' + encodeURIComponent(node.ownerId) + ':' + node.kind);
  const requiredOwners = new Set(graph.nodes.filter(selectedResource).map(node => node.ownerId).filter(Boolean));
  const priority = node => ownerNode(node) && (requiredOwners.has(node.id) || pinned.has(node.id)) ? 6
    : selectedResource(node) ? 5 : ownerNode(node) ? 4 : 0;
  const ordered = [...graph.nodes].sort((a, b) => priority(b) - priority(a));
  const nodes = [], ids = new Set();
  for (const node of ordered) {
    if (nodes.length >= ARCHITECTURE_LIMITS.nodes) break;
    if (node.ownerId && !ids.has(node.ownerId)) continue;
    nodes.push(node); ids.add(node.id);
  }
  const edges = graph.edges.filter(edge => ids.has(edge.source) && ids.has(edge.target)).slice(0, ARCHITECTURE_LIMITS.edges);
  const limited = nodes.length < graph.nodes.length || edges.length < graph.edges.length;
  return {...graph, nodes, edges, coverage: {...graph.coverage, limited: Boolean(graph.coverage?.limited || limited),
      ...(limited ? {complete: false} : {})},
    sceneLimits: {omittedNodes: graph.nodes.length - nodes.length, omittedEdges: graph.edges.length - edges.length}};
}
