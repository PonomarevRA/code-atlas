import { FLOW_CARD, flowCardMetrics } from './flow-card.js';
import { cardDocumentation } from './card-documentation.js';
import { buildMethodFrameTree, packMethodFrames } from './path-row-layout.js';
import { codeConnectionCounts } from './code-semantics.js';
import { implementationFlowLayout } from './implementation-flow-layout.js';
import { flowLayout } from './flow-layout.js';
import { pathReadingLayout } from './path-reading-layout.js';
import { naturalLayout } from './natural-layout.js';
import { filterPathControls } from './path-control-filter.js';
import { pathControlTreeLayout } from './path-control-tree-layout.js';
import { PATH_SPACING } from './path-spacing.js';
import { staticDataScene } from './path-static-data.js';
/** У вложенной ветви ближайший владелец — последний извлечённый охватывающий arm. */
function nearestPathArm(ids, armOwner) {
  for (let index = (ids?.length || 0) - 1; index >= 0; index -= 1) {
    const owner = armOwner.get(ids[index]);
    if (owner) return owner;
  }
  return null;
}

/**
 * Преобразует локальную область в сцену без DOM и изменения входных nodes/edges.
 * Сохраняет ветви, выходы и ограничения анализа; размещение не задаёт порядок выполнения.
 * reading и natural показывают лексические условия деревом внутри горизонтальных рангов методов.
 * flowPositions использует подготовленные места текущей проекции; controlFilters скрывает только условия/циклы.
 */
