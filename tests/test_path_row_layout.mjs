/** Блок метода держит if и вызовы внутри себя; повторный вызов остаётся ссылкой. */
import assert from 'node:assert/strict';
import { buildMethodFrameTree, packMethodFrames } from '../web/path-row-layout.js';

/** Центр и края карточки лежат внутри рамки. */
function assertInside(frame, card) {
  const left = card.x - card.w / 2;
  const right = card.x + card.w / 2;
  const top = card.y - card.h / 2;
  const bottom = card.y + card.h / 2;
  assert.ok(left >= frame.x - 0.1, card.id);
  assert.ok(right <= frame.x + frame.w + 0.1, card.id);
  assert.ok(top >= frame.y - 0.1, card.id);
  assert.ok(bottom <= frame.y + frame.h + 0.1, card.id);
}

/** Рамка целиком лежит внутри другой рамки. */
function assertFrameInside(outer, inner) {
  assert.ok(inner.x >= outer.x - 0.1);
  assert.ok(inner.y >= outer.y - 0.1);
  assert.ok(inner.x + inner.w <= outer.x + outer.w + 0.1);
  assert.ok(inner.y + inner.h <= outer.y + outer.h + 0.1);
}

/** if и вызванный метод остаются в блоке вызывающего, а ветвь if — ещё и в блоке условия. */
function ifAndCalleeStayInsideMethod() {
  const nodes = [
    { id: 'calc', w: 250, h: 96 },
    { id: 'calc::branch:12:0', w: 150, h: 72, decision: true },
    { id: 'apply', w: 250, h: 96 },
  ];
  const edges = [
    { source: 'calc', target: 'calc::branch:12:0' },
    { source: 'calc::branch:12:0', target: 'apply' },
  ];
  const roots = buildMethodFrameTree(nodes, edges, ['calc', 'calc::branch:12:0', 'apply']);
  assert.equal(roots.length, 1);
  assert.equal(roots[0].id, 'calc');
  assert.equal(roots[0].children[0].id, 'calc::branch:12:0');
  assert.equal(roots[0].children[0].children[0].id, 'apply');
  const { placed, frames } = packMethodFrames(roots);
  const byId = new Map(placed.map(item => [item.id, item]));
  const method = frames.find(frame => frame.id === 'calc');
  const decision = frames.find(frame => frame.id === 'calc::branch:12:0');
  assert.equal(decision.decision, true);
  assertInside(method, byId.get('calc::branch:12:0'));
  assertInside(decision, byId.get('apply'));
  assertInside(method, byId.get('apply'));
  assertFrameInside(method, decision);
}

/** Вызов, который уже лежит в if, не дублируется рядом с методом. */
function branchedCallIsNotRepeatedBesideMethod() {
  const nodes = [
    { id: 'calc', w: 200, h: 80 },
    { id: 'calc::branch:1:0', w: 150, h: 72, decision: true },
    { id: 'apply', w: 200, h: 80 },
  ];
  const edges = [
    { source: 'calc', target: 'calc::branch:1:0' },
    { source: 'calc', target: 'apply' },
    { source: 'calc::branch:1:0', target: 'apply' },
  ];
  const roots = buildMethodFrameTree(nodes, edges, ['calc', 'calc::branch:1:0', 'apply']);
  const decision = roots[0].children.find(child => child.decision);
  assert.equal(decision.children[0].id, 'apply');
  assert.equal(roots[0].children.filter(child => child.id === 'apply' || child.focusId === 'apply').length, 0);
}

/** Безусловный return/throw и сводка скрытых шагов остаются листьями внутри метода. */
function directExitsRemainInsideMethod() {
  const exits = ['calc::exit:20:0', 'calc::exit:21:1', 'calc::exit:omissions'];
  const nodes = [{ id: 'calc', w: 220, h: 80 }];
  const edges = [];
  for (const id of exits) {
    nodes.push({ id, w: 200, h: 64, exit: true });
    edges.push({ source: 'calc', target: id });
  }
  const roots = buildMethodFrameTree(nodes, edges, ['calc', ...exits]);
  assert.deepEqual(roots[0].children.map(child => child.id), exits);
  for (const child of roots[0].children) {
    assert.equal(child.exit, true);
    assert.deepEqual(child.children, []);
  }
  const { placed, frames } = packMethodFrames(roots);
  const owner = frames.find(frame => frame.id === 'calc');
  assert.equal(frames.length, 1, 'terminal leaves do not create method frames');
  for (const id of exits) assertInside(owner, placed.find(card => card.id === id));
}

