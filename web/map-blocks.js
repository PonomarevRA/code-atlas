import { revealProgressiveDetails } from './progressive-navigation.js';
import { $, el } from './dom.js';
import { codeProjectId } from './filters.js';
import { buildInlineHierarchy } from './inline-hierarchy.js';
import { inlineFlowLayout } from './inline-flow-layout.js';
import { inlineBaseBounds } from './inline-space.js';
import { InlineHierarchyRenderer } from './inline-hierarchy-renderer.js';
import { capturePathTransition, animatePathTransition } from './path-transition.js';
import { InlineMethodFacts, inlineMethodBody } from './inline-method-body.js';
import { appendNodeActions } from './node-actions.js';
import { renderHtmlPreview } from './html-preview.js';

/** AST callable_class описывает тип, а точка входа пути должна быть реальным методом. */
export function executableBlock(node) {
  if (!node || node._callable_class || ['attribute', 'attribute_scope', 'file', 'project', 'note'].includes(node.kind)) return false;
  return node._callable === true || ['method', 'function', 'constructor', 'local-function'].includes(node.kind)
    || node.kind === 'symbol' && /\([^)]*\)/.test(String(node.label || ''));
}

/** Класс открывает альтернативные начала методов, статическое поле — его данные и фабрику. */
export function implementationBlock(node) {
  return executableBlock(node) || Boolean(node?._callable_class || node?.typeFacts || node?.staticData || node?.dataFacts || node?.fieldFacts)
    || ['class', 'interface', 'struct', 'record'].includes(node?.kind);
}

/** Полное членство typed-блока вычисляется до display limits; synthetic ID не является символом. */
export function blockMembers(block, nodes, communityId) {
  if (!block) return [];
  if (block.kind === 'raw') return nodes.filter(node => node.id === block.rawId);
  if (block.kind === 'project') return nodes.filter(node => codeProjectId(node) === String(block.projectId));
  if (block.kind === 'file') return nodes.filter(node => codeProjectId(node) === String(block.projectId) && (node.file || null) === (block.file || null));
  if (block.groupId) return nodes.filter(node => String(node.community ?? 'ungrouped') === block.groupId
    && (!block.projectScoped || codeProjectId(node) === String(block.projectId)));
  return nodes.filter(node => communityId(node) === block.id);
}

/** Компактный адрес изоляции: membership заново проверяется по текущему снимку. */
export function blockAddress(item) {
  return { id: item.id, kind: item.kind, projectId: item.projectId ?? null, file: item.file ?? null, rawId: item.rawId ?? null, groupId: item.groupId ?? null, projectScoped: item.projectScoped === true };
}

