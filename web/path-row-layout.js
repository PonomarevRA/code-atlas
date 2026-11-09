/**
 * Собирает путь в блоки методов, как тело функции.
 * Прямые вызовы цикла группируются в короткие ряды в пределах текущей ширины.
 * Следующие зависимости переходят в короткие ряды в пределах ширины родителя.
 * Повторный вызов остаётся короткой ссылкой, а не второй копией блока.
 */

/** Метод-владелец синтетического id ветви или выхода. Для обычного шага это он сам. */
export function methodOwnerId(id) {
  const text = String(id);
  const branch = text.indexOf('::branch:');
  if (branch >= 0) return text.slice(0, branch);
  const exit = text.indexOf('::exit:');
  if (exit >= 0) return methodOwnerId(text.slice(0, exit));
  return text;
}

/** Уникальные id в порядке чтения пути. */
function idsInOrder(ids, rank) {
  const seen = new Set();
  const unique = [];
  for (const id of ids) {
    if (seen.has(id)) continue;
    seen.add(id);
    unique.push(id);
  }
  unique.sort((left, right) => (rank.get(left) ?? 0) - (rank.get(right) ?? 0));
  return unique;
}

/** Сохраняет вложенность условий одного метода; неоднозначные и циклические связи не создают второй родительский блок. */
function decisionParents(decisionIds, outgoing, byId, rank) {
  const decisions = new Set(decisionIds);
  const parents = new Map();
  for (const parent of decisionIds) {
    for (const child of idsInOrder(outgoing.get(parent) || [], rank)) {
      if (!byId.get(child)?.decision || !decisions.has(child) || parents.has(child)) continue;
      let ancestor = parent;
      while (ancestor !== child && parents.has(ancestor)) ancestor = parents.get(ancestor);
      if (ancestor !== child) parents.set(child, parent);
    }
  }
  return parents;
}

/** Число структурных родителей определяет ближайшую ветвь для вызова с дублированными рёбрами. */
function decisionDepth(id, parents) {
  let depth = 0;
  while (parents.has(id)) {
    id = parents.get(id);
    depth += 1;
  }
  return depth;
}

/** Отдаёт вызовы и выходы самой глубокой ветви; повторные вызовы сохраняет как ссылки. */
function branchTargetsOf(decisionIds, parents, outgoing, byId, rank) {
  const ownerOf = new Map();
  const depths = new Map();
  for (const id of decisionIds) depths.set(id, decisionDepth(id, parents));
  for (const id of decisionIds) {
    for (const target of outgoing.get(id) || []) {
      if (!byId.has(target) || byId.get(target).decision) continue;
      const owner = ownerOf.get(target);
      if (!owner || depths.get(id) > depths.get(owner)) ownerOf.set(target, id);
    }
  }
  const targetsByDecision = new Map();
  for (const id of decisionIds) {
    const targets = [];
    for (const target of idsInOrder(outgoing.get(id) || [], rank)) {
      const node = byId.get(target);
      if (!node) continue;
      if (node.decision) {
        if (parents.get(target) === id) targets.push({ id: target, kind: 'decision' });
        continue;
      }
      if (node.exit) {
        if (ownerOf.get(target) === id) targets.push({ id: target, kind: 'exit' });
        continue;
      }
      targets.push({ id: target, kind: ownerOf.get(target) === id ? 'method' : 'ref' });
    }
    targetsByDecision.set(id, targets);
  }
  return { targetsByDecision, ownedIds: new Set(ownerOf.keys()) };
}

/** Выход return/throw и сводка пропусков являются листьями внутри метода или условия. */
function exitLeaf(node) {
  return { id: node.id, w: node.w, h: node.h, exit: true, children: [] };
}

/**
 * Дерево виртуальных блоков: условия вложены по структурным рёбрам одного метода,
 * вызовы принадлежат ближайшему условию, выходы остаются листьями своих владельцев.
 * Повторные вызовы и рекурсия заменяются короткими ссылками.
 * @param {Array<{id:string, w:number, h:number, decision?:boolean, exit?:boolean}>} nodes
 * @param {Array<{source:string, target:string}>} edges
 * @param {string[]} order порядок чтения
 * @returns {Array<{id:string, w:number, h:number, decision?:boolean, reference?:boolean, focusId?:string, children:Array}>}
 */
