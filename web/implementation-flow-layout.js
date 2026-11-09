import { FLOW_CARD } from './flow-card.js';

/** Отдельные тела методов сохраняют lexical controls; связанная цепочка переносится в ограниченные ряды. */

/** Переносит рассчитанный блок, не меняя исходные координаты дерева. */
function shiftBox(box, dx, dy) {
  return {
    placed: box.placed.map(item => ({ ...item, x: item.x + dx, y: item.y + dy })),
    frames: box.frames.map(frame => ({ ...frame, x: frame.x + dx, y: frame.y + dy })),
  };
}

/** Тело метода получает поля; листья условий, выходов и ссылок сохраняют размер карточки. */
function minimumWidth(node, padding) {
  return node.w + (isMethod(node) || (node.children?.length || 0) > 0 ? padding * 2 : 0);
}

/** Выбирает до трёх локальных полос, сохраняя полные размеры карточек. */
function laneCount(children, budget, options) {
  let minimum = 0;
  for (const child of children) minimum = Math.max(minimum, minimumWidth(child, options.padding));
  let columns = Math.min(options.maxColumns, children.length);
  while (columns > 1 && columns * minimum + (columns - 1) * options.columnGap > budget) columns -= 1;
  return Math.max(1, columns);
}

/** Раскладывает только условия, выходы и ссылки текущего метода, сохраняя их структурную вложенность. */
function layoutBody(node, budget, options) {
  const children = node.children || [];
  const header = {
    id: node.id, x: node.w / 2, y: node.h / 2, w: node.w, h: node.h,
    focusId: node.focusId || node.id, reference: Boolean(node.reference),
  };
  if (!children.length) {
    if (isMethod(node)) {
      const width = node.w + options.padding * 2;
      const height = node.h + options.padding * 2;
      return {
        placed: [{ ...header, x: width / 2, y: height / 2 }],
        frames: [{ id: node.id, x: 0, y: 0, w: width, h: height, decision: false }],
        width, height,
      };
    }
    // Свободное место под пустым условием остаётся для стрелки-подсказки существующего renderer.
    return { placed: [header], frames: [], width: node.w, height: node.h + (node.decision ? 40 : 0) };
  }
  const widthBudget = Math.max(budget, minimumWidth(node, options.padding));
  const columns = laneCount(children, widthBudget, options);
  const childBudget = (widthBudget - (columns - 1) * options.columnGap) / columns;
  const rows = [];
  for (let index = 0; index < children.length; index += columns) {
    const boxes = [];
    let rowWidth = 0;
    let rowHeight = 0;
    for (let offset = 0; offset < columns && index + offset < children.length; offset += 1) {
      const box = layoutBody(children[index + offset], childBudget, options);
      if (boxes.length) rowWidth += options.columnGap;
      rowWidth += box.width;
      rowHeight = Math.max(rowHeight, box.height);
      boxes.push(box);
    }
    rows.push({ boxes, width: rowWidth, height: rowHeight });
  }
  let width = minimumWidth(node, options.padding);
  for (const row of rows) width = Math.max(width, row.width);
  const placed = [{ ...header, x: width / 2, y: options.padding + node.h / 2 }];
  const frames = [];
  let y = options.padding + node.h + options.bodyGap;
  for (const row of rows) {
    let x = (width - row.width) / 2;
    for (const box of row.boxes) {
      const moved = shiftBox(box, x, y);
      placed.push(...moved.placed);
      frames.push(...moved.frames);
      x += box.width + options.columnGap;
    }
    y += row.height + options.rowGap;
  }
  const height = y - options.rowGap + options.padding;
  frames.push({ id: node.id, x: 0, y: 0, w: width, h: height, decision: Boolean(node.decision) });
  return { placed, frames, width, height };
}

/**
 * Локальный поток идёт от заголовка вправо к его ветвям. Вложенные условия остаются
 * внутри своей рамки. Широкое поддерево переносится под заголовок, сохраняя вложенность;
 * ветви одного уровня распределяются по двум полосам в пределах бюджета тела метода.
 */
