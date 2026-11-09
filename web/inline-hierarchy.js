import { DEPENDENCY_RELATIONS } from './architecture.js';
import { htmlBlock, ordinal } from './code-structure.js';

const STRUCTURAL = new Set(['contains', 'method', 'property', 'field', 'has_attribute', 'has_parameter_attribute']);
const CONTAINER_KINDS = new Set(['project', 'file', 'community']);

/** Нормализует только display budgets; не меняет исходную область rawNodes. */
function budget(value, fallback, maximum, minimum = 0) {
  return Number.isFinite(value) ? Math.max(minimum, Math.min(maximum, Math.trunc(value))) : fallback;
}
/** Стабильный порядок не зависит от порядка узлов/рёбер ответа сервера. */
function ordered(values) {
  const items = [...values];
  if (items.length && items.every(item => htmlBlock(item.node || item))) {
    return items.sort((a, b) => ordinal(a.node || a) - ordinal(b.node || b) || String(a.id).localeCompare(String(b.id), 'en'));
  }
  return items.sort((a, b) => String(a.label || a.id).localeCompare(String(b.label || b.id), 'ru') || String(a.id).localeCompare(String(b.id), 'en'));
}
/** ID файла принадлежит конкретному parent: одинаковые пути разных проектов/групп не совпадают. */
export function inlineFileId(parentId, projectId, file) {
  return JSON.stringify(['inline-file', parentId, projectId ?? null, file ?? null]);
}
/** Исходные structural children; calls/dispatches_to никогда не становятся вложенными карточками. */
function structuralIndex(graph, byId) {
  const outgoing = new Map();
  for (const edge of graph.edges || []) {
    const definesField = edge.relation === 'defines' && byId.get(edge.target)?.fieldFacts;
    if ((!STRUCTURAL.has(edge.relation) && !definesField) || !byId.has(edge.source) || !byId.has(edge.target)) continue;
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, new Set());
    outgoing.get(edge.source).add(edge.target);
  }
  return outgoing;
}
/** Полный filtered structural subtree нужен collapsed контейнеру для проекции рёбер. */
function descendants(id, structural, allowed = null) {
  const seen = new Set([id]), queue = [id];
  for (let index = 0; index < queue.length; index++) {
    for (const child of structural.get(queue[index]) || []) if ((!allowed || allowed.has(child)) && !seen.has(child)) { seen.add(child); queue.push(child); }
  }
  return [...seen];
}
/** Выбирает корни деклараций и по одному представителю disconnected containment-cycle. */
function declarationRoots(memberIds, byId, structural, warn, owner) {
  const eligible = new Set(memberIds.filter(id => !['file', 'project', 'community'].includes(byId.get(id)?.kind) && !fileWrapper(byId.get(id), structural)));
  const incoming = new Set();
  for (const id of eligible) for (const child of structural.get(id) || []) if (eligible.has(child)) incoming.add(child);
  const candidates = ordered([...eligible].map(id => byId.get(id)));
  const result = candidates.filter(node => !incoming.has(node.id));
  const reachable = new Set();
  const cover = id => { for (const child of descendants(id, structural, eligible)) reachable.add(child); };
  for (const node of result) cover(node.id);
  for (const node of candidates) {
    if (reachable.has(node.id)) continue;
    result.push(node); cover(node.id);
    warn('cyclic-root', owner, node.id, 'Цикл вложенности представлен одним корнем; повторные владельцы не дублируются.');
  }
  return ordered(result);
}
/** Graphify AST file wrapper имеет явные file_type/origin/location и точный file label; classname не угадывается. */
function fileWrapper(node, structural) {
  const file = node?.file || node?.source_file;
  if (node?.kind !== 'symbol' || node?._origin !== 'ast' || node?.file_type !== 'code' || !file || !structural.has(node.id)) return false;
  const filename = String(file).replaceAll('\\', '/').split('/').at(-1);
  const label = String(node.label || '');
  return (label === file || label === filename) && /^L1(?:\D|$)/u.test(String(node.source_location || ''));
}
/** Создаёт descriptor raw-карточки с неизменным original ID и оригинальными фактами. */
function rawDescriptor(node, structural, allowed = null) {
  return { id: node.id, rawId: node.id, label: node.label || node.id, kind: 'raw', node,
    memberIds: descendants(node.id, structural, allowed), projectId: node.codeProjectId ?? null, file: node.file ?? node.source_file ?? null };
}
/** Сохраняет отдельное действие enter; выбор карточки/inline expansion не выполняет навигацию. */
function enterOf(item) {
  if (item.kind === 'project') return { kind: 'project', projectId: item.projectId ?? item.node?.targetProjectId ?? item.node?.codeProjectId ?? null };
  if (item.kind === 'file') return { kind: 'file', projectId: item.projectId ?? item.node?.targetProjectId ?? null, file: item.file ?? item.node?.targetFile ?? null };
  if (item.kind === 'community') return { kind: 'community', groupId: item.id };
  return item.rawId ? { kind: 'raw', rawId: item.rawId } : null;
}
/** Непосредственные кандидаты до display limits, общая фильтрация сохраняется во всех слоях. */
function childrenOf(item, byId, structural, warn) {
  if (item.kind === 'project') {
    const groups = new Map();
    for (const id of item.memberIds) {
      const node = byId.get(id);
      const file = node.file ?? node.source_file ?? null;
      const project = node.codeProjectId ?? item.projectId ?? null;
      const identifier = inlineFileId(item.id, project, file);
      if (!groups.has(identifier)) groups.set(identifier, { id: identifier, label: file || 'Без исходного файла', kind: 'file',
        rawId: null, file, projectId: project, memberIds: [], node: { id: identifier, label: file || 'Без исходного файла', file, codeProjectId: project, kind: 'file' } });
      groups.get(identifier).memberIds.push(id);
    }
    return ordered(groups.values());
  }
  if (item.kind === 'file' || item.kind === 'community') {
    const allowed = new Set(item.memberIds);
    return declarationRoots(item.memberIds, byId, structural, warn, item.id).map(node => rawDescriptor(node, structural, allowed));
  }
  const allowed = new Set(item.memberIds);
  return ordered([...(structural.get(item.rawId) || [])].filter(id => allowed.has(id)).map(id => byId.get(id))).map(node => rawDescriptor(node, structural, allowed));
}
/** Actual edges project onto deepest visible owner; candidate and exact relations are never merged into one proof class. */
function projectedEdges(graph, membership, relation, preset, maxEdges) {
  const groups = new Map();
  let sourceEdges = 0;
  for (const edge of graph.edges || []) {
    if (relation && edge.relation !== relation) continue;
    if (preset !== 'all' && !DEPENDENCY_RELATIONS.has(edge.relation)) continue;
    const source = membership.get(edge.source), target = membership.get(edge.target);
    if (source === undefined || target === undefined || source === target) continue;
    sourceEdges++;
    const candidate = edge.relation === 'dispatches_to' || ['INFERRED', 'CANDIDATE', 'LOW'].includes(String(edge.confidence || '').toUpperCase());
    const key = JSON.stringify([source, target, edge.relation, candidate, edge._origin || null, edge.evidence?.kind || null]);
    if (!groups.has(key)) groups.set(key, { id: JSON.stringify(['inline-edge', source, target, edge.relation, candidate, edge._origin || null, edge.evidence?.kind || null]),
      source, target, relation: edge.relation, candidate, confidence: edge.confidence, _origin: edge._origin,
      count: 0, weight: 0, evidence: edge.evidence, samples: [], samplesTruncated: false });
    const group = groups.get(key);
    group.count++; group.weight++;
    if (group.samples.length < 2) group.samples.push(edge);
    else group.samplesTruncated = true;
  }
  const edges = [...groups.values()].sort((a, b) => a.id.localeCompare(b.id, 'en'));
  return { edges: edges.slice(0, maxEdges), total: edges.length, sourceEdges, truncated: edges.length > maxEdges };
}

