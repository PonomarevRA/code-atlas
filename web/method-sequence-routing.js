import { implementationEdgeRouter, placeRouteCaption } from './implementation-edge-routing.js';
import { curveCrossesCards, smoothSequenceDetour } from './inline-flow-edges.js';
import { RectangleIndex, cardRectangle } from './rectangle-index.js';
import { connectionCurve } from './edge-curves.js';
import { choicePortOffset } from './method-sequence-compact.js';

/** Safe fallback for forward links; renderers may supply their existing bypass curve. */
function directCurve(a, b) {
  const horizontal = b.x - b.w / 2 >= a.x + a.w / 2;
  return connectionCurve(horizontal ? { x: a.x + a.w / 2, y: a.y } : { x: a.x, y: a.y + a.h / 2 },
    horizontal ? { x: b.x - b.w / 2, y: b.y } : { x: b.x, y: b.y - b.h / 2 }, { axis: horizontal ? 'horizontal' : 'vertical' });
}

/** Include conservative control-point bounds so fitting/export never clips an outside corridor. */
export function includeRouteBounds(bounds, d) {
  const values = d.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi)?.map(Number) || [];
  let left = bounds.x, top = bounds.y, right = bounds.x + bounds.width, bottom = bounds.y + bounds.height;
  for (let index = 0; index + 1 < values.length; index += 2) {
    left = Math.min(left, values[index] - 8); right = Math.max(right, values[index] + 8);
    top = Math.min(top, values[index + 1] - 8); bottom = Math.max(bottom, values[index + 1] + 8);
  }
  return { x: left, y: top, width: right - left, height: bottom - top };
}