export function buildMethodFrameTree(nodes, edges, order) {
  const byId = new Map(nodes.map(node => [node.id, node]));
  const rank = new Map(order.map((id, index) => [id, index]));
  const outgoing = new Map();
  for (const edge of edges) {
    if (!byId.has(edge.source) || !byId.has(edge.target)) continue;
    const list = outgoing.get(edge.source);
    if (list) list.push(edge.target);
    else outgoing.set(edge.source, [edge.target]);
  }
  const decisionsOf = new Map();
  for (const node of nodes) {
    if (!node.decision) continue;
    const owner = methodOwnerId(node.id);
    const list = decisionsOf.get(owner);
    if (list) list.push(node.id);
    else decisionsOf.set(owner, [node.id]);
  }
  const claimed = new Set();
  const stack = new Set();
  let referenceCount = 0;

  const referenceTo = (targetId, sourceId) => {
    const target = byId.get(targetId);
    referenceCount += 1;
    return {
      id: `${targetId}::ref:${sourceId}:${referenceCount}`,
      focusId: targetId,
      w: Math.min(target?.w ?? 180, 200),
      h: 58,
      reference: true,
      children: [],
    };
  };

  const placeMethod = (target, sourceId) => {
    if (claimed.has(target) || stack.has(target)) return referenceTo(target, sourceId);
    return buildMethod(target);
  };

  // Дерево структурных родителей уже очищено от циклов; ссылки на методы обрабатываются отдельно.
  const buildDecision = (id, targetsByDecision) => {
    const node = byId.get(id);
    claimed.add(id);
    const children = [];
    for (const target of targetsByDecision.get(id) || []) {
      const targetNode = byId.get(target.id);
      if (target.kind === 'exit') {
        if (claimed.has(target.id)) continue;
        claimed.add(target.id);
        children.push(exitLeaf(targetNode));
        continue;
      }
      if (target.kind === 'decision') {
        if (!claimed.has(target.id)) children.push(buildDecision(target.id, targetsByDecision));
        continue;
      }
      if (target.kind === 'ref') {
        children.push(referenceTo(target.id, id));
        continue;
      }
      children.push(placeMethod(target.id, id));
    }
    return { id, w: node.w, h: node.h, decision: true, kind: node.kind, children };
  };

  const buildMethod = id => {
    const node = byId.get(id);
    if (stack.has(id)) return referenceTo(id, id);
    stack.add(id);
    claimed.add(id);
    const decisionIds = idsInOrder(decisionsOf.get(id) || [], rank);
    const parents = decisionParents(decisionIds, outgoing, byId, rank);
    // Вызов из вложенного if остаётся там, даже при дублированном ребре от метода или цикла.
    const { targetsByDecision, ownedIds } = branchTargetsOf(decisionIds, parents, outgoing, byId, rank);
    const topLevelDecisions = decisionIds.filter(decisionId => !parents.has(decisionId));
    const directCalls = [];
    for (const target of idsInOrder(outgoing.get(id) || [], rank)) {
      const targetNode = byId.get(target);
      if (!targetNode || targetNode.decision || ownedIds.has(target)) continue;
      directCalls.push(target);
    }
    const children = [];
    for (const childId of idsInOrder([...topLevelDecisions, ...directCalls], rank)) {
      if (targetsByDecision.has(childId)) {
        if (!claimed.has(childId)) children.push(buildDecision(childId, targetsByDecision));
        continue;
      }
      if (byId.get(childId).exit) {
        if (!claimed.has(childId)) {
          claimed.add(childId);
          children.push(exitLeaf(byId.get(childId)));
        }
        continue;
      }
      children.push(placeMethod(childId, id));
    }
    stack.delete(id);
    return { id, w: node.w, h: node.h, children };
  };

  const roots = [];
  for (const id of order) {
    const node = byId.get(id);
    if (!node || node.decision || node.exit || claimed.has(id)) continue;
    roots.push(buildMethod(id));
  }
  return roots;
}