/** Видимая композиция после поглощения base roots; скрытый root не оставляет видимые сироты своего subtree. */
function visibleItems(rootIds, items) {
  const result = [];
  const visit = id => {
    const item = items.get(id);
    result.push(item);
    for (const child of item.children) visit(child);
  };
  for (const id of rootIds) visit(id);
  return result;
}

/** ID верхней видимой зоны нужен, чтобы collapsed cycle не мог скрыть собственный root под своим потомком. */
function rootZone(item, items) {
  let current = item;
  while (current.parentId !== null) current = items.get(current.parentId);
  return current.id;
}

/**
 * Descendant base cards внутри раскрытой зоны представлены ближайшим видимым
 * владельцем, даже если он collapsed. Например, method-owned attributes скрыты
 * под методом до его раскрытия. Original roots/input не меняются: следующий
 * build после collapse восстанавливает их; calls не участвуют в этом решении.
 */
function consumeHiddenRoots(rootIds, items, warn) {
  const hidden = [];
  for (const id of [...rootIds]) {
    if (!rootIds.includes(id)) continue;
    const root = items.get(id);
    if (!root.rawId) continue;
    const owners = [];
    for (const item of visibleItems(rootIds, items)) {
      if (item.id === id || rootZone(item, items) === id || (!item.expanded && item.parentId === null)) continue;
      if (!item.memberIds.includes(root.rawId)) continue;
      const zone = rootZone(item, items);
      const zoneRoot = items.get(zone);
      if (zoneRoot.rawId && root.memberIds.includes(zoneRoot.rawId)) {
        warn('cyclic-ownership', zone, id, 'Циклические base roots представлены одной зоной, без дублирования.');
        if (id.localeCompare(zone, 'en') < 0) continue;
      }
      owners.push(item);
    }
    owners.sort((a, b) => b.depth - a.depth || a.id.localeCompare(b.id, 'en'));
    if (!owners.length) continue;
    if (owners.length > 1 && owners[0].depth === owners[1].depth) {
      warn('shared-hidden-owner', owners[0].id, id, 'Потомок имеет несколько видимых владельцев; выбран один, карточка не дублируется.');
    }
    rootIds.splice(rootIds.indexOf(id), 1);
    hidden.push({ id, ownerId: owners[0].id });
  }
  return hidden;
}

