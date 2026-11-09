import { decorateModCard } from './mod-runtime.js';
import { resetSvgViewport } from './svg-viewport.js';
import { appendFlowCard } from './flow-card.js';
import { cardDocumentation } from './card-documentation.js';
import { projectColor, nodeColor } from './colors.js';
import { methodOwnerId } from './path-row-layout.js';
import { classifyCodeNode } from './code-semantics.js';
import { inlineFlowEdgeRouter, smoothImplementationEdgeRouter } from './inline-flow-edges.js';
import { cachedPathRouter } from './path-route-cache.js';
import { RectangleIndex, cardRectangle } from './rectangle-index.js';
import { entryPathCaption, entryPathStub } from './entry-path-edges.js';
import { appendEntryPathMarker } from './entry-path-marker.js';
import { connectionCurve } from './edge-curves.js';
import { pathStubKey } from './path-control-tree-layout.js';
import { pathRelations, pathDirection } from './path-relations.js';
import { revealCanvasAction } from './canvas-viewport.js';
let markerSequence = 0;
const NS = 'http://www.w3.org/2000/svg';
/** Ширина подписи кнопки: живой SVG, если длина уже посчитана, иначе примерно 7.2px на знак. */
function pathButtonWidth(textNode, label) {
  const measured = textNode.getComputedTextLength?.() ?? 0;
  if (measured > 0) return measured;
  return label.length * 7.2;
}

/** Подпись кнопки следующего шага: непустой caption ребра, иначе «Далее». */
function pathNextLabel(edge) {
  const caption = edge.caption == null ? '' : String(edge.caption);
  return caption ? caption : 'Далее';
}

/** Укорачивает подпись, пока она не помещается в оставшуюся ширину. Карточку из-за этого не растягиваем. */
function shortenPathButtonLabel(label, room, measure) {
  let text = label;
  while (text.length > 1 && measure(text) > room) {
    const core = text.endsWith('…') ? text.slice(0, -1) : text;
    if (core.length <= 1) return '…';
    text = `${core.slice(0, -1)}…`;
  }
  return text;
}

/** Владелец метода различает одинаковые имена; имя файла остаётся надёжным запасным вариантом. */
function pathOwnerLabel(node) {
  const owner = node.typeContext?.name || node.declaringType || node.typeName || node.owner || node.className;
  if (typeof owner === 'string' && owner) return owner;
  const filename = String(node.file || '').split(/[\\/]/).pop();
  return filename ? filename.replace(/\.[^.]+$/, '') : String(node.kind || 'метод');
}

/** На общей карте условие сохраняет имя своего метода, поскольку внешней рамки владения нет. */
function pathControlNote(node, byId, flatFlow) {
  if (node.dataSelection) return 'Объявленные пары ключ → значение; значения не вычисляются';
  if (flatFlow) {
    const owner = byId.get(node.ownerId) || {};
    return `${pathOwnerLabel(owner)}.${String(owner.label || '').replace(/^\./, '')}`;
  }
  return node.kind === 'if' || node.kind.startsWith('switch') ? 'Условие · подписанные ветви' : 'Цикл · тело и выход';
}

/** Подсказка сохраняет доказательство перехода и не выдаёт dispatch за выбранную runtime-реализацию. */
export function pathEdgeEvidence(edge) {
  const candidate = edge.relation === 'dispatches_to' || String(edge.confidence).toLowerCase() === 'candidate' || String(edge.confidence).toUpperCase() === 'INFERRED';
  const origin = edge.relation ? 'Связь сохранённого графа' : 'Структура текущего исходника';
  const lines = [candidate ? 'Возможный переход по статическим свидетельствам' : origin];
  if (edge.caption) lines.push(edge.caption);
  if (edge.relation) lines.push(`Связь: ${edge.relation}`);
  if (edge.confidence) lines.push(`Уверенность: ${edge.confidence}`);
  if (edge.graphConfidence) lines.push(`Свидетельство графа: ${edge.graphConfidence}`);
  if (edge.sourceLine) lines.push(`${edge.relation === 'calls' ? 'Строка вызова' : 'Строка исходника'}: ${edge.sourceLine}`);
  if (edge.source_file) lines.push(`Исходник: ${String(edge.source_file).slice(0, 300)}${edge.source_location ? `:${String(edge.source_location).slice(0, 40)}` : ''}`);
  if (edge.evidence) {
    lines.push('Основание из снимка исходников: статическое сопоставление, выбор при выполнении не установлен.');
    if (typeof edge.evidence === 'string') lines.push(edge.evidence.slice(0, 300));
    else {
      const proof = edge.evidence;
      const kinds = { 'typed-interface-call': 'Вызов через тип интерфейса', 'interface-implementation': 'Реализация интерфейса', 'method-group-binding': 'Передача метода в параметр делегата' };
      if (kinds[proof.kind]) lines.push(kinds[proof.kind]);
      const fields = { rule: 'Правило', parameter: 'Параметр', receiverType: 'Тип получателя', declaringType: 'Тип, объявляющий метод', interfaceType: 'Интерфейс', implementationType: 'Реализация', signature: 'Сигнатура', file: 'Файл', line: 'Строка', bindingFile: 'Файл передачи метода', bindingLine: 'Строка передачи метода' };
      for (const [key, label] of Object.entries(fields)) {
        if (typeof proof[key] === 'string' || typeof proof[key] === 'number') lines.push(`${label}: ${String(proof[key]).slice(0, 300)}`);
      }
      if (proof.ambiguous) lines.push('Несколько статически совместимых реализаций.');
    }
  }
  return lines.join('\n');
}