/** Контроллер разделяет выбор, явный переход и структурное раскрытие, сохраняя камеру. */
export function createMapBlocks({ state, view, filteredNodes, communityId, selectNode, navigate, openCommunity,
  analyzePath, isolate, draw, controls, checkpoint, commit, reveal, fetcher, readMethod, openGroupProject }) {
  let byId = new Map();
  let baseline = new Map(), layoutScope = null;
  let bodyCache = new Map(), bodyCacheKey = null;
  const renderer = new InlineHierarchyRenderer(view);
  const facts = new InlineMethodFacts({ context: () => ({ projectId: state.project?.id, snapshotId: state.graph.metadata?.snapshotId }),
    fetcher, changed: refreshFacts });
  state.inlineExpanded = new Set();
  state.inlineMethodPlacements = new Map();
  let visibleMethodRegions = new Set();
  state.selectedBlock = null;
  state.isolatedBlock = null;

  /** Завершённый запрос сохраняет экранное место раскрытого вхождения, как синхронный плюс. */
  function refreshFacts(rawId) {
    if (state.pathOnGraph) return;
    const item = [...byId.values()].find(item => (item.rawId === rawId || item.rawIds?.includes(rawId)) && state.inlineExpanded.has(item.id));
    if (!item) return;
    const before = capturePathTransition(view), point = before.get(item.id), camera = { ...view.camera };
    draw(false);
    const after = view.positions?.get(item.id);
    const offset = point && after ? { x: point.x - after.x, y: point.y - after.y } : { x: 0, y: 0 };
    view.camera = { ...camera, x: camera.x + offset.x * camera.scale, y: camera.y + offset.y * camera.scale };
    view.transform(); animatePathTransition(view, before, offset);
  }
  /** Descriptors следуют текущей топологии; членство берётся из полной filtered области. */
  function roots(items, overview, nodes) {
    return items.map(node => {
      if (!overview) return { id: node.id, rawId: node.id, kind: 'raw', label: node.label, node, memberIds: [node.id] };
      const kind = node.architectureLevel === 'projects' ? 'project' : node.architectureLevel === 'files' ? 'file' : 'community';
      const block = { id: String(node.id), kind, label: node.label, node, projectId: node.targetProjectId ?? node.codeProjectId,
        file: node.targetFile ?? null, groupId: kind === 'community' ? String(node.originalCommunityId ?? node.id) : null,
        projectScoped: kind === 'community' && node.originalCommunityId != null };
      block.memberIds = blockMembers(block, nodes, communityId).map(item => item.id);
      return block;
    });
  }
  /** Исходные координаты принадлежат области карты; раскрытия и выбор не меняют эту основу. */
  function prepare(items, overview, initialGeometry) {
    if (isSystemMode() || state.pathOnGraph || state.progressiveMap) { view.inlineSceneActive = false; return false; }
    const scope = JSON.stringify([state.graph.metadata?.snapshotId, state.algorithm, view.displayProvider,
      $('view-mode').value, $('relation').value, $('architecture-preset').value,
      $('project-blocks')?.checked, overview, items.map(item => String(item.id)).sort()]);
    if (scope !== layoutScope || state.inlineRestorePlacements) {
      baseline = new Map(); layoutScope = scope;
      state.inlineMethodPlacements = state.inlineRestorePlacements || new Map(); state.inlineRestorePlacements = null;
      visibleMethodRegions = new Set(state.inlineMethodPlacements.keys());
      bodyCache.clear(); bodyCacheKey = null;
    }
    const nodes = filteredNodes(false);
    const scene = buildInlineHierarchy(state.graph, { roots: roots(items, overview, nodes), rawNodes: nodes,
      expandedIds: state.inlineExpanded, maxItems: 320, relation: $('relation').value,
      preset: $('view-mode').value === 'architecture' ? $('architecture-preset').value : 'all' });
    const cacheKey = JSON.stringify([state.graph.metadata?.snapshotId, state.algorithm, facts.revision, [...state.inlineExpanded].sort()]);
    if (cacheKey !== bodyCacheKey) { bodyCache.clear(); bodyCacheKey = cacheKey; }
    scene.methodBodies = new Map();
    let openedBodies = 0;
    // Fetch/body slots follow actual prior visibility, independently of raw source order.
    const bodyOrder = scene.items.slice().sort((a, b) => Number(visibleMethodRegions.has(b.id + '::inline-body'))
      - Number(visibleMethodRegions.has(a.id + '::inline-body')));
    for (const item of bodyOrder) {
      if (!item.rawId || !(executableBlock(item.node) || item.node.staticData || item.node.dataFacts || item.node.fieldFacts) || !state.inlineExpanded.has(item.id)) continue;
      item.expanded = true;
      const allowed = openedBodies++ < 8;
      if (!allowed) { item.bodyLimit = 'Лимит 8 открытых тел: сверните часть деталей.'; continue; }
      facts.load(item.rawId);
      const entry = facts.get(item.rawId);
      if (!bodyCache.has(item.id)) bodyCache.set(item.id, inlineMethodBody(item.node, entry, state.graph, { natural: state.algorithm === 'natural', expandedIds: state.inlineExpanded,
        getFacts: id => facts.get(id), loadFacts: id => facts.load(id), placements: state.inlineMethodPlacements, visibleRegionIds: visibleMethodRegions }));
      bodyCache.get(item.id).visibleRegionIds = visibleMethodRegions;
      scene.methodBodies.set(item.id, bodyCache.get(item.id));
    }
    byId = new Map(scene.items.map(item => [item.id, item]));
    for (const [ownerId, body] of scene.methodBodies) {
      for (const node of body.visuals) if (node.canExpand && node.readTargetId) byId.set(node.id, {
        id: node.id, rawId: node.readTargetId, rawIds: node.readTargetIds || [node.readTargetId], node: state.nodes.get(node.readTargetId),
        label: node.label, expanded: state.inlineExpanded.has(node.id), totalChildren: 0, parentId: ownerId });
    }
    const retained = new Set([...state.inlineExpanded].flatMap(id => byId.get(id)?.rawIds || [byId.get(id)?.rawId]).filter(Boolean));
    facts.retain(retained);
    state.inlineScene = scene;
    view.inlineSceneActive = scene.items.some(item => item.expanded);
    if (!view.inlineSceneActive) { visibleMethodRegions.clear(); return false; }
    // Прямой URL и Back восстанавливают ту же обычную геометрию без промежуточного SVG.
    if (!baseline.size) baseline = inlineBaseBounds(initialGeometry?.());
    const layout = inlineFlowLayout(scene, baseline, { reading: view.codeReading });
    visibleMethodRegions = new Set();
    for (const [id, placement] of layout.methodBodies) if (placement.scene.detached) {
      visibleMethodRegions.add(id + '::inline-body');
      for (const area of placement.scene.expansions) visibleMethodRegions.add(area.bounds.id);
    }
    renderer.render(scene, layout, state.selectedBlock || state.selected);
    return true;
  }
  /** Выбранный агрегат получает реальные callable-входы без передачи synthetic ID в API. */
  function inspect(item) {
    const members = (item.memberIds || []).map(id => state.nodes.get(id)).filter(Boolean);
    const callables = members.filter(executableBlock);
    const heading = el('h2', item.label);
    const summary = item.node?.groupSummary;
    const information = el('p', summary
      ? `${summary.files} файлов · ${item.totalMembers} символов. Кнопка в карточке открывает ${summary.scope === 'project' ? 'группы проекта' : 'символы группы'}. Линии показывают зависимости, не порядок выполнения.`
      : `${item.totalMembers} символов · ${item.totalChildren} внутренних блоков. Круг «+» раскрывает здесь, стрелка открывает отдельный уровень.`, 'muted');
    const button = el('button', 'Изолировать исходящую цепочку');
    button.addEventListener('click', isolate);
    const panel = $('selection'); panel.replaceChildren(heading, information, button);
    if (item.kind === 'file') {
      const html = members.find(node => ['page', 'fragment'].includes(node.htmlFacts?.role));
      renderHtmlPreview(panel, html, {projectId: state.project?.id, snapshotId: state.graph.metadata?.snapshotId});
    }
    if (summary) {
      const open = el('button', summary.scope === 'project' ? 'Открыть группы проекта' : 'Открыть символы группы');
      open.addEventListener('click', () => enter(item.id)); panel.append(open);
    }
    if (!callables.length) { panel.append(el('p', 'В этом блоке нет методов для точки входа.', 'muted')); return; }
    const label = el('label', 'Путь выполнения от метода');
    const search = el('input'); search.type = 'search'; search.placeholder = 'Найти метод в выбранном блоке'; search.setAttribute('aria-label', 'Найти точку входа');
    const choices = el('select'); choices.setAttribute('aria-label', 'Метод — точка входа');
    const count = el('p', '', 'small muted');
    const start = el('button', 'Построить путь');
    const update = () => {
      const query = search.value.toLocaleLowerCase();
      const matches = callables.filter(node => `${node.label} ${node.file}`.toLocaleLowerCase().includes(query));
      choices.replaceChildren();
      for (const node of matches.slice(0, 100)) choices.append(new Option(`${node.label} · ${node.file || ''}`, node.id));
      start.disabled = !matches.length; count.textContent = `${Math.min(100, matches.length)} из ${matches.length} методов · поиск по всем ${callables.length}`;
    };
    search.addEventListener('input', update);
    start.addEventListener('click', () => { if (callables.some(node => node.id === choices.value)) analyzePath(choices.value); });
    label.append(search, choices); panel.append(label, count, start); update();
  }
  /** Body click выбирает, не меняя уровень, раскрытия или camera. */
  function select(id) {
    const item = byId.get(id);
    if (!item) return;
    if (item.rawId) { state.selectedBlock = null; selectNode(item.rawId); return; }
    checkpoint(); reveal(); state.selectedBlock = id; state.selected = null;
    state.answerRevision++; $('answer').replaceChildren(); inspect(item); controls();
    if (view.inlineSceneActive) renderer.setSelected(id);
    else if (view.displayProvider === 'flow') view.setSelected(id);
    else for (const [key, entry] of view.communityElements) { entry.group.classList.toggle('selected', key === id); entry.group.setAttribute('aria-pressed', String(key === id)); }
    commit();
  }
  /** Отдельная стрелка переводит typed-контейнер на уровень ниже либо открывает реализацию raw-метода. */
  function enter(id) {
    const item = byId.get(id), target = item?.enter;
    if (!target) return;
    if (item.node?.groupSummary?.scope === 'project' && openGroupProject) { openGroupProject(String(item.projectId)); return; }
    checkpoint();
    if (target.kind === 'project' || target.kind === 'file') navigate({ level: target.kind === 'project' ? 'files' : 'symbols', projectId: String(target.projectId ?? '__unassigned__'), file: target.file ?? null, page: 0 });
    else if (target.kind === 'community') { reset(); openCommunity(target.groupId); }
    else if (implementationBlock(state.nodes.get(target.rawId))) analyzePath(target.rawId);
    else {
      const node = state.nodes.get(target.rawId);
      if (node?.file) navigate({ level: 'symbols', projectId: codeProjectId(node), file: node.file, page: 0 }, target.rawId);
      selectNode(target.rawId);
    }
    commit();
  }
  /** Toggle сохраняет header и камеру; соседи только раздвигаются по исходным направлениям. */
  function toggle(id) {
    const item = byId.get(id);
    const readable = item?.rawId && (executableBlock(item.node) || item.node.staticData || item.node.dataFacts || item.node.fieldFacts);
    if (!item?.totalChildren && !readable) return;
    if (readable && !state.inlineExpanded.has(id)) facts.retry(item.rawId);
    checkpoint();
    const transition = capturePathTransition(view);
    const point = view.positions?.get(id), camera = { ...view.camera };
    if (!view.inlineSceneActive) baseline = inlineBaseBounds(view.positions);
    const header = baseline.get(id);
    const before = !view.inlineSceneActive && header
      ? { x: header.x + header.width / 2, y: header.y + header.height / 2 } : point;
    const choice = state.inlineScene?.methodBodies && [...state.inlineScene.methodBodies.values()].map(body => body.byId?.get(id)).find(Boolean);
    if (choice?.kind === 'candidate' && !state.inlineExpanded.has(id)) {
      for (const body of state.inlineScene.methodBodies.values()) for (const other of body.visuals) {
        if (other.kind === 'candidate' && other.ownerId === choice.ownerId) state.inlineExpanded.delete(other.id);
      }
    }
    if (state.inlineExpanded.has(id)) state.inlineExpanded.delete(id); else state.inlineExpanded.add(id);
    state.expandedId = null; draw(false);
    const after = view.positions?.get(id);
    if (before && after) { view.camera.x = camera.x + (before.x - after.x) * camera.scale; view.camera.y = camera.y + (before.y - after.y) * camera.scale; }
    view.camera.scale = camera.scale; view.transform();
    animatePathTransition(view, transition, before && after ? { x: before.x - after.x, y: before.y - after.y } : { x: 0, y: 0 });
    revealProgressiveDetails(view, id, true);
    // querySelector escaping differs for JSON aggregate IDs: exact attribute comparison is safe.
    const exact = [...view.viewport.querySelectorAll('[data-node-action="inline"]')].find(node => node.getAttribute('data-owner-id') === id);
    exact?.focus?.({ preventScroll: true });
    if (state.selectedBlock && byId.has(state.selectedBlock)) inspect(byId.get(state.selectedBlock));
    commit();
  }
  /** Декорирует существующие topology/flow cards теми же изолированными actions. */
  function decorate(group, id, bounds) {
    const item = byId.get(id); if (!item) return;
    const annotation = item.node?.kind === 'attribute';
    const summary = item.node?.groupSummary;
    appendNodeActions(group, { id, label: item.label, enter: annotation ? null : () => enter(id),
      enterLabel: summary ? `Открыть ${summary.scope === 'project' ? 'группы проекта' : 'символы группы'}: ${item.label}` : implementationBlock(item.node) ? `Просмотреть реализацию: ${item.label}` : `Войти в блок: ${item.label}`,
      onToggle: !summary && (item.totalChildren || implementationBlock(item.node)) ? () => toggle(id) : null, expanded: item.expanded,
      x: bounds.right - 48, y: bounds.top + 16,
      reading: view.codeReading || annotation || summary ? { left: bounds.left, top: bounds.top, width: bounds.width, height: bounds.height, compact: annotation,
        enterText: summary ? summary.scope === 'project' ? 'Группы →' : 'Символы →' : item.rawId ? 'Схема →' : 'Внутрь →', sourceId: item.rawId,
        source: item.rawId && item.node?.file ? id => view.onPathSource?.(id, item.node.line) : null } : null });
  }
  /** Смена области сбрасывает локальную иерархию; история восстанавливает её отдельно. */
  function reset() { bodyCache.clear(); bodyCacheKey = null; facts.clear(); state.inlineExpanded.clear(); visibleMethodRegions.clear(); state.selectedBlock = null; state.isolatedBlock = null; byId.clear(); baseline.clear(); layoutScope = null; view.inlineSceneActive = false; }
  /** Определяет топологию систем: dependency edges не являются containment. */
  function isSystemMode() { return $('view-mode').value === 'system'; }
  view.prepareInline = prepare; view.decorateNodeActions = decorate;
  view.canReadBlock = item => implementationBlock(item.node);
  view.onReadInlineTarget = id => readMethod ? readMethod({ ...state.nodes.get(id), id, targetIds: [id] }) : analyzePath(id);
  view.onInlineSource = (id, line) => view.onPathSource?.(id, line);
  view.onBlockSelect = select; view.onEnterBlock = enter; view.onToggleBlock = toggle;
  view.inlineHierarchyRenderer = renderer;
  return { select, enter, toggle, reset, selected: () => byId.get(state.selectedBlock),
    restoreSelection: () => { if (byId.has(state.selectedBlock)) select(state.selectedBlock); else state.selectedBlock = null; },
    address: () => { const item = byId.get(state.selectedBlock); return item ? blockAddress(item) : null; } };
}