const LOOP_KINDS = new Set(['for', 'foreach', 'while', 'do']);

/** Упаковывает вызовы в ряды максимум по три блока, сохраняя исходный порядок. */
function wrapRows(boxes, budget, gap) {
  const rows = [[]];
  let rowWidth = 0;
  for (const box of boxes) {
    const row = rows[rows.length - 1];
    const next = row.length ? rowWidth + gap + box.width : box.width;
    if (row.length && (next > budget || row.length >= 3)) {
      rows.push([box]);
      rowWidth = box.width;
    } else {
      row.push(box);
      rowWidth = next;
    }
  }
  return rows;
}

/** Циклы группируют прямые вызовы; if/switch оставляют альтернативы отдельными строками. */
function placeChildren(node, header, childBoxes, { budget, gap }) {
  if (LOOP_KINDS.has(node.kind)) return wrapRows([header, ...childBoxes], budget, gap);

  const firstRow = [header];
  const dependencies = [];
  for (let index = 0; index < childBoxes.length; index += 1) {
    const box = childBoxes[index];
    if (index === 0 && header.width + gap + box.width <= budget) firstRow.push(box);
    else dependencies.push(box);
  }
  if (!dependencies.length) return [firstRow];
  if (node.decision) return [firstRow, ...dependencies.map(box => [box])];
  return [firstRow, ...wrapRows(dependencies, budget, gap)];
}

/** Лист хранит только собственную карточку; потомки получают локальные смещения отдельно. */
function leafBox(node) {
  return { node, children: [], width: node.w, height: node.h, framed: false };
}

/** Считает максимальную исходную ширину снизу вверх без рекурсии и копирования потомков. */
function collectIntrinsicWidths(roots) {
  const widths = new Map(), order = [], pending = [...roots];
  while (pending.length) {
    const node = pending.pop();
    order.push(node);
    for (const child of node.children || []) pending.push(child);
  }
  for (let index = order.length - 1; index >= 0; index--) {
    const node = order[index];
    let width = node.w;
    for (const child of node.children || []) width = Math.max(width, widths.get(child));
    widths.set(node, width);
  }
  return widths;
}

/** Выделяет детям ширину колонки, если исходные карточки допускают два или три столбца. */
function childWidthBudget(node, children, budget, gap, intrinsicWidths) {
  if (node.decision && !LOOP_KINDS.has(node.kind)) return budget;
  let minimumWidth = 0;
  for (const child of children) minimumWidth = Math.max(minimumWidth, intrinsicWidths.get(child));
  for (let columns = Math.min(3, children.length); columns >= 2; columns -= 1) {
    const columnWidth = (budget - gap * (columns - 1)) / columns;
    if (columnWidth >= minimumWidth) return columnWidth;
  }
  return budget;
}

/**
 * Собирает вложенные рамки в пределах переданной внешней ширины, в пикселях.
 * Ширина детей ограничена содержимым родителя. Отступы уменьшаются с глубиной,
 * чтобы даже длинная цепочка не накапливала десятки одинаковых боковых отступов.
 * Слишком широкая исходная карточка остаётся целой и может превысить бюджет.
 */