/** Создаёт SVG-элемент; текст всегда устанавливается безопасно через textContent. */
function element(tag, attributes, text) {
  const node = document.createElementNS(NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Индексирует ссылки формата «target::ref:source:sequence»; source может сам содержать двоеточия. */
function indexPathReferences(placed) {
  const references = new Map();
  for (const item of placed) {
    if (!item.reference) continue;
    const prefix = `${item.focusId}::ref:`;
    const source = String(item.id).slice(prefix.length).replace(/:[^:]*$/, '');
    const key = JSON.stringify([source, item.focusId]);
    // Как и прежний find, выбираем первую ссылку при нескольких вызовах одной цели.
    if (!references.has(key)) references.set(key, item);
  }
  return references;
}

/** Включает контрольную оболочку плавной связи в область команды «Вместить». */
function includeCurveBounds(d, boundsOf) {
  const coordinates = d.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi).map(Number);
  for (let index = 0; index < coordinates.length; index += 2) boundsOf(coordinates[index], coordinates[index + 1], 0, 0);
}

/** Рисует отдельно каждую пустую альтернативу дерева; её подпись открывает исходник владельца. */
function appendTreeStubs(view, stubs, positions, stubPositions, byId, arrowMarker, route, occupiedLabels, boundsOf) {
  for (const stub of stubs) {
    const source = positions.get(stub.source), target = stubPositions.get(pathStubKey(stub));
    if (!source || !target) continue;
    const d = route(source, target).d;
    includeCurveBounds(d, boundsOf);
    const path = element('path', { class: 'path-edge path-branch-stub', d, fill: 'none', stroke: '#9aa9a0',
      'stroke-dasharray': '4 3', 'marker-end': arrowMarker });
    path.append(element('title', {}, `${stub.caption}: подробнее в исходнике`));
    view.viewport.append(path);
    const x = target.x - target.w / 2 + 12;
    const caption = stub.caption.length > 28 ? `${stub.caption.slice(0, 27)}…` : stub.caption;
    const label = element('text', { x, y: target.y - 4, class: 'path-edge-label', role: 'button', tabindex: 0 }, caption);
    label.append(element('title', {}, `${stub.caption}: подробнее в исходнике`));
    const owner = byId.get(stub.source);
    view.bindPathAction(label, () => view.onPathSource?.(owner?.ownerId, owner?.line));
    view.viewport.append(label);
    view.viewport.append(element('text', { x, y: target.y + 14, class: 'path-edge-label' }, 'детали в исходнике'));
    occupiedLabels.push({ left: x, right: x + 248, top: target.y - 18, bottom: target.y + 18 });
    boundsOf(target.x - target.w / 2, target.y - target.h / 2, target.w, target.h);
  }
}

/** Рисует подготовленную сцену, сохраняя исходные ID, доказательства связей и текущие callbacks общего view. */
export function renderExecutionPath(view, scene, selected) {
  const { flowPath, flatFlow, nodes, visuals, visualEdges, byId, stubs, layout, positions, methodNumbers, connectionCounts } = scene;
  const implementations = new Set();
  for (const edge of scene.scope.edges) if (edge.relation === 'dispatches_to') implementations.add(edge.target);
  view.pathMethodNumbers = methodNumbers;
  view.pathFlatFlow = Boolean(flatFlow);
  view.pathFrameElements = new Map();
  view.pathEdgeElements = [];
  view.pathReferenceOwners = new Map();
  view.pathPanelOwners = new Map(layout.frames.filter(frame => !frame.decision).map(frame => [frame.panelIndex, frame.id]));
  const names = new Map((view.colorProjects || []).map(project => [String(project.id), project.label]));
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const boundsOf = (x, y, w, h) => {
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x + w);
    maxY = Math.max(maxY, y + h);
  };
  for (const frame of layout.frames) boundsOf(frame.x, frame.y, frame.w, frame.h);
  for (const item of layout.placed) boundsOf(item.x - item.w / 2, item.y - item.h / 2, item.w, item.h);
  if (flatFlow) {
    for (const item of layout.placed) {
      if (String(item.id).startsWith('path-entry:')) boundsOf(item.x - 150, item.y - 8, 300, 62);
    }
  }
  if (!layout.placed.length) {
    minX = 90;
    minY = 80;
    maxX = 450;
    maxY = 248;
  }
  resetSvgViewport(view);
  let arrowMarker = 'url(#arrow)';
  if (flowPath) {
    const id = `implementation-arrow-${markerSequence++}`;
    const definitions = element('defs', {});
    const marker = element('marker', { id, markerWidth: 8, markerHeight: 8, refX: 6, refY: 3, orient: 'auto', markerUnits: 'userSpaceOnUse' });
    marker.append(element('path', { d: 'M0,0 L6,3 L0,6', fill: '#5f7d70' }));
    definitions.append(marker);
    view.viewport.append(definitions);
    arrowMarker = `url(#${id})`;
  }
  view.nodeElements.clear();
  view.communityElements.clear();
  view.positions = new Map();
  for (const [id, point] of positions) {
    view.positions.set(id, { ...point, id, r: point.h / 2, labelWidth: point.w, labelBottom: point.h / 2 });
  }
  view.flowBounds = { x: minX - 24, y: minY - 24, width: maxX - minX + 48, height: maxY - minY + 48 };
  view.containers = [];
  view.ingressPositions = [];
  // Внешняя рамка записана после вложенных, поэтому рисуется первой и не закрывает блоки методов.
  for (let index = layout.frames.length - 1; index >= 0; index -= 1) {
    const frame = layout.frames[index];
    const node = byId.get(frame.id);
    const projectId = String(node?.codeProjectId ?? '__unassigned__');
    const label = names.get(projectId) || (projectId === '__unassigned__' ? 'Без проекта' : projectId);
    const palette = projectColor(label, { unassigned: projectId === '__unassigned__' });
    const frameClass = frame.decision ? 'path-decision-frame' : 'path-method-frame';
    const semantic = classifyCodeNode(node || {});
    const block = element('g', { class: `${frameClass}${flowPath ? ' implementation-flow-frame' : ''}`, 'data-method-id': frame.id });
    let frameStroke = palette.stroke;
    if (flowPath) frameStroke = frame.decision ? '#9b7628' : semantic.color;
    const shape = {
      x: frame.x, y: frame.y, width: frame.w, height: frame.h, rx: 16,
      fill: frame.decision ? '#fffaf0' : flowPath ? semantic.fill : palette.fill,
      stroke: frameStroke,
    };
    if (frame.decision) shape['stroke-dasharray'] = '5 4';
    block.append(element('rect', shape));
    block.append(element('title', {}, node?.label || label));
    view.viewport.append(block);
    if (!frame.decision) view.pathFrameElements.set(frame.id, block);
  }
  const occupiedPathLabels = [];
  const routeFlow = flatFlow ? cachedPathRouter(view, positions, inlineFlowEdgeRouter) : null;
  const captionCards = routeFlow ? new RectangleIndex(positions.values(), cardRectangle) : null;
  if (layout.stubPositions && routeFlow) {
    appendTreeStubs(view, stubs, positions, layout.stubPositions, byId, arrowMarker, routeFlow, occupiedPathLabels, boundsOf);
  }
  // Несопоставленные arms одного switch разделяют один выходной порт, а не рисуют подписи друг на друге.
  const stubGroups = new Map();
  for (const stub of stubs) {
    if (layout.stubPositions && routeFlow) continue;
    if (!stubGroups.has(stub.source)) stubGroups.set(stub.source, []);
    stubGroups.get(stub.source).push(stub.caption);
  }
  for (const [sourceId, captions] of stubGroups) {
    const stub = { source: sourceId, caption: captions.length > 1 ? `${captions.length} ветвей · исходник` : captions[0] };
    const source = positions.get(stub.source);
    if (!source) continue;
    const y1 = source.y + source.h / 2;
    let stubPath = `M ${source.x} ${y1} L ${source.x} ${y1 + 22}`;
    if (flowPath) stubPath = entryPathStub(source);
    view.viewport.append(element('path', { class: 'path-edge', d: stubPath, fill: 'none', stroke: '#9aa9a0', 'stroke-dasharray': '4 3', 'marker-end': arrowMarker }));
    if (flowPath) {
      const caption = stub.caption.length > 28 ? `${stub.caption.slice(0, 27)}…` : stub.caption;
      const label = element('text', { x: source.x, y: y1 + 20, class: 'path-edge-label', 'text-anchor': 'middle' }, caption);
      label.append(element('title', {}, `${captions.join('\n')}: подробнее в исходнике`));
      label.setAttribute('role', 'button');
      label.setAttribute('tabindex', 0);
      const owner = byId.get(stub.source);
      view.bindPathAction(label, () => view.onPathSource?.(owner?.ownerId || String(stub.source).split('::branch:')[0], owner?.line));
      view.viewport.append(label);
      view.viewport.append(element('text', { x: source.x, y: y1 + 34, class: 'path-edge-label', 'text-anchor': 'middle' }, 'детали в исходнике'));
      for (const [text, baseline] of [[caption, y1 + 20], ['детали в исходнике', y1 + 34]]) {
        occupiedPathLabels.push({ left: source.x - text.length * 3.5, right: source.x + text.length * 3.5, top: baseline - 11, bottom: baseline + 2 });
      }
    } else view.viewport.append(element('text', { x: source.x + 8, y: y1 + 20, class: 'path-edge-label' }, `${stub.caption}: подробнее в исходнике`));
  }
  const routeImplementation = flowPath && !flatFlow ? smoothImplementationEdgeRouter(positions, layout.frames, occupiedPathLabels, { horizontal: true }) : null;
  const referenceEdges = new Map();
  const referencesByTransition = indexPathReferences(layout.placed);
  for (const [edgeIndex, edge] of visualEdges.entries()) {
    const source = positions.get(edge.source);
    // Повторный вызов соединяем с локальной ссылкой, чтобы длинная дуга не пересекала чужие тела методов.
    const reference = referencesByTransition.get(JSON.stringify([edge.source, edge.target]));
    const target = positions.get(reference?.id || edge.target);
    if (!source || !target) continue;
    let x1;
    let y1;
    let x2;
    let y2;
    let d;
    let captionPoint;
    const sameRow = Math.abs(target.y - source.y) <= 24;
    if (routeFlow) {
      d = routeFlow(source, target, edgeIndex).d;
      if (edge.caption) captionPoint = entryPathCaption(d, edge.caption, positions, occupiedPathLabels, captionCards);
    } else if (routeImplementation) {
      const route = routeImplementation(source, target, edge.caption);
      d = route.d;
      captionPoint = route.caption;
    } else if (target.y > source.y + 24) {
      // Перенос на следующую строку — продолжение пути, а не возврат к уже показанному шагу.
      x1 = source.x;
      y1 = source.y + source.h / 2;
      x2 = target.x;
      y2 = target.y - target.h / 2;
      d = connectionCurve({ x: x1, y: y1 }, { x: x2, y: y2 }, { axis: 'vertical' });
    } else if (sameRow && target.x >= source.x) {
      x1 = source.x + source.w / 2;
      y1 = source.y;
      x2 = target.x - target.w / 2;
      y2 = target.y;
      d = connectionCurve({ x: x1, y: y1 }, { x: x2, y: y2 });
    } else {
      // Возврат к уже показанному шагу рисуется дугой под строками, чтобы не перечёркивать историю.
      x1 = source.x;
      y1 = source.y + source.h / 2;
      x2 = target.x;
      y2 = target.y + target.h / 2;
      const below = Math.max(y1, y2) + 40;
      d = `M ${x1} ${y1} C ${x1} ${below} ${x2} ${below} ${x2} ${y2}`;
    }
    const uncertain = edge.relation === 'dispatches_to' || /candidate|inferred/i.test(String(edge.confidence));
    const path = element('path', { class: `path-edge${uncertain ? ' path-edge-candidate' : ''}${flowPath ? ' implementation-flow-edge' : ''}`, d, fill: 'none', stroke: '#5f7d70', 'marker-end': arrowMarker });
    if (flatFlow) {
      path.setAttribute('data-source', edge.source);
      path.setAttribute('data-target', edge.target);
      // Контрольная оболочка включает верхние возвраты в область «Вместить».
      includeCurveBounds(d, boundsOf);
    }
    const rawSource = methodOwnerId(edge.source), rawTarget = methodOwnerId(edge.target);
    path.setAttribute('data-caller-id', rawSource);
    path.setAttribute('data-callee-id', rawTarget);
    view.pathEdgeElements.push({ path, source: rawSource, target: rawTarget });
    if (reference) {
      view.pathReferenceOwners.set(reference.id, rawSource);
      if (!referenceEdges.has(reference.id)) referenceEdges.set(reference.id, []);
      referenceEdges.get(reference.id).push(edge);
    }
    if (uncertain) path.setAttribute('stroke-dasharray', '5 4');
    path.append(element('title', {}, pathEdgeEvidence(edge)));
    view.viewport.append(path);
    if (edge.caption && (!flowPath || captionPoint)) {
      const label = element('text', { x: captionPoint?.x ?? (x1 + x2) / 2, y: captionPoint?.y ?? (y1 + y2) / 2 - 8, class: 'path-edge-label', 'text-anchor': 'middle' }, captionPoint?.text ?? edge.caption);
      label.append(element('title', {}, edge.caption));
      label.addEventListener('click', event => { event.stopPropagation(); view.followPathNode(edge.target); });
      view.viewport.append(label);
    }
  }
  view.appendPathReferenceLinks(positions, layout.frames, referenceEdges, arrowMarker, routeImplementation);
  for (const node of visuals) {
    const point = positions.get(node.id);
    if (!point) continue;
    if (flatFlow && String(node.id).startsWith('path-entry:')) {
      appendEntryPathMarker(view, node, point);
      continue;
    }
    const picked = node.id === selected ? ' selected' : '';
    if (node.decision) {
      const group = element('g', { class: `path-decision${picked}${flowPath ? ' implementation-flow-decision' : ''}`, transform: `translate(${point.x},${point.y})`, role: 'button', tabindex: 0, 'data-control-kind': node.kind, 'aria-label': `${node.label}, строка ${node.line || '?'}` });
      if (flowPath) {
        appendFlowCard(group, { width: point.w, height: point.h, title: `${node.label} · ${node.line || '?'}`,
          context: node.condition, contextLines: 2, note: pathControlNote(node, byId, flatFlow),
          semantic: { id: 'control', color: '#8a6420', fill: '#ffedc2' }, palette: { text: '#6b4a16' }, color: '#8a6420',
          showCounts: false, portClass: 'implementation-flow-port' });
      } else {
        group.append(element('polygon', { points: `0,${-point.h / 2} ${point.w / 2},0 0,${point.h / 2} ${-point.w / 2},0`, fill: '#fff8e8', stroke: '#8a6420', 'stroke-width': 1.4 }));
      group.append(element('text', { y: -22, 'text-anchor': 'middle', class: 'path-decision-kind' }, `${node.label} · ${node.line || '?'}`));
      for (let index = 0; index < 2; index += 1) {
        const start = index * 31;
        if (start >= node.condition.length) break;
        let condition = node.condition.slice(start, start + 31);
        if (index === 1 && node.condition.length > 62) condition = `${condition.slice(0, 30)}…`;
        group.append(element('text', { y: -3 + index * 17, 'text-anchor': 'middle', class: 'path-decision-text' }, condition));
      }
      }
      group.append(element('title', {}, `${node.label}\n${node.condition}`));
      const openBlock = event => {
        event.stopPropagation();
        const owner = String(node.id).split('::branch:')[0];
        view.onPathSource?.(owner, node.line);
      };
      group.addEventListener('click', openBlock);
      group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openBlock(event); } });
      view.viewport.append(group);
      view.nodeElements.set(node.id, { group, radius: 20 });
      continue;
    }
    if (node.exit) {
      const group = element('g', { class: `path-exit${picked}${node.omission ? ' path-omission' : ''}${flowPath ? ' implementation-flow-exit' : ''}`, transform: `translate(${point.x - point.w / 2},${point.y - point.h / 2})`, role: 'button', tabindex: 0, 'aria-label': `${node.label}. Открыть исходник.` });
      if (flowPath) {
        appendFlowCard(group, { width: point.w, height: point.h, left: 0, top: 0, title: node.label || 'выход',
          context: node.condition, contextLines: 2, note: flatFlow ? `${pathOwnerLabel(byId.get(node.ownerId) || {})} · строка ${node.line || '?'}` : node.line ? `Строка ${node.line}` : '',
          semantic: { id: 'exit', color: '#5f7d70', fill: '#e8f2eb' }, palette: { text: '#17382c' }, color: '#5f7d70',
          showCounts: false, outPort: false, portClass: 'implementation-flow-port' });
      } else {
        group.append(element('rect', { width: point.w, height: point.h, rx: 16, fill: '#f4f7f5', stroke: '#5f7d70' }));
        group.append(element('text', { x: 12, y: 24, class: 'path-decision-kind' }, String(node.label || 'выход')));
        const text = String(node.condition || '');
        group.append(element('text', { x: 12, y: 44, class: 'path-decision-text' }, text.length > 34 ? `${text.slice(0, 33)}…` : text));
      }
      group.append(element('title', {}, `${node.label}\n${node.condition || ''}`));
      const openExit = event => { event.stopPropagation(); view.onPathSource?.(node.ownerId, node.line); };
      group.addEventListener('click', openExit);
      group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openExit(event); } });
      view.viewport.append(group);
      view.nodeElements.set(node.id, { group, radius: 20 });
      continue;
    }
    const entryCard = String(node.id).startsWith('path-entry:');
    const facts = view.pathScope.stats.get(node.id);
    const role = classifyCodeNode(node);
    const semantic = implementations.has(node.id) ? { ...role, label: 'Возможная реализация' } : role;
    const group = element('g', { class: `path-card${picked}${flowPath ? ' implementation-flow-card' : ''}`, transform: `translate(${point.x - point.w / 2},${point.y - point.h / 2})`, role: 'button', tabindex: 0, 'data-code-role': semantic.id, 'aria-label': node.label || node.id });
    const title = String(node.label || node.id);
    const documentation = cardDocumentation(node, point.w);
    const ownerLabel = entryCard ? 'Точка входа' : pathOwnerLabel(node);
    const place = node.file ? `${node.file}${node.line ? `:${node.line}` : ''}` : 'Исходник не сопоставлен';
    const details = [];
    if (node.callableFacts?.kind === 'constructor') details.push('Конструктор · создание объекта');
    if (facts?.directCalls) details.push(`${facts.structuralRelations ? 'связи' : 'вызовы'}: ${facts.directCalls}`);
    const branches = node.controlFlow?.branches?.length || facts?.hiddenBranches || 0;
    if (branches) details.push(`ветви: ${branches}`);
    if (facts?.hiddenDescendants) details.push(`скрыто: ${facts.hiddenDescendants}`);
    if (!details.length) details.push(facts?.expanded ? 'Тело раскрыто' : 'Нет сопоставленных вызовов');
    if (flowPath) {
      const projectId = String(node.codeProjectId || '__unassigned__');
      const palette = projectColor(names.get(projectId) || projectId, { unassigned: projectId === '__unassigned__' });
      const number = methodNumbers.get(node.id);
      appendFlowCard(group, { width: point.w, height: point.h, left: 0, top: 0, title, context: ownerLabel,
        note: number ? `Блок ${number} · ${details.join(' · ')}` : details.join(' · '), semantic, palette,
        color: nodeColor(node, view.colorProjects || []), counts: connectionCounts.get(String(node.id)),
        footerHeight: entryCard ? 0 : 30, documentation: entryCard ? null : documentation,
        bodyClass: 'path-card-body', portClass: 'implementation-flow-port', roleClass: 'implementation-flow-role' });
    } else {
      group.append(element('rect', { width: point.w, height: point.h, rx: 8, class: 'path-card-body', fill: '#fbfcfa', stroke: node.id === selected ? '#17382c' : '#b7c9be' }));
      group.append(element('rect', { x: 1, y: 1, width: point.w - 2, height: 32, rx: 7, fill: entryCard ? '#6b4a16' : '#17382c' }));
      group.append(element('text', { x: 12, y: 21, class: 'path-card-title' }, title.length > 34 ? `${title.slice(0, 33)}…` : title));
      group.append(element('text', { x: 12, y: 52, class: 'path-card-owner' }, ownerLabel.length > 34 ? `${ownerLabel.slice(0, 33)}…` : ownerLabel));
      group.append(element('text', { x: 12, y: 72, class: 'path-card-meta' }, place.length > 40 ? `…${place.slice(-38)}` : place));
      if (!entryCard) group.append(element('text', { x: 12, y: 94, class: 'path-card-meta' }, details.join(' · ')));
      if (!entryCard) for (const [index, line] of documentation.lines.entries()) {
        group.append(element('text', { x: 12, y: 116 + index * 17, class: 'flow-card-description' }, line));
      }
    }
    if (node.file && !entryCard) {
      const source = element('text', { x: 12, y: point.h - 13, class: 'path-card-source', role: 'button', tabindex: 0, 'aria-label': `Исходник ${ownerLabel}.${title}` }, 'Исходник');
      view.bindPathAction(source, () => view.onPathSource?.(node.id));
      group.append(source);
    }
    if (!entryCard && facts?.canExpand && node.id !== view.pathScope.focusId) {
      const label = facts.expanded ? 'Свернуть' : flowPath ? `Раскрыть ${facts.contentKind || 'вызовы'}` : 'Раскрыть';
      const expand = element('text', { x: point.w - 12, y: point.h - 13, 'text-anchor': 'end', class: 'path-card-expand', role: 'button', tabindex: 0, 'aria-label': `${label} ${ownerLabel}.${title}` }, label);
      view.bindPathAction(expand, () => view.togglePathNode(node.id));
      group.append(expand);
    }
    group.append(element('title', {}, `${ownerLabel}.${title}\n${documentation.text}\n${place}\n${details.join(' · ')}\nСтатическая схема; раскрытие показывает тело метода на месте.`));
    if (!entryCard) view.bindMapTooltip(group, node, connectionCounts.get(String(node.id)));
    group.addEventListener('click', () => view.onSelect(node.id));
    group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); view.onSelect(node.id); } });
    decorateModCard(view, group, node, {left:0,top:0,width:point.w,height:point.h});
    view.viewport.append(group);
    view.nodeElements.set(node.id, { group, radius: 20 });
  }
  for (const item of layout.placed) {
    if (!item.reference) continue;
    const point = positions.get(item.id);
    const target = byId.get(item.focusId);
    const title = String(target?.label || item.focusId);
    const number = methodNumbers.get(item.focusId);
    const owner = pathOwnerLabel(target || {});
    const direction = pathDirection(point, positions.get(item.focusId));
    const destination = flowPath && number ? `${direction.arrow} К блоку ${number} · ${owner}` : 'к блоку метода';
    const group = element('g', { class: `path-reference${flowPath ? ' implementation-flow-reference' : ''}`, transform: `translate(${point.x - point.w / 2},${point.y - point.h / 2})`, role: 'button', tabindex: 0, 'aria-label': flowPath ? `${title} · ${destination}` : title, 'data-focus-id': item.focusId });
    if (flowPath) {
      const semantic = classifyCodeNode(target || {});
      const projectId = String(target?.codeProjectId || '__unassigned__');
      const palette = projectColor(names.get(projectId) || projectId, { unassigned: projectId === '__unassigned__' });
      appendFlowCard(group, { width: point.w, height: point.h, left: 0, top: 0, title, context: owner,
        note: destination, semantic, palette, color: nodeColor(target || {}, view.colorProjects || []), showCounts: false,
        portClass: 'implementation-flow-port' });
    } else {
      group.append(element('rect', { width: point.w, height: point.h, rx: 8, fill: '#f4f7f5', stroke: '#5f7d70' }));
      group.append(element('text', { x: 12, y: 24, class: 'path-decision-kind' }, title.length > 22 ? `${title.slice(0, 21)}…` : title));
      group.append(element('text', { x: 12, y: 44, class: 'path-reference-label' }, destination.length > 30 ? `${destination.slice(0, 29)}…` : destination));
    }
    group.append(element('title', {}, `${owner}.${title}\n${target?.file || ''}${target?.line ? `:${target.line}` : ''}\n${destination}: ${direction.label} относительно ссылки. Сохраняет масштаб карты.`));
    group.addEventListener('click', event => { event.stopPropagation(); view.followPathNode(item.focusId); });
    group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); view.followPathNode(item.focusId); } });
    view.viewport.append(group);
    view.nodeElements.set(item.id, { group, radius: 20 });
  }
  view.selectedId = selected;
  if (flatFlow) {
    for (const label of occupiedPathLabels) boundsOf(label.left, label.top, label.right - label.left, label.bottom - label.top);
    view.flowBounds = { x: minX - 24, y: minY - 24, width: maxX - minX + 48, height: maxY - minY + 48 };
  }
  const root = nodes.find(node => node.id === view.pathScope.focusId);
  view.onPathScopeChange?.({
    focusId: view.pathScope.focusId, label: root?.label || view.pathScope.focusId,
    visible: view.pathScope.visible, total: view.pathScope.total,
    expanded: view.pathScope.expandedIds.size, canBack: false,
    canExpandAll: [...view.reachablePathNodeIds()].some(id => !view.pathScope.expandedIds.has(id)),
  });
  view.updatePathRelations();
}