export function buildPathScene(scope, { flowPath = false, flatFlow = false, flowPositions, reading = false, natural = false, controlFilters, selected } = {}) {
  const { nodes, edges } = scope;
  const connectionCounts = codeConnectionCounts(edges);
  const nodeIds = new Set(nodes.map(node => node.id));
  const methodsById = new Map(nodes.map(node => [node.id, node]));
  let visuals = nodes.map(node => ({ ...node, decision: false }));
  const armOwner = new Map();
  const visualIds = new Set(nodeIds);
  const branchLimit = flowPath ? 100 : 12;
  for (const node of nodes) {
    for (const [index, branch] of (node.controlFlow?.branches || []).slice(0, branchLimit).entries()) {
      const id = `${node.id}::branch:${branch.line || index}:${index}`;
      const loopKind = ['for', 'foreach', 'while', 'do'].includes(branch.kind) ? branch.kind : '';
      const kind = branch.kind === 'if' ? 'if' : String(branch.kind || '').includes('switch') ? 'switch' : (loopKind || branch.kind || 'условие');
      visualIds.add(id);
      visuals.push({ id, ownerId: node.id, parentArmIds: branch.parentArmIds || [], armIds: (branch.arms || []).map(arm => arm.id), decision: true, kind, label: kind === 'conditional' ? '?:' : kind, condition: String(branch.header || branch.condition || branch.selector || 'условие не извлечено'), line: branch.line });
      for (const arm of branch.arms || []) armOwner.set(arm.id, { id, arm });
    }
  }
  let visualEdges = [];
  /** Пустые альтернативы ?: показывают выбранное выражение даже без вызовов. */
  const armCaption = arm => {
    const name = arm.kind === 'true' ? 'истина' : arm.kind === 'false' ? 'иначе' : arm.kind === 'body' ? 'тело цикла' : String(arm.label || arm.kind || 'ветвь');
    return (arm.expression ? `${name}: ${arm.expression}` : name).slice(0, 42);
  };
  for (const node of visuals) {
    if (!node.decision) continue;
    const parent = nearestPathArm(node.parentArmIds, armOwner);
    visualEdges.push({ source: parent?.id || node.ownerId, target: node.id, caption: parent ? armCaption(parent.arm) : '', armId: parent?.arm.id });
  }
  const linked = new Set();
  for (const edge of edges) {
    const owner = nearestPathArm(edge.branchIds, armOwner);
    if (owner) {
      visualEdges.push({ ...edge, source: owner.id, caption: armCaption(owner.arm), armId: owner.arm.id });
      linked.add(`${owner.arm.id}:${edge.target}`);
    } else visualEdges.push({ ...edge, caption: edge.relation === 'dispatches_to' ? 'Возможная реализация' : edge.branchIds?.length ? 'ветвь скрыта' : (edge.caption || '') });
  }
  for (const node of nodes) {
    for (const event of node.events || []) {
      if (event.kind === 'data-write') {
        const owner = nearestPathArm(event.branchIds, armOwner);
        const id = `${node.id}::data-write:${event.line}:${event.column}`;
        visuals.push({ id, ownerId: node.id, exit: true, staticValue: true, label: event.dataKey,
          condition: event.dataValue, line: event.line });
        visualEdges.push({ source: owner?.id || node.id, target: id, caption: 'запись в словарь', armId: owner?.arm.id });
        continue;
      }
      if (event.kind !== 'call') continue;
      const owner = nearestPathArm(event.branchIds, armOwner);
      if (!owner) continue;
      for (const target of event.targetNodeIds || []) {
        if (!nodeIds.has(target) || linked.has(`${owner.arm.id}:${target}`)) continue;
        linked.add(`${owner.arm.id}:${target}`);
        visualEdges.push({ source: owner.id, target, caption: armCaption(owner.arm), armId: owner.arm.id, relation: 'calls', confidence: event.confidence, sourceLine: event.line });
      }
    }
    for (const [index, terminal] of (node.controlFlow?.terminals || []).slice(0, 12).entries()) {
      const owner = nearestPathArm(terminal.branchIds, armOwner);
      // Каждый извлечённый return/throw сохраняет строку и собственный блок выхода.
      const exitOwner = owner?.id || node.id;
      const exitId = `${exitOwner}::exit:${terminal.line || index}:${index}`;
      if (visualIds.has(exitId)) continue;
      visualIds.add(exitId);
      visuals.push({ id: exitId, ownerId: node.id, decision: false, exit: true, label: terminal.kind, condition: String(terminal.text || terminal.kind), line: terminal.line });
      visualEdges.push({ source: owner?.id || node.id, target: exitId, caption: owner ? armCaption(owner.arm) : '', armId: owner?.arm.id });
    }
    const omitted = Math.max(0, (node.controlFlow?.branches?.length || 0) - branchLimit);
    const omittedTerminals = Math.max(0, (node.controlFlow?.terminals?.length || 0) - 12);
    if (omitted || omittedTerminals || node.controlFlow?.truncated) {
      const id = `${node.id}::exit:omissions`;
      const detail = [omitted ? `${omitted} ветвей` : '', omittedTerminals ? `${omittedTerminals} выходов` : ''].filter(Boolean).join(' · ');
      visuals.push({ id, ownerId: node.id, exit: true, omission: true, label: 'Детали ограничены', condition: detail || 'Анализ исходника ограничен', line: node.line });
      visualEdges.push({ source: node.id, target: id, caption: 'подробнее в исходнике' });
    }
  }
  // Пустая ветвь остаётся подписанной стрелкой; дерево резервирует ей отдельную полосу.
  for (const node of nodes) {
    const data = staticDataScene(node);
    visuals.push(...data.visuals);
    visualEdges.push(...data.edges);
  }
  let stubs = [];
  const linkedArms = new Set();
  for (const edge of visualEdges) linkedArms.add(edge.armId);
  for (const owner of armOwner.values()) {
    if (linkedArms.has(owner.arm.id)) continue;
    stubs.push({ source: owner.id, armId: owner.arm.id, caption: armCaption(owner.arm) });
  }
  ({ visuals, visualEdges, stubs } = filterPathControls(visuals, visualEdges, stubs, controlFilters));
  const outgoing = new Map(visuals.map(node => [node.id, []]));
  for (const edge of visualEdges) if (outgoing.has(edge.source) && outgoing.has(edge.target)) outgoing.get(edge.source).push(edge.target);
  for (const node of visuals) {
    if (node.codeProjectId != null && node.codeProjectId !== '') continue;
    const methodId = node.ownerId || node.id;
    node.codeProjectId = methodsById.get(methodId)?.codeProjectId;
  }
  // Обход задаёт только размещение карточек; реальный порядок исполнения не вычисляется.
  const entry = visuals.find(node => String(node.id).startsWith('path-entry:'));
  const startId = entry?.id || (flowPath ? scope.focusId : selected) || visuals[0]?.id;
  const order = [];
  const seen = new Set();
  const stack = startId && outgoing.has(startId) ? [startId] : [];
  while (stack.length) {
    const id = stack.pop();
    if (seen.has(id)) continue;
    seen.add(id);
    order.push(id);
    const targets = outgoing.get(id) || [];
    for (let index = targets.length - 1; index >= 0; index -= 1) {
      if (seen.has(targets[index])) continue;
      stack.push(targets[index]);
    }
  }
  for (const node of visuals) if (!seen.has(node.id)) order.push(node.id);
  const byId = new Map(visuals.map(node => [node.id, node]));
  // Размеры карточек стабильны: раскрытие добавляет тело, а не растягивает заголовок метода.
  const cardSize = node => {
    if (flatFlow && String(node.id).startsWith('path-entry:')) return { w: 16, h: 16 };
    if (flowPath) {
      const method = !node.decision && !node.exit && !String(node.id).startsWith('path-entry:');
      return { w: FLOW_CARD.width, h: method ? flowCardMetrics(node).height : FLOW_CARD.height };
    }
    if (node.decision) return { w: 240, h: 96 };
    if (node.exit) return { w: 230, h: 66 };
    const entryHeight = flowPath ? 112 : 100;
    let height = flowPath ? 154 : 132;
    if (String(node.id).startsWith('path-entry:')) height = entryHeight;
    else height += cardDocumentation(node, 280).height;
    return { w: 280, h: height };
  };
  const sized = [];
  for (const id of order) {
    const node = byId.get(id);
    const size = cardSize(node);
    sized.push({ ...node, w: size.w, h: size.h });
  }
  // Подробный вид сохраняет рамки владения; горизонтальный Flow размещает каждый реальный узел один раз.
  let layout;
  if (flatFlow) {
    // Подготовленная раскладка включает все условия видимых тел; фильтр выбирает ID без пересчёта геометрии.
    const flat = flatPositions(sized, visualEdges, stubs, startId, { flowPositions, natural, reading });
    const reserved = flat.positions;
    const placed = [];
    for (const node of sized) {
      const point = reserved.get(node.id);
      if (point) placed.push({ ...node, x: point.x, y: point.y });
    }
    layout = { frames: [], placed, stubPositions: flat.stubPositions };
  } else {
    const methodFrames = buildMethodFrameTree(sized, visualEdges, order);
    layout = flowPath ? implementationFlowLayout(methodFrames, { direction: 'flow' }) : packMethodFrames(methodFrames);
  }
  const positions = new Map();
  for (const item of layout.placed) positions.set(item.id, { x: item.x, y: item.y, w: item.w, h: item.h, focusId: item.focusId, reference: item.reference, panelIndex: item.panelIndex });
  positions.stubPositions = layout.stubPositions;
  // Номера позволяют найти вынесенное тело; это координаты чтения, не шаги исполнения.
  const methodNumbers = new Map();
  if (flowPath) {
    for (const item of layout.placed) {
      const node = byId.get(item.id);
      if (!node || node.decision || node.exit || item.reference || String(item.id).startsWith('path-entry:')) continue;
      methodNumbers.set(item.id, methodNumbers.size + 1);
    }
  }
  return { scope, flowPath, flatFlow, nodes, visuals, visualEdges, byId, stubs, layout, positions, methodNumbers, connectionCounts };
}

/** Выбирает общую раскладку или уже зарезервированные координаты без повторного расчёта. */
function flatPositions(nodes, edges, stubs, rootId, { flowPositions, natural, reading }) {
  if (flowPositions) return { positions: flowPositions, stubPositions: flowPositions.stubPositions };
  if ((natural || reading) && nodes.some(node => node.decision)) return pathControlTreeLayout(nodes, edges, stubs, rootId, { natural });
  let positions;
  if (natural) positions = naturalLayout(nodes, edges, { rootId, rankGap: PATH_SPACING.horizontal, rowGap: PATH_SPACING.vertical }).positions;
  else if (reading) positions = pathReadingLayout(nodes, edges, rootId);
  else positions = flowLayout(nodes, edges, { cardWidth: FLOW_CARD.width, cardHeight: FLOW_CARD.height + 30,
    rankGap: FLOW_CARD.rankGap, rowGap: FLOW_CARD.rowGap + 30 }).positions;
  return { positions };
}
