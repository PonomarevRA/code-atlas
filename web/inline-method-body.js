import { buildProgressiveMethodScene } from './progressive-method-scene.js';
import { pathStubKey } from './path-control-tree-layout.js';
import { buildPathScene } from './path-scene.js';

const MAX_CACHE = 32;
const MAX_CALLS = 40;
const MAX_BODY_CARDS = 160;
const graphIndexes = new WeakMap();

/** Индекс чтения данных строится один раз для неизменяемого объекта графа, а не для каждого тела/redraw. */
function sourceIndex(graph) {
  if (graphIndexes.has(graph)) return graphIndexes.get(graph);
  const nodes = new Map((graph.nodes || []).map(node => [node.id, node])), reads = new Map();
  for (const edge of graph.edges || []) {
    if (!['reads', 'writes', 'initializes_from'].includes(edge.relation)) continue;
    if (!reads.has(edge.source)) reads.set(edge.source, []);
    reads.get(edge.source).push(edge);
  }
  const index = { nodes, reads }; graphIndexes.set(graph, index); return index;
}

/** Описания загружаются только для открытых тел; кэш ограничен и принадлежит снимку. */
export class InlineMethodFacts {
  constructor({ context, changed, fetcher = (...args) => fetch(...args) }) {
    this.context = context; this.changed = changed; this.fetcher = fetcher;
    this.entries = new Map(); this.key = null; this.generation = 0; this.revision = 0;
  }
  /** Новый проект/снимок отменяет старые запросы, включая fetchers без AbortSignal. */
  sync() {
    const context = this.context();
    const key = JSON.stringify([context.projectId, context.snapshotId]);
    if (key !== this.key) { this.clear(); this.key = key; }
    return context;
  }
  /** Возвращает текущее состояние без повторного запроса при каждом redraw. */
  get(id) { this.sync(); return this.entries.get(id) || (this.entries.size >= MAX_CACHE ? { status: 'error', limited: true, message: 'Лимит 32 открытых тел. Сверните детали или откройте полную цепочку стрелкой.' } : undefined); }
  /** Один запрос на тело; ошибка остаётся видимой до явного повторного раскрытия. */
  load(id) {
    const context = this.sync();
    if (!context.projectId || !context.snapshotId || this.entries.has(id)) return;
    if (this.entries.size >= MAX_CACHE) return;
    const controller = new AbortController(), generation = this.generation;
    const entry = { status: 'loading', controller };
    this.entries.set(id, entry); this.revision++;
    this.fetcher(`/api/projects/${encodeURIComponent(context.projectId)}/node-description`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
      body: JSON.stringify({ nodeId: id, snapshotId: context.snapshotId }),
    }).then(async response => {
      const data = await response.json();
      this.sync();
      if (generation !== this.generation || this.entries.get(id) !== entry) return;
      if (!response.ok) throw new Error(data.error || `Ошибка описания: ${response.status}`);
      if (data.nodeId !== id || data.snapshotId !== context.snapshotId) throw new Error('Описание относится к другому снимку.');
      Object.assign(entry, { status: 'ready', data }); this.revision++; this.changed(id);
    }).catch(error => {
      if (generation !== this.generation || this.entries.get(id) !== entry || error.name === 'AbortError') return;
      Object.assign(entry, { status: 'error', message: error.message }); this.revision++; this.changed(id);
    });
  }
  /** Повторный плюс после ошибки разрешает один новый запрос. */
  retry(id) { if (this.get(id)?.status === 'error') this.entries.delete(id); }
  /** Скрытые тела освобождают слоты; сохранённые раскрытия загрузятся при возврате. */
  retain(ids) {
    let changed = false;
    for (const [id, entry] of this.entries) if (!ids.has(id)) { entry.controller.abort(); this.entries.delete(id); changed = true; }
    if (changed) this.revision++;
    return changed;
  }
  /** Освобождает память и отменяет только собственные запросы. */
  clear() {
    for (const entry of this.entries.values()) entry.controller.abort();
    this.entries.clear(); this.generation++; this.revision++;
  }
}