/** Продолжает локальный вызов от ссылки до реального метода; повторные свидетельства сохраняются в одной линии. */
export function appendPathReferenceLinks(view, positions, frames, referenceEdges, arrowMarker, flowRouter) {
  const route = flowRouter || smoothImplementationEdgeRouter(positions, frames);
  for (const [referenceId, edges] of referenceEdges) {
    const reference = positions.get(referenceId);
    const target = positions.get(reference?.focusId);
    if (!reference || !target) continue;
    const uncertain = edges.some(edge => edge.relation === 'dispatches_to' || /candidate|inferred/i.test(String(edge.confidence)));
    const path = element('path', {
      class: `path-edge path-reference-link${uncertain ? ' path-edge-candidate' : ''}${flowRouter ? ' implementation-flow-edge' : ''}`,
      d: route(reference, target).d, fill: 'none', stroke: '#5f7d70', 'marker-end': arrowMarker,
      'data-reference-id': referenceId, 'data-caller-id': methodOwnerId(edges[0].source),
      'data-callee-id': reference.focusId,
    });
    if (uncertain) path.setAttribute('stroke-dasharray', '5 4');
    path.append(element('title', {}, edges.map(pathEdgeEvidence).join('\n\n')));
    view.viewport.append(path);
    view.pathEdgeElements.push({ path, source: methodOwnerId(edges[0].source), target: reference.focusId });
  }
}