function layoutFlowBody(node, budget, options) {
  const children = node.children || [];
  if (!children.length) return layoutBody(node, budget, options);
  const gap = 64;
  const remaining = budget - node.w - gap - options.padding * 2;
  const childBudget = Math.max(FLOW_CARD.width + options.padding * 2, remaining);
  const boxes = children.map(child => layoutFlowBody(child, childBudget, options));
  const rows = [];
  let bodyWidth = 0, bodyHeight = 0;
  for (const box of boxes) {
    let row = rows.at(-1);
    if (!row || row.boxes.length >= 2 || row.width + options.columnGap + box.width > childBudget) {
      row = { boxes: [], width: 0, height: 0 }; rows.push(row);
    }
    if (row.boxes.length) row.width += options.columnGap;
    row.boxes.push(box); row.width += box.width; row.height = Math.max(row.height, box.height);
  }
  for (const row of rows) { bodyWidth = Math.max(bodyWidth, row.width); bodyHeight += row.height + options.rowGap; }
  bodyHeight -= options.rowGap;
  const beside = node.w + gap + bodyWidth + options.padding * 2 <= budget;
  const width = (beside ? node.w + gap + bodyWidth : Math.max(node.w, bodyWidth)) + options.padding * 2;
  const height = (beside ? Math.max(node.h, bodyHeight) : node.h + options.bodyGap + bodyHeight) + options.padding * 2;
  const placed = [{ id: node.id, x: options.padding + node.w / 2, y: beside ? height / 2 : options.padding + node.h / 2,
    w: node.w, h: node.h, focusId: node.focusId || node.id, reference: Boolean(node.reference) }];
  const frames = [];
  let y = beside ? (height - bodyHeight) / 2 : options.padding + node.h + options.bodyGap;
  for (const row of rows) {
    let x = options.padding + (beside ? node.w + gap : 0);
    for (const box of row.boxes) {
      const shifted = shiftBox(box, x, y); placed.push(...shifted.placed); frames.push(...shifted.frames);
      x += box.width + options.columnGap;
    }
    y += row.height + options.rowGap;
  }
  frames.push({ id: node.id, x: 0, y: 0, w: width, h: height, decision: Boolean(node.decision) });
  return { placed, frames, width, height };
}

/** Реализация вызванного метода — отдельный panel, а не lexical body вызывающего метода. */
function isMethod(node) {
  return !node.decision && !node.exit && !node.reference;
}

/**
 * Отделяет реальные реализации в preorder исходного дерева. Условия/выходы/reference остаются
 * у своего метода; повторные вызовы и циклы уже представлены reference-листьями входного дерева.
 */
function methodPanels(roots, flowCards = false) {
  const panels = [];
  const usedIds = new Set();
  const pending = [...roots];
  while (pending.length) {
    const current = pending.pop();
    usedIds.add(current.id);
    pending.push(...(current.children || []));
  }
  let referenceSequence = 0;
  const referenceTo = (target, sourceId) => {
    let id;
    do {
      referenceSequence += 1;
      id = `${target.id}::ref:${sourceId}:panel-${referenceSequence}`;
    } while (usedIds.has(id));
    usedIds.add(id);
    return { id, focusId: target.id, reference: true, w: flowCards ? FLOW_CARD.width : Math.min(target.w, 200), h: flowCards ? FLOW_CARD.height : 58, children: [] };
  };
  const extract = node => {
    const panel = { node: null, localCount: 0 };
    panels.push(panel);
    const localNode = current => {
      panel.localCount += 1;
      const children = [];
      for (const child of current.children || []) {
        if (isMethod(child)) {
          extract(child);
          children.push(localNode(referenceTo(child, current.id)));
        } else {
          children.push(localNode(child));
        }
      }
      return { ...current, ...(flowCards && current.reference ? { w: FLOW_CARD.width, h: FLOW_CARD.height } : {}), children };
    };
    panel.node = localNode(node);
  };
  for (const root of roots) extract(root);
  return panels;
}