function layoutNode(root, options, outerBudget, intrinsicWidths) {
  const pending = [{ node: root, outerBudget, depth: 0 }];
  const order = [], boxes = new Map();
  // Бюджет зависит от родителя, а размер — от детей: два прохода разделяют эти зависимости.
  while (pending.length) {
    const item = pending.pop(), node = item.node, children = node.children || [];
    if (!children.length) { boxes.set(node, leafBox(node)); continue; }
    const gap = options.childGap ?? 24;
    const requestedPad = Math.floor((options.pad ?? 16) / (2 ** item.depth));
    const availablePad = Math.max(0, (item.outerBudget - intrinsicWidths.get(node)) / 2);
    const pad = Math.min(requestedPad, Math.floor(availablePad));
    const budget = Math.max(0, item.outerBudget - pad * 2);
    order.push({ node, pad, budget, gap });
    const childBudget = childWidthBudget(node, children, budget, gap, intrinsicWidths);
    for (const child of children) pending.push({ node: child, outerBudget: childBudget, depth: item.depth + 1 });
  }
  const rowGap = options.rowGap ?? 28;
  for (let index = order.length - 1; index >= 0; index--) {
    const { node, pad, budget, gap } = order[index];
    const childBoxes = [];
    for (const child of node.children) childBoxes.push(boxes.get(child));
    const rows = placeChildren(node, leafBox(node), childBoxes, { budget, gap });
    const rowWidths = [], rowHeights = [];
    for (const row of rows) {
      let width = 0, height = 0;
      for (const box of row) {
        if (width > 0) width += gap;
        width += box.width;
        height = Math.max(height, box.height);
      }
      rowWidths.push(width); rowHeights.push(height);
    }
    const width = Math.max(...rowWidths);
    const rowsHeight = rowHeights.reduce((sum, height, index) => sum + height + (index ? rowGap : 0), 0);
    const children = [];
    let y = pad;
    for (let rowIndex = 0; rowIndex < rows.length; rowIndex++) {
      let x = pad;
      for (const box of rows[rowIndex]) {
        children.push({ box, x, y });
        x += box.width + gap;
      }
      y += rowHeights[rowIndex] + rowGap;
    }
    boxes.set(node, { node, children, framed: true, width: width + pad * 2,
      height: rowsHeight + pad * 2 });
  }
  return boxes.get(root);
}

/** Выпускает карточки до потомков, рамки после потомков; каждый объект создаётся один раз. */
function flattenBox(root, x, y, placed, frames) {
  const pending = [{ box: root, x, y }];
  while (pending.length) {
    const item = pending.pop(), { box } = item, node = box.node;
    if (item.frame) {
      frames.push({ id: node.id, x: item.x, y: item.y, w: box.width, h: box.height,
        decision: Boolean(node.decision) });
    } else if (!box.framed) {
      placed.push({ id: node.id, x: node.w / 2 + item.x, y: node.h / 2 + item.y,
        w: node.w, h: node.h, focusId: node.focusId || node.id, reference: Boolean(node.reference) });
    } else {
      pending.push({ ...item, frame: true });
      for (let index = box.children.length - 1; index >= 0; index--) {
        const child = box.children[index];
        pending.push({ box: child.box, x: child.x + item.x, y: child.y + item.y });
      }
    }
  }
}

/**
 * Ставит корни пути друг под другом. Координаты placed — центры, frames — левый верхний угол.
 * innerBudget задаёт ширину содержимого корня в пикселях: его внешняя ширина
 * не превышает innerBudget + 2 * pad, кроме исходных карточек шире бюджета.
 * По умолчанию внешняя ширина ограничена 1000 px (968 + 2 * 16).
 * @param {Array<{id:string, w:number, h:number, children?:Array}>} roots
 * @param {{originX?:number, originY?:number, rootGap?:number, innerBudget?:number, pad?:number, childGap?:number, rowGap?:number}} [options]
 * @returns {{placed:Array, frames:Array}} карточки и вложенные рамки в координатах графа
 */
export function packMethodFrames(roots, options = {}) {
  const originX = options.originX ?? 80;
  const originY = options.originY ?? 80;
  const rootGap = options.rootGap ?? 48;
  const outerBudget = (options.innerBudget ?? 968) + 2 * (options.pad ?? 16);
  const intrinsicWidths = collectIntrinsicWidths(roots);
  const placed = [];
  const frames = [];
  let y = originY;
  for (const root of roots) {
    const box = layoutNode(root, options, outerBudget, intrinsicWidths);
    flattenBox(box, originX, y, placed, frames);
    y += box.height + rootGap;
  }
  return { placed, frames };
}