/** Подсвечивает raw связи выбранного метода и обе стороны локальных ссылок без новых пересекающих карту дуг. */
export function updatePathRelations(view) {
  if (!view.pathSceneActive || !view.pathData) return;
  const info = pathRelations(view.pathData, view.selectedId, view.positions, view.pathMethodNumbers, view.pathReveal);
  if (!info) { view.onPathRelationsChange?.(null); return; }
  const outgoing = new Set(info.outgoing.map(item => item.id));
  const incoming = new Set(info.incoming.map(item => item.id));
  const revealed = new Set(info.revealed.map(item => item.id));
  for (const [id, item] of view.nodeElements) {
    item.group.querySelector('.path-relation-badge')?.remove();
    const point = view.positions.get(id);
    const owner = point?.reference ? view.pathReferenceOwners.get(id) || view.pathPanelOwners.get(point.panelIndex) : methodOwnerId(id);
    const target = point?.reference ? point.focusId : owner;
    const isSelected = owner === info.selected.id;
    const fromSelected = point?.reference ? isSelected && outgoing.has(target) : outgoing.has(target);
    const toSelected = point?.reference ? incoming.has(owner) && target === info.selected.id : incoming.has(target);
    item.group.classList.toggle('path-related-outgoing', fromSelected);
    item.group.classList.toggle('path-related-incoming', toSelected && !fromSelected);
    if (!item.group.classList.contains('path-card')) continue;
    let badge = '';
    if (isSelected) badge = view.pathFlatFlow ? 'Выбранный метод' : 'Выбранный метод · условия внутри';
    else if (fromSelected) badge = info.selected.number ? `Вызов из блока ${info.selected.number}` : 'Вызван выбранным методом';
    else if (toSelected) badge = 'Вызывает выбранный метод';
    else if (revealed.has(id)) badge = 'Новый блок';
    const relation = info.outgoing.find(candidate => candidate.id === id);
    if (relation?.candidate && fromSelected && !isSelected) badge = 'Кандидат для выбранного метода';
    if (badge) item.group.append(element('text', { x: 12, y: -5, class: 'path-relation-badge' }, badge));
  }
  for (const [id, frame] of view.pathFrameElements) {
    frame.classList.toggle('path-related-selected', id === info.selected.id);
    frame.classList.toggle('path-related-outgoing', outgoing.has(id) && id !== info.selected.id);
    frame.classList.toggle('path-related-incoming', incoming.has(id) && !outgoing.has(id) && id !== info.selected.id);
    frame.classList.toggle('path-related-new', revealed.has(id) && !outgoing.has(id));
  }
  for (const edge of view.pathEdgeElements) {
    const fromSelected = edge.source === info.selected.id;
    const toSelected = edge.target === info.selected.id && !fromSelected;
    edge.path.classList.toggle('path-related-outgoing', fromSelected);
    edge.path.classList.toggle('path-related-incoming', toSelected);
    edge.path.classList.toggle('path-relation-muted', !fromSelected && !toSelected);
  }
  view.onPathRelationsChange?.(info);
}