/** One render shares obstacle indexes/grid; lexical loop and method scopes choose local side corridors. */
export function sequenceEdgeRouter(scene, positions = scene.positions, { dx = 0, dy = 0, curve = directCurve } = {}) {
  const routing = new Map([...positions].map(([id, point]) => [id, { ...point, panelIndex: undefined }]));
  const ports = new Map(routing);
  for (const node of scene.visuals) for (const [index] of (node.valueChoices || []).entries()) {
    const point = routing.get(node.id);
    if (point) ports.set(`${node.id}::port:${index}`, { ...point, y: point.y + choicePortOffset(node, index), h: 0 });
  }
  for (const node of scene.visuals) if (node.kind === 'loop') {
    const point = routing.get(node.id);
    if (point) ports.set(`${node.id}::repeat`, { ...point, x: point.x + point.w / 4, w: 0 });
  }
  const obstacles = new RectangleIndex(routing.values(), cardRectangle);
  const labels = new RectangleIndex([...routing.values()].map(cardRectangle));
  const frames = scene.layout.frames.map(frame => ({ ...frame, x: frame.x + dx, y: frame.y + dy })).sort((a, b) => b.depth - a.depth);
  const controls = new Map((scene.layout.controlScopes || []).map(scope => [scope.id, { ...scope, x: scope.x + dx, y: scope.y + dy }]));
  const loops = new Map(), methods = new Map(), loopLabels = new Set();
  for (const scope of controls.values()) if (scope.loop) for (const target of scope.continueTargets) loops.set(target, scope);
  for (const node of scene.visuals) methods.set(node.id, frames.find(frame => node.id === frame.id || node.ownerId === frame.id || node.id.startsWith(frame.id + '::')));
  const fallback = implementationEdgeRouter(routing, [], [], { horizontal: true, customPorts: true, labelIndex: labels, portPositions: ports });
  let bounds = { x: Infinity, y: Infinity, width: 0, height: 0 };
  for (const point of routing.values()) {
    if (!Number.isFinite(bounds.x)) bounds = { x: point.x - point.w / 2, y: point.y - point.h / 2, width: point.w, height: point.h };
    else bounds = includeRouteBounds(bounds, `M ${point.x - point.w / 2} ${point.y - point.h / 2} L ${point.x + point.w / 2} ${point.y + point.h / 2}`);
  }
  /** Prefer local spines for jumps; a blocked preference still uses the shared obstacle-aware grid. */
  const route = edge => {
    const a = edge.sourcePort == null ? routing.get(edge.source) : ports.get(`${edge.source}::port:${edge.sourcePort}`);
    let b = routing.get(edge.target);
    if (!a || !b) return { d: '', caption: null };
    const source = scene.byId.get(edge.source), target = scene.byId.get(edge.target), method = methods.get(edge.target) || methods.get(edge.source);
    const loop = loops.get(edge.target), control = controls.get(source?.controlId);
    const loopBody = source?.kind === 'loop' && edge.sequenceKind === 'branch' && !edge.loopExit;
    const branchBody = source?.kind === 'branch' && target?.kind !== 'join' && b.x > a.x;
    const merge = target?.kind === 'join' && controls.get(target.controlId);
    let options, preferred = false;
    if (edge.choiceExit) {
      if (b.y - b.h / 2 >= source.y + source.h / 2 + dy) {
        // The upper result takes the outer gutter, so it cannot cross the lower
        // result before both merge in the free gap above the next source step.
        const gutter = a.x + a.w / 2 + 14 + (1 - edge.sourcePort) * 8, corridor = b.y - b.h / 2 - 14;
        options = { sourceSide: 'right', targetSide: 'top', via: [{ x: gutter, y: a.y },
          { x: gutter, y: corridor }, { x: b.x, y: corridor }] };
      } else if (loop && target?.kind === 'loop') {
        b = ports.get(`${edge.target}::repeat`);
        options = { sourceSide: 'right', targetSide: 'bottom' };
      } else options = { sourceSide: 'right', targetSide: target?.assignmentSelection ? 'top' : 'left' };
      preferred = true;
    } else if (target?.assignmentSelection) {
      options = { sourceSide: b.y - b.h / 2 >= a.y + a.h / 2 ? 'bottom' : 'right', targetSide: 'top' };
      preferred = true;
    } else if (merge && !merge.loop && b.y > a.y) {
      // Skips descend to the top input; completed right-hand arms enter from
      // the right below their occupied band, keeping the next step below.
      const sameColumn = Math.abs(a.x - b.x) < 1;
      options = { sourceSide: 'bottom', targetSide: sameColumn ? 'top' : 'right',
        via: sameColumn ? undefined : [{ x: a.x, y: b.y }, { x: b.x + b.w / 2 + 12, y: b.y }] };
      preferred = true;
    } else if (target?.compactLoopUpdate) {
      // Both ordinary completion and continue reach the same visible top input of the update.
      const middleY = (a.y + a.h / 2 + b.y - b.h / 2) / 2;
      options = { sourceSide: source?.kind === 'loop' ? 'right' : 'bottom', targetSide: 'top',
        via: source?.kind === 'loop' ? undefined : [{ x: a.x, y: middleY }, { x: b.x, y: middleY }] };
      preferred = true;
    } else if (edge.sequenceKind === 'call' && b.x + b.w / 2 < a.x - a.w / 2) {
      options = { sourceSide: 'left', targetSide: 'right' }; preferred = true;
    } else if (['return', 'throw'].includes(edge.sequenceKind) && ['return', 'throw'].includes(source?.kind) && method) {
      const gutter = method.x + method.w - 12;
      options = { sourceSide: 'bottom', targetSide: 'top', via: [{ x: a.x, y: a.y + a.h / 2 + 12 },
        { x: gutter, y: a.y + a.h / 2 + 12 }, { x: gutter, y: b.y - b.h / 2 - 12 }, { x: b.x, y: b.y - b.h / 2 - 12 }] };
      preferred = true;
    } else if (['loop', 'continue'].includes(edge.sequenceKind) && loop) {
      if (target?.kind === 'loop') {
        // Re-enter on the bottom-right, staying to the right of the independent downward false exit.
        b = ports.get(`${edge.target}::repeat`);
        const selfRepeat = source?.kind === 'loop', startX = selfRepeat ? a.x + a.w / 2 + 12 : a.x;
        const startY = Math.max(a.y + a.h / 2, b.y + b.h / 2) + 12;
        options = { sourceSide: selfRepeat ? 'right' : 'bottom', targetSide: 'bottom',
          via: [{ x: startX, y: startY }, { x: b.x, y: startY }, { x: b.x, y: b.y + b.h / 2 + 12 }] };
      } else {
        const gutter = loop.x - 16, startY = a.y + a.h / 2 + 12;
        options = { sourceSide: 'bottom', targetSide: 'left', via: [{ x: a.x, y: startY }, { x: gutter, y: startY },
          { x: gutter, y: b.y }, { x: b.x - b.w / 2 - 12, y: b.y }] };
      }
      preferred = true;
    } else if (edge.sequenceKind === 'data' && method) {
      const gutter = method.x + 12;
      options = { sourceSide: 'left', targetSide: 'left', via: [{ x: gutter, y: a.y }, { x: gutter, y: b.y }] };
      preferred = true;
    } else if (b.x < a.x && b.y > a.y) {
      const corridor = Math.max(a.y + a.h / 2 + 12, control ? control.y + control.h + 12 : b.y - b.h / 2 - 24);
      options = { sourceSide: 'bottom', targetSide: 'left', via: [{ x: a.x, y: corridor },
        { x: b.x - b.w / 2 - 12, y: corridor }, { x: b.x - b.w / 2 - 12, y: b.y }] };
      preferred = true;
    }
    let caption = source?.valueChoices || ['Далее', 'Продолжить'].includes(edge.caption) || (['throw', 'continue'].includes(edge.sequenceKind) || edge.sequenceKind === 'return' && source?.sourceOwnerId === scene.byId.get(edge.target)?.sourceOwnerId) ? null : edge.caption;
    if (edge.sequenceKind === 'loop') {
      if (loopLabels.has(edge.target)) caption = null;
      loopLabels.add(edge.target);
    }
    const candidate = preferred ? '' : curve(a, b, edge);
    let d, label;
    if (candidate && !curveCrossesCards(candidate, obstacles)) {
      // A loop-body label belongs beside its body, not on the separate downward false-exit line.
      d = candidate; label = placeRouteCaption(caption, a, b, [], labels, { preferTarget: loopBody || branchBody });
      if (label) labels.add({ left: label.x - label.width / 2 - 2, right: label.x + label.width / 2 + 2, top: label.y - 14, bottom: label.y + 5 });
    } else {
      const routed = fallback(a, b, caption, { ...options, preferTargetCaption: loopBody || branchBody });
      d = smoothSequenceDetour(routed.d, obstacles); label = routed.caption;
    }
    bounds = includeRouteBounds(bounds, d);
    return { d, caption: label, data: edge.sequenceKind === 'data' };
  };
  route.bounds = () => bounds;
  return route;
}