/** Вложенный if принадлежит foreach; дублированные связи не переносят его вызов наружу. */
function nestedDecisionOwnsItsCallee() {
  const loopId = 'calc::branch:10:0';
  const ifId = 'calc::branch:12:1';
  const nodes = [
    { id: 'calc', w: 220, h: 80 },
    { id: loopId, w: 200, h: 70, decision: true, kind: 'foreach' },
    { id: ifId, w: 200, h: 70, decision: true, kind: 'if' },
    { id: 'apply', w: 220, h: 80 },
  ];
  const edges = [
    { source: 'calc', target: loopId },
    { source: 'calc', target: ifId },
    { source: loopId, target: ifId },
    { source: 'calc', target: 'apply' },
    { source: loopId, target: 'apply' },
    { source: ifId, target: 'apply' },
  ];
  const roots = buildMethodFrameTree(nodes, edges, ['calc', loopId, ifId, 'apply']);
  assert.deepEqual(roots[0].children.map(child => child.id), [loopId]);
  const loop = roots[0].children[0];
  const iff = loop.children.find(child => child.id === ifId);
  assert.equal(iff.children[0].id, 'apply');
  assert.ok(loop.children.find(child => child.reference && child.focusId === 'apply'));
  assert.ok(!loop.children.find(child => child.id === 'apply'));
  const { placed, frames } = packMethodFrames(roots);
  const outer = frames.find(frame => frame.id === 'calc');
  const loopFrame = frames.find(frame => frame.id === loopId);
  const ifFrame = frames.find(frame => frame.id === ifId);
  assertFrameInside(outer, loopFrame);
  assertFrameInside(loopFrame, ifFrame);
  assertInside(ifFrame, placed.find(card => card.id === 'apply'));
  assertCardsDoNotOverlap(placed);

  // Повреждённая структурная обратная связь не должна вызвать бесконечную рекурсию.
  const cyclicEdges = [...edges, { source: ifId, target: loopId }];
  const cyclicRoots = buildMethodFrameTree(nodes, cyclicEdges, ['calc', loopId, ifId, 'apply']);
  assert.deepEqual(cyclicRoots, roots);
}

/** Второй вызов уже показанного метода — ссылка внутри вызывающего, без вложенной копии. */
function secondCallIsReferenceInsideCaller() {
  const nodes = [
    { id: 'a', w: 200, h: 80 },
    { id: 'b', w: 200, h: 80 },
    { id: 'shared', w: 220, h: 80 },
  ];
  const edges = [
    { source: 'a', target: 'shared' },
    { source: 'a', target: 'b' },
    { source: 'b', target: 'shared' },
  ];
  const roots = buildMethodFrameTree(nodes, edges, ['a', 'shared', 'b']);
  const caller = roots[0].children.find(child => child.id === 'b');
  const link = caller.children.find(child => child.reference);
  assert.equal(link.focusId, 'shared');
  assert.equal(link.children.length, 0);
  const { placed, frames } = packMethodFrames(roots);
  const box = frames.find(frame => frame.id === 'b');
  assertInside(box, placed.find(item => item.reference));
  assertFrameInside(frames.find(frame => frame.id === 'a'), box);
}

/** Цикл не разворачивает метод второй раз. */
function cycleBecomesReference() {
  const nodes = [
    { id: 'a', w: 80, h: 40 },
    { id: 'b', w: 80, h: 40 },
  ];
  const edges = [
    { source: 'a', target: 'b' },
    { source: 'b', target: 'a' },
  ];
  const roots = buildMethodFrameTree(nodes, edges, ['a', 'b']);
  const link = roots[0].children[0].children[0];
  assert.equal(link.reference, true);
  assert.equal(link.focusId, 'a');
  assert.equal(link.children.length, 0);
}

/** Прямая цепочка делит один ряд. Вторая зависимость метода уходит ниже, ширина ряда не растёт в столбец. */
function callChainStaysOnOneRow() {
  const nodes = [
    { id: 'a', w: 200, h: 80 },
    { id: 'b', w: 200, h: 80 },
    { id: 'c', w: 200, h: 80 },
    { id: 'extra', w: 200, h: 80 },
  ];
  const edges = [
    { source: 'a', target: 'b' },
    { source: 'b', target: 'c' },
    { source: 'a', target: 'extra' },
  ];
  const { placed, frames } = packMethodFrames(buildMethodFrameTree(nodes, edges, ['a', 'b', 'c', 'extra']));
  const byId = new Map(placed.map(item => [item.id, item]));
  const top = card => card.y - card.h / 2;
  // Продолжение b и c стоит в полосе a, а не следующим этажом под карточкой.
  assert.ok(top(byId.get('b')) < top(byId.get('a')) + byId.get('a').h);
  assert.ok(Math.abs(top(byId.get('b')) - top(byId.get('c'))) < 1);
  assert.ok(top(byId.get('extra')) > top(byId.get('a')) + byId.get('a').h);
  const frame = frames.find(item => item.id === 'a');
  assert.ok(frame.w < 980);
  assert.ok(frame.h < byId.get('a').h * 6);
}