/** Порты вертикального Flow показывают места входа и выхода статических связей, не вычисленный runtime-порядок. */
export function appendImplementationPorts(view, group, point, color, centered = false, inputOnly = false) {
  const x = centered ? 0 : point.w / 2;
  const top = centered ? -point.h / 2 : 0;
  const ports = inputOnly ? [['in', top]] : [['in', top], ['out', top + point.h]];
  for (const [side, y] of ports) {
    const port = element('circle', { cx: x, cy: y, r: 4, class: `implementation-flow-port implementation-flow-port-${side}`, fill: color, stroke: '#fbfcfa', 'stroke-width': 1.5, 'aria-hidden': true });
    port.append(element('title', {}, side === 'in' ? 'Входящая статическая связь' : 'Исходящая статическая связь'));
    group.append(port);
  }
}

/** Привязывает действие SVG к мыши и клавиатуре, не активируя выбор родительской карточки. */
export function bindPathAction(view, control, action) {
  control.addEventListener('focus', () => {
    // Pointer focus precedes click; moving the target here can send that click to an adjacent action.
    if (control.matches?.(':focus-visible') === false) return;
    revealCanvasAction(view, control);
  });
  control.addEventListener('click', event => { event.stopPropagation(); action(); });
  control.addEventListener('keydown', event => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    event.stopPropagation();
    action();
  });
}