/** Та же сцена пути строит локальное тело; derived IDs никогда не отправляются как raw-символы. */
export function inlineMethodBody(node, entry, graph, { natural = false, expandedIds = new Set(), getFacts = () => undefined, loadFacts = () => {}, placements = new Map(), visibleRegionIds = new Set(placements.keys()) } = {}) {
  if (entry?.data?.methodFlow?.status === 'parsed') {
    const scene = buildProgressiveMethodScene(graph, node.id, { rootData: entry.data, expandedIds, placements: new Map(), visibleRegionIds,
      getData: id => id === node.id ? entry.data : getFacts(id)?.data, getStatus: id => id === node.id ? entry : getFacts(id), loadData: loadFacts });
    // The outer raw header stays intact; the disclosed method has its own derived entry card.
    const entryId = node.id + '::body-entry', header = scene.byId.get(node.id);
    header.id = entryId; scene.rootId = entryId;
    scene.baselineIds.delete(node.id); scene.baselineIds.add(entryId);
    scene.byId.delete(node.id); scene.byId.set(entryId, header);
    for (const card of scene.visuals) if (card.ownerId === node.id) card.ownerId = entryId;
    for (const frame of scene.layout.frames) if (frame.id === node.id) frame.id = entryId;
    for (const edge of scene.visualEdges) {
      if (edge.source === node.id) edge.source = entryId;
      if (edge.target === node.id) edge.target = entryId;
    }
    scene.visualEdges.push({ source: node.id, target: entryId, caption: 'Тело метода', sequenceKind: 'disclosure', relation: 'source-body' });
    scene.content = scene.visuals;
    scene.detached = true; scene.inlineSimple = false; scene.placements = placements;
    scene.visibleRegionIds = visibleRegionIds;
    scene.detailBounds = { ...scene.bounds };
    scene.bounds = { x: 0, y: 0, width: 0, height: 0 };
    return scene;
  }
  const nodes = [], edges = [];
  const { nodes: byId, reads: readEdges } = sourceIndex(graph);
  const pending = [{ raw: node, id: node.id, entry, ancestors: new Set([node.id]), depth: 0 }];
  /** Каждое раскрытое вхождение сохраняет владельца/строку; циклы и общий callee не создают бесконечный обход. */
  for (let index = 0; index < pending.length && index < 8; index++) {
    const current = pending[index], description = scopeDescription(current.entry?.data, current.id);
    const owner = { ...current.raw, ...description, id: current.id, label: current.raw.label,
      sourceOwnerId: current.raw.id, staticData: current.entry?.limited ? undefined : (current.raw.staticData || current.raw.dataFacts), events: description?.events || [] };
    if (current.id !== node.id) {
      const position = nodes.findIndex(item => item.id === current.id);
      Object.assign(nodes[position], owner, { bodyExpanded: true });
    } else nodes.push(owner);
    const calls = (description?.events || []).filter(event => ['call', 'log', 'span'].includes(event.kind));
    for (const event of calls.slice(0, MAX_CALLS)) {
      const id = `${current.id}::inline-call:${event.line}:${event.column}`;
      const target = event.targetNodeIds?.length === 1 ? byId.get(event.targetNodeIds[0]) : null;
      const canExpand = Boolean(target && !current.ancestors.has(target.id) && current.depth < 3 && pending.length < 8);
      const expression = event.callKind === 'constructor-initializer' ? event.text :
        `${event.callee || event.kind}(${(event.arguments || []).map(argument => argument.expression || argument.text || '').join(', ')})`;
      nodes.push({ id, label: event.callKind === 'constructor-initializer' ? 'Вызов конструктора base/this' : event.text,
        kind: 'source-call', ownerId: current.id, sourceOwnerId: current.raw.id, sourceLine: event.line,
        sourceExpression: expression, targetNodeIds: event.targetNodeIds || [], codeProjectId: node.codeProjectId,
        readTargetId: target?.id, canExpand, bodyExpanded: false });
      edges.push({ source: current.id, target: id, branchIds: event.branchIds || [], caption: event.callKind === 'constructor-initializer' ? 'при создании объекта' : event.deferred ? 'отложенный вызов в lambda' : 'вызов из тела', confidence: event.confidence });
      if (canExpand && expandedIds.has(id)) {
        loadFacts(target.id);
        pending.push({ raw: target, id, entry: getFacts(target.id), ancestors: new Set([...current.ancestors, target.id]), depth: current.depth + 1 });
      }
    }
    const shownFields = new Set();
    for (const edge of current.entry?.limited ? [] : readEdges.get(current.raw.id) || []) {
      const field = byId.get(edge.target), data = field?.staticData || field?.dataFacts;
      if (!field?.fieldFacts && !data) continue;
      const id = `${current.id}::inline-data:${field.id}`;
      if (!shownFields.has(id)) {
        shownFields.add(id);
        nodes.push({ ...field, id, sourceOwnerId: field.id, sourceLine: field.line, events: [], controlFlow: undefined,
          staticData: data?.kind === 'constant' ? undefined : data, dataFacts: undefined,
          sourceExpression: data?.initializer || field.fieldFacts?.type || '',
          targetNodeIds: [field.id], dataField: true });
      }
      const caption = { reads: 'читает поле', writes: 'записывает поле', initializes_from: 'инициализируется из' }[edge.relation];
      edges.push({ source: current.id, target: id, caption, relation: edge.relation });
    }
    if (!description || calls.length > MAX_CALLS || description.callableFacts?.controlFlowSupported === false || ['unresolved', 'unavailable'].includes(description.sourceStatus)) {
      const label = current.entry?.limited ? 'Детали ограничены' : current.entry?.status === 'error' ? 'Не удалось прочитать тело' : !description ? 'Читаем тело…' : 'Детали ограничены';
      nodes.push({ id: `${current.id}::inline-status`, label, kind: 'source-status', sourceOwnerId: current.raw.id,
        sourceExpression: current.entry?.message || description?.unresolved?.join(' ') || 'Подробности в исходнике', codeProjectId: node.codeProjectId });
      edges.push({ source: current.id, target: nodes.at(-1).id, caption: 'исходник' });
    }
  }
  const scene = buildPathScene({ nodes, edges, focusId: node.id, stats: new Map() }, { flowPath: true, flatFlow: true, reading: true, natural, selected: node.id });
  for (const stub of scene.stubs) {
    const point = scene.layout.stubPositions?.get(pathStubKey(stub));
    if (!point) continue;
    const id = `${stub.source}::inline-stub:${stub.armId}`;
    const stubNode = { id, label: stub.caption, condition: 'Продолжение / подробности в исходнике',
      ownerId: scene.byId.get(stub.source)?.ownerId, exit: true };
    scene.byId.set(id, stubNode);
    scene.layout.placed.push({ ...stubNode, ...point });
    scene.visualEdges.push({ source: stub.source, target: id, caption: stub.caption });
  }
  let content = scene.layout.placed.filter(item => item.id !== node.id);
  if (content.length > MAX_BODY_CARDS) {
    const omitted = content.length - MAX_BODY_CARDS + 1;
    const omittedPosition = content[MAX_BODY_CARDS - 1];
    content = content.slice(0, MAX_BODY_CARDS - 1);
    const notice = { ...omittedPosition, canExpand: false, bodyExpanded: false, id: `${node.id}::body-limit`, label: 'Детали ограничены', condition: `Скрыто ${omitted} карточек. Полная цепочка доступна стрелкой метода.`, sourceOwnerId: node.id };
    content.push(notice); scene.byId.set(notice.id, notice);
  }
  const left = content.length ? Math.min(...content.map(item => item.x - item.w / 2)) : 0;
  const top = content.length ? Math.min(...content.map(item => item.y - item.h / 2)) : 0;
  const right = content.length ? Math.max(...content.map(item => item.x + item.w / 2)) : 0;
  const bottom = content.length ? Math.max(...content.map(item => item.y + item.h / 2)) : 0;
  return { ...scene, content, bounds: { x: left, y: top, width: right - left, height: bottom - top } };
}

/** Arm IDs уникальны для вхождения вызова: общее тело в двух местах не смешивает владельцев ветвей. */
function scopeDescription(data, occurrenceId) {
  if (!data) return data;
  const copy = structuredClone(data);
  if (copy.controlFlow?.branches?.length > 20) {
    copy.controlFlow.branches = copy.controlFlow.branches.slice(0, 20); copy.controlFlow.truncated = true;
  }
  const armIds = new Map();
  for (const branch of copy.controlFlow?.branches || []) for (const arm of branch.arms || []) armIds.set(arm.id, `${occurrenceId}::arm:${arm.id}`);
  const scoped = values => (values || []).map(id => armIds.get(id) || id);
  for (const branch of copy.controlFlow?.branches || []) {
    branch.parentArmIds = scoped(branch.parentArmIds);
    for (const arm of branch.arms || []) arm.id = armIds.get(arm.id);
  }
  for (const event of copy.events || []) event.branchIds = scoped(event.branchIds);
  for (const terminal of copy.controlFlow?.terminals || []) terminal.branchIds = scoped(terminal.branchIds);
  return copy;
}