/** Прямые вызовы цикла стоят в одном ряду, а вторая детализация вызова уходит вниз. if так не делает. */
function loopCallsShareOneRow() {
  const top = card => card.y - card.h / 2;
  const loop = {
    id: 'm::branch:4:0', w: 150, h: 72, decision: true, kind: 'for',
    children: [
      { id: 'c1', w: 180, h: 80, children: [
        { id: 'd1', w: 180, h: 80, children: [] },
        { id: 'd2', w: 180, h: 80, children: [] },
      ] },
      { id: 'c2', w: 180, h: 80, children: [] },
    ],
  };
  const placed = new Map(packMethodFrames([loop], { originX: 0, originY: 0 }).placed.map(item => [item.id, item]));
  assert.ok(Math.abs(top(placed.get('c1')) - top(placed.get('c2'))) < 24);
  assert.ok(top(placed.get('d2')) > top(placed.get('c1')) + placed.get('c1').h);
  const branch = {
    id: 'if', w: 150, h: 72, decision: true, kind: 'if',
    children: [
      { id: 't', w: 180, h: 80, children: [] },
      { id: 'f', w: 180, h: 80, children: [] },
    ],
  };
  const iff = new Map(packMethodFrames([branch], { originX: 0, originY: 0 }).placed.map(item => [item.id, item]));
  assert.ok(top(iff.get('f')) > top(iff.get('t')) + iff.get('t').h);
}

/** Широкие вызовы: первый остаётся в ряду цикла, остальные уходят вниз внутри блока. */
function wideCallsWrapInsideMethod() {
  const nodes = [
    { id: 'm', w: 100, h: 40 },
    { id: 'c1', w: 400, h: 40 },
    { id: 'c2', w: 400, h: 40 },
    { id: 'c3', w: 400, h: 40 },
  ];
  const edges = [
    { source: 'm', target: 'c1' },
    { source: 'm', target: 'c2' },
    { source: 'm', target: 'c3' },
  ];
  const roots = buildMethodFrameTree(nodes, edges, ['m', 'c1', 'c2', 'c3']);
  const { placed, frames } = packMethodFrames(roots, { innerBudget: 900 });
  const frame = frames.find(item => item.id === 'm');
  const cards = ['c1', 'c2', 'c3'].map(id => placed.find(item => item.id === id));
  for (const card of cards) assertInside(frame, card);
  assert.ok(new Set(cards.map(card => card.y)).size >= 2);
  assert.equal(frames.filter(item => item.id === 'm').length, 1);
}

/** Карточки остаются отдельными областями даже после переноса строк и вложения рамок. */
function assertCardsDoNotOverlap(cards) {
  for (let left = 0; left < cards.length; left += 1) {
    for (let right = left + 1; right < cards.length; right += 1) {
      const a = cards[left];
      const b = cards[right];
      const separatedX = Math.abs(a.x - b.x) >= (a.w + b.w) / 2;
      const separatedY = Math.abs(a.y - b.y) >= (a.h + b.h) / 2;
      assert.ok(separatedX || separatedY, `${a.id} overlaps ${b.id}`);
    }
  }
}

/** Глубина раскрытия не расширяет граф: все рамки вписываются в общий бюджет. */
function deepChainHasBoundedWidth() {
  let root = { id: 'method-79', w: 240, h: 80, children: [] };
  for (let index = 78; index >= 0; index -= 1) {
    root = { id: `method-${index}`, w: 240, h: 80, children: [root] };
  }
  const layout = packMethodFrames([root]);
  const outer = layout.frames.find(frame => frame.id === root.id);
  assert.ok(outer.w <= 1000, `deep chain width is ${outer.w}`);
  assert.equal(layout.placed.length, 80);
  for (const card of layout.placed) assertInside(outer, card);
  for (const frame of layout.frames) assertFrameInside(outer, frame);
  assertCardsDoNotOverlap(layout.placed);
  assert.deepEqual(packMethodFrames([root]), layout, 'packing is deterministic');

  // В узком бюджете подушка вложенных рамок не должна вытеснять карточки за правую границу.
  const narrow = packMethodFrames([root], { innerBudget: 240 });
  const narrowOuter = narrow.frames.find(frame => frame.id === root.id);
  assert.ok(narrowOuter.w <= 272, `narrow chain width is ${narrowOuter.w}`);
  for (const card of narrow.placed) assertInside(narrowOuter, card);
}