/**
   * Кнопки прямых следующих шагов внизу карточки.
   * Если подписи не входят в ширину, лишние не рисуются, а первая укорачивается: карточку не растягиваем.
   * @param {SVGGElement} group карточка пути
   * @param {Array<{target:string, caption?:string}>} edges непосредственные визуальные преемники
   * @param {number} cardWidth
   * @param {number} cardHeight
   * @returns {string[]} подписи, которые не поместились и остаются в подсказке
   */
export function appendPathSuccessorButtons(view, group, edges, cardWidth, cardHeight) {
  let x = 12;
  const limit = cardWidth - 8;
  const hidden = [];
  let roomLeft = true;
  for (const edge of edges) {
    const full = pathNextLabel(edge);
    if (!roomLeft) {
      hidden.push(full);
      continue;
    }
    let label = full.length > 18 ? `${full.slice(0, 17)}…` : full;
    const button = element('text', { x, y: cardHeight - 12, class: 'path-card-next' });
    const labelNode = element('tspan', {}, label);
    button.append(labelNode);
    button.append(element('title', {}, full));
    group.append(button);
    const measure = candidate => {
      labelNode.textContent = candidate;
      return pathButtonWidth(button, candidate);
    };
    let width = measure(label);
    if (x + width > limit) {
      if (x > 12) {
        button.remove();
        hidden.push(full);
        roomLeft = false;
        continue;
      }
      label = shortenPathButtonLabel(label, limit - x, measure);
      width = measure(label);
    }
    const target = edge.target;
    button.addEventListener('click', event => {
      event.stopPropagation();
      view.followPathNode(target);
    });
    x += width + 8;
  }
  return hidden;
}

/** Обводит совпавшие карточки активной реализации, не обращаясь к оставшемуся от другого вида FlowRenderer. */
export function setPathSearchMatches(view, ids) {
  const matches = new Set(ids);
  let visible = 0;
  for (const [id, item] of view.nodeElements) {
    item.group.querySelector('.regex-ring')?.remove();
    const occurrence = view.pathSequenceScene?.byId.get(id);
    if (!matches.has(id) && !(occurrence && (matches.has(occurrence.sourceOwnerId) || occurrence.targetNodeIds?.some(target => matches.has(target))))) continue;
    const point = view.positions.get(id);
    if (!point) continue;
    const centered = item.group.classList.contains('path-decision');
    item.group.append(element('rect', {
      x: (centered ? -point.w / 2 : 0) - 4,
      y: (centered ? -point.h / 2 : 0) - 4,
      width: point.w + 8, height: point.h + 8, rx: 12,
      fill: 'none', stroke: '#2866ad', 'stroke-width': 3, 'pointer-events': 'none', class: 'regex-ring',
    }));
    visible += 1;
  }
  return visible;
}