/**
 * Чистая inline hierarchy текущей filtered области. Не меняет graph, roots или expandedIds.
 * Все base roots сначала резервируются, поэтому новые children не вытесняют соседей.
 * Expanded parent может поглотить base raw root с тем же ID; collapse восстанавливает
 * исходную композицию при следующем вызове. Cycle/shared owner сохраняют один экземпляр.
 */
export function buildInlineHierarchy(graph, { roots = [], rawNodes = graph.nodes || [], expandedIds = new Set(),
  maxItems = 240, maxChildren = 60, maxDepth = 6, relation = '', preset = 'dependencies' } = {}) {
  const limits = { maxItems: budget(maxItems, 240, 1000, 1), maxChildren: budget(maxChildren, 60, 240), maxDepth: budget(maxDepth, 6, 12), maxEdges: 1200 };
  const nodes = rawNodes instanceof Map ? [...rawNodes.values()] : rawNodes;
  const byId = new Map(nodes.filter(node => node?.id != null).map(node => [node.id, node]));
  const structural = structuralIndex(graph, byId);
  const expanded = expandedIds instanceof Set ? expandedIds : new Set(expandedIds || []);
  const warnings = [], warningKeys = new Set();
  const warn = (kind, itemId, childId, message) => {
    const key = JSON.stringify([kind, itemId, childId]);
    if (warningKeys.has(key)) return;
    warningKeys.add(key);
    if (warnings.length < 60) warnings.push({ kind, itemId, childId, message });
  };
  const uniqueRoots = new Map();
  for (const descriptor of roots) if (!uniqueRoots.has(descriptor.id)) uniqueRoots.set(descriptor.id, descriptor);
  const descriptors = ordered(uniqueRoots.values());
  const items = new Map(), rootIds = [], baseIds = new Set();
  let omittedItems = Math.max(0, descriptors.length - limits.maxItems);
  const create = (descriptor, parentId = null, depth = 0) => {
    const rawId = descriptor.kind === 'raw' ? descriptor.rawId ?? descriptor.id : null;
    const node = descriptor.node ?? byId.get(rawId) ?? descriptor;
    const supplied = rawId && parentId === null ? descendants(rawId, structural) : descriptor.memberIds ?? [];
    const memberIds = [...new Set(supplied.filter(id => byId.has(id)))].sort((a, b) => String(a).localeCompare(String(b), 'en'));
    const item = { ...descriptor, rawId, node, label: descriptor.label || node.label || descriptor.id, parentId, depth,
      memberIds, totalMembers: memberIds.length, children: [], totalChildren: 0, visibleChildren: 0,
      expanded: false, childrenTruncated: false, enter: null };
    item.enter = enterOf(item);
    items.set(item.id, item);
    return item;
  };
  for (const root of descriptors.slice(0, limits.maxItems)) { create(root); rootIds.push(root.id); baseIds.add(root.id); }
  const processed = new Set();
  const isAncestor = (ancestorId, item) => {
    let current = item;
    while (current) { if (current.id === ancestorId) return true; current = items.get(current.parentId); }
    return false;
  };
  const moveDepth = (item, depth) => {
    item.depth = depth;
    for (const id of item.children) moveDepth(items.get(id), depth + 1);
  };
  const expand = item => {
    if (processed.has(item.id)) return;
    processed.add(item.id);
    const candidates = childrenOf(item, byId, structural, warn);
    item.totalChildren = candidates.length;
    item.expanded = expanded.has(item.id) && candidates.length > 0;
    if (!item.expanded) return;
    if (item.depth >= limits.maxDepth) { item.childrenTruncated = true; omittedItems += candidates.length; return; }
    for (const candidate of candidates.slice(0, limits.maxChildren)) {
      if (isAncestor(candidate.id, item)) { warn('cycle', item.id, candidate.id, 'Цикл не создаёт вторую карточку предка.'); item.childrenTruncated = true; continue; }
      let child = items.get(candidate.id);
      if (child && child.parentId !== item.id) {
        if (child.parentId === null && baseIds.has(child.id)) {
          child.parentId = item.id; moveDepth(child, item.depth + 1);
          const index = rootIds.indexOf(child.id); if (index >= 0) rootIds.splice(index, 1);
        } else {
          warn('shared-parent', item.id, child.id, 'У элемента уже есть видимый владелец; повторная карточка не добавлена.');
          item.childrenTruncated = true; continue;
        }
      }
      if (!child) {
        if (items.size >= limits.maxItems) { item.childrenTruncated = true; omittedItems++; continue; }
        child = create(candidate, item.id, item.depth + 1);
      }
      if (!item.children.includes(child.id)) item.children.push(child.id);
      expand(child);
    }
    item.visibleChildren = item.children.length;
    if (candidates.length > limits.maxChildren) { item.childrenTruncated = true; omittedItems += candidates.length - limits.maxChildren; }
  };
  // Containers/type owners precede members; existing base roots can be reparented without changing their raw identity.
  const firstRoots = [...rootIds];
  const consumption = new Map();
  for (const id of firstRoots) {
    const item = items.get(id), members = new Set(item.memberIds);
    consumption.set(id, expanded.has(id) ? firstRoots.filter(root => root !== id && members.has(items.get(root).rawId)).length : 0);
  }
  const first = [...rootIds].sort((a, b) => {
    const left = items.get(a), right = items.get(b);
    const aOwner = CONTAINER_KINDS.has(left.kind) || structural.has(left.rawId), bOwner = CONTAINER_KINDS.has(right.kind) || structural.has(right.rawId);
    return consumption.get(b) - consumption.get(a) || Number(bOwner) - Number(aOwner) || a.localeCompare(b, 'en');
  });
  for (const id of first) expand(items.get(id));
  const hiddenRoots = consumeHiddenRoots(rootIds, items, warn);
  const flat = visibleItems(rootIds, items);
  const membership = new Map();
  for (const item of flat) for (const id of item.memberIds) {
    const previous = items.get(membership.get(id));
    if (!previous || item.depth > previous.depth || item.depth === previous.depth && item.rawId === id && previous.rawId !== id) membership.set(id, item.id);
  }
  // An actual visible raw card is more specific than another container's collapsed membership.
  for (const item of flat) if (item.rawId && byId.has(item.rawId)) membership.set(item.rawId, item.id);
  const projected = projectedEdges(graph, membership, relation, preset, limits.maxEdges);
  return { schemaVersion: 1, items: flat, roots: rootIds, hiddenRoots, edges: projected.edges, membership, warnings, limits,
    truncated: omittedItems > 0 || projected.truncated || flat.some(item => item.childrenTruncated),
    counts: { baseRoots: descriptors.length, visibleRoots: rootIds.length, visibleItems: flat.length, rawNodes: byId.size,
      totalProjectedEdges: projected.total, visibleEdges: projected.edges.length, sourceEdges: projected.sourceEdges, omittedItems,
      hiddenBaseRoots: hiddenRoots.length } };
}