/** Обычные зависимости делят короткие ряды; ветви и их тела остаются внутри владельца. */
function wideFanoutUsesColumnsInsideBound() {
  const children = [];
  for (let index = 0; index < 10; index += 1) {
    children.push({
      id: `call-${index}`, w: 220, h: 80,
      children: [{
        id: `call-${index}::branch:1:0`, w: 170, h: 70, decision: true, kind: 'if',
        children: [{ id: `leaf-${index}`, w: 220, h: 80, children: [] }],
      }],
    });
  }
  const root = { id: 'fanout', w: 220, h: 80, children };
  const { placed, frames } = packMethodFrames([root]);
  const outer = frames.find(frame => frame.id === root.id);
  assert.ok(outer.w <= 1000, `fanout width is ${outer.w}`);
  for (const card of placed) assertInside(outer, card);
  for (let index = 0; index < 10; index += 1) {
    const caller = frames.find(frame => frame.id === `call-${index}`);
    const branch = frames.find(frame => frame.id === `call-${index}::branch:1:0`);
    assertFrameInside(outer, caller);
    assertFrameInside(caller, branch);
    assertInside(branch, placed.find(card => card.id === `leaf-${index}`));
  }
  const calls = placed.filter(card => /^call-\d+$/.test(card.id));
  const rowCounts = new Map();
  for (const call of calls) rowCounts.set(call.y, (rowCounts.get(call.y) || 0) + 1);
  assert.ok(Math.max(...rowCounts.values()) >= 2, 'siblings share columns');
  assert.ok(Math.max(...rowCounts.values()) <= 3, 'rows remain easy to scan');
  assertCardsDoNotOverlap(placed);
}

/** Заведомо широкую исходную карточку оставляем целой, без обрезки и лишних отступов. */
function oversizedLeafRemainsUnclipped() {
  const root = {
    id: 'owner', w: 220, h: 80,
    children: [{ id: 'oversized', w: 1400, h: 80, children: [] }],
  };
  const { placed, frames } = packMethodFrames([root]);
  const outer = frames.find(frame => frame.id === root.id);
  const card = placed.find(item => item.id === 'oversized');
  assert.equal(card.w, 1400);
  assert.equal(outer.w, 1400);
  assertInside(outer, card);
}

/** Пустой путь не создаёт ни карточек, ни рамок. */
function emptyPathHasNoFrames() {
  assert.deepEqual(buildMethodFrameTree([], [], []), []);
  assert.deepEqual(packMethodFrames([]), { placed: [], frames: [] });
}

ifAndCalleeStayInsideMethod();
branchedCallIsNotRepeatedBesideMethod();
directExitsRemainInsideMethod();
nestedDecisionOwnsItsCallee();
secondCallIsReferenceInsideCaller();
cycleBecomesReference();
callChainStaysOnOneRow();
loopCallsShareOneRow();
wideCallsWrapInsideMethod();
deepChainHasBoundedWidth();
wideFanoutUsesColumnsInsideBound();
oversizedLeafRemainsUnclipped();
emptyPathHasNoFrames();
console.log('Path row layout PASS');

/** Итеративная упаковка выдерживает глубину, превышающую стек JavaScript, сохраняя порядок. */
function tenThousandNestedFramesRemainReadable() {
  const size = 10000;
  let root = { id: `deep-${size - 1}`, w: 220, h: 80, children: [] };
  for (let index = size - 2; index >= 0; index--) root = { id: `deep-${index}`, w: 220, h: 80, children: [root] };
  const { placed, frames } = packMethodFrames([root]);
  assert.equal(placed.length, size);
  assert.equal(frames.length, size - 1);
  assert.equal(placed[0].id, 'deep-0');
  assert.equal(placed.at(-1).id, `deep-${size - 1}`);
  assert.equal(frames[0].id, `deep-${size - 2}`);
  assert.equal(frames.at(-1).id, 'deep-0');
  const outer = frames.at(-1);
  assert.ok(outer.w <= 1000);
  for (const card of placed) assertInside(outer, card);
}
tenThousandNestedFramesRemainReadable();