/**
 * Ordered skyline: каждый следующий panel занимает самый низкий свободный участок колонок.
 * Массив и panelIndex сохраняют preorder; высокие тела не оставляют пустоту под соседями.
 * Пространственное соседство не означает последовательность или runtime parallelism.
 */
function placePanels(boxes, width, gap, rowGap) {
  let slotWidth = 1;
  for (const box of boxes) slotWidth = Math.max(slotWidth, box.minimumWidth);
  const pitch = slotWidth + gap;
  const columns = Math.max(1, Math.floor((width + gap) / pitch));
  const heights = Array(columns).fill(0);
  const placed = [];
  for (const box of boxes) {
    const span = Math.min(columns, Math.max(1, Math.ceil((box.width + gap) / pitch)));
    let column = 0;
    let y = Infinity;
    for (let start = 0; start <= columns - span; start += 1) {
      let bottom = 0;
      for (let offset = 0; offset < span; offset += 1) bottom = Math.max(bottom, heights[start + offset]);
      if (bottom < y) {
        column = start;
        y = bottom;
      }
    }
    placed.push({ box, x: column * pitch, y });
    for (let offset = 0; offset < span; offset += 1) heights[column + offset] = y + box.height + rowGap;
  }
  return placed;
}

/**
 * Карта адаптирует общую ширину по площади panels до 6000 px, сохраняя bodyWidth <=1000 px.
 * Явный maxWidth задаёт общий бюджет вместо адаптации. Огромная входная карточка сохраняется.
 * placed — центры карточек; frames — левый верхний угол локальных рамок. Вход не изменяется.
 */
export function implementationFlowLayout(roots, options = {}) {
  const bodyWidth = Math.min(options.bodyWidth ?? options.maxWidth ?? 1000, 1000);
  const settings = {
    maxColumns: Math.max(1, Math.min(3, Math.trunc(options.maxColumns) || 3)),
    columnGap: options.columnGap ?? 24,
    rowGap: options.rowGap ?? 36,
    bodyGap: options.bodyGap ?? 44,
    padding: options.padding ?? 12,
  };
  const horizontal = options.direction === 'flow';
  const panels = methodPanels(roots, horizontal);
  const panelNumbers = new Map(panels.map((panel, index) => [panel.node.id, index + 1]));
  const boxes = [];
  let area = 0;
  for (const panel of panels) {
    // Малое тело не растягиваем до лимита; большой набор controls получает две локальные полосы.
    const minimum = minimumWidth(panel.node, settings.padding);
    const budget = panel.localCount >= 8 ? Math.min(bodyWidth, minimum * 2 + settings.columnGap) : minimum;
    const box = horizontal ? layoutFlowBody(panel.node, bodyWidth, settings) : layoutBody(panel.node, budget, settings);
    box.panelIndex = panelNumbers.get(panel.node.id);
    box.minimumWidth = minimum;
    boxes.push(box);
    area += (box.width + settings.columnGap) * (box.height + settings.rowGap);
  }
  const adaptiveWidth = Math.min(options.maxMapWidth ?? 6000, Math.max(1000, Math.sqrt(area * 1.25)));
  const width = options.maxWidth ?? adaptiveWidth;
  const originX = options.originX ?? 80;
  const originY = options.originY ?? 80;
  const placed = [];
  const frames = [];
  const placements = placePanels(boxes, width, options.panelGap ?? 40, options.rootGap ?? 48);
  for (const { box, x, y } of placements) {
    const moved = shiftBox(box, originX + x, originY + y);
    for (const item of moved.placed) {
      placed.push({
        ...item,
        panelIndex: box.panelIndex,
        targetPanelIndex: item.reference ? panelNumbers.get(item.focusId) : undefined,
      });
    }
    for (const frame of moved.frames) frames.push({ ...frame, panelIndex: box.panelIndex });
  }
  return { placed, frames };
}
