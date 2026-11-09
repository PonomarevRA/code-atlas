/** Popup безопасно отображает исходник, остаётся в карте и закрывается с восстановлением фокуса. */
import assert from 'node:assert/strict';
import { MapTooltip, tooltipPosition } from '../web/map-tooltip.js';
import { classifyCodeNode } from '../web/code-semantics.js';
import { GraphView } from '../web/graph.js';
import { Svg } from './svg_fixture.mjs';

/** Минимальный HTML DOM; установка innerHTML запрещена даже в тесте. */
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.style = {setProperty(name,value){this[name]=value;}}; this.classList = {toggle(){}}; this.dataset = {}; }
  setAttribute(name, value) { this[name] = value; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  contains(element) { return this === element || this.children.some(child => child.contains(element)); }
  getBoundingClientRect() { return { left: 0, top: 0, width: 270, height: 180 }; }
  set innerHTML(_) { throw new Error('Source must remain plain text'); }
}
globalThis.document = { createElement: tag => new Element(tag), createElementNS: (_,tag) => new Element(tag) };
function find(root,name) { if(root.className === name) return root; for(const child of root.children || []) { const match=find(child,name); if(match)return match; } }
const host = new Element('main');
host.getBoundingClientRect = () => ({ left: 40, top: 30, width: 600, height: 360 });
const described = [];
const openers = [];
const tooltip = new MapTooltip(host, (id, anchor) => { described.push(id); openers.push(anchor); });
const model = { id: 'raw:Calculate', label: '<script>Calculate</script>', location: 'Domain/Calculator.cs:14', role: classifyCodeNode({ file: 'Domain/Calculator.cs' }), incoming: 2, outgoing: 3, canDescribe: true };
let focusOptions;
const anchor = {
  getBoundingClientRect: () => ({ left: 550, right: 620, top: 320, width: 70, height: 40 }),
  focus: options => { focusOptions = options; tooltip.show(model, anchor); },
};
tooltip.show(model, anchor);
assert.equal(tooltip.element.hidden, false);
assert.equal(find(tooltip.element,'map-tooltip-title').textContent, model.label);
assert.ok(Number.parseFloat(tooltip.element.style.left) + 270 <= 600);
assert.ok(Number.parseFloat(tooltip.element.style.top) + 180 <= 360);
assert.equal(tooltip.element.style.overflow, 'auto');
const actions = find(tooltip.element,'map-tooltip-actions');
actions.children.find(item => item.className === 'map-tooltip-describe').listeners.click({ stopPropagation() {} });
assert.deepEqual(described, ['raw:Calculate']);
assert.equal(openers[0], anchor);
assert.equal(tooltip.element.hidden, true, 'Detailed popup replaces the hover summary');
tooltip.show(model, anchor);
find(tooltip.element,'map-tooltip-pin').listeners.click();
tooltip.show({ ...model, id: 'other', label: 'Other' }, anchor);
assert.equal(tooltip.model.id, model.id, 'Pinned model survives another hover');
find(tooltip.element,'map-tooltip-close').listeners.click();
assert.equal(tooltip.element.hidden, true, 'Returning focus must not reopen the popup');
assert.deepEqual(focusOptions, { preventScroll: true });
tooltip.show(model, anchor);
assert.equal(tooltip.element.hidden, false, 'Next intentional hover opens again');
const originalModel = tooltip.model;
find(tooltip.element,'map-tooltip-collapse').listeners.click();
assert.equal(tooltip.body.hidden, true);
assert.equal(tooltip.pinned, true, 'Collapsed header stays reachable after hover-out');
tooltip.hide(); assert.equal(tooltip.element.hidden, false);
assert.equal(find(tooltip.element,'map-tooltip-collapse')['aria-expanded'], 'false');
assert.equal(find(tooltip.element,'map-tooltip-collapse').title, 'Развернуть сведения');
find(tooltip.element,'map-tooltip-expand').listeners.click();
assert.equal(tooltip.body.hidden, false);
assert.equal(tooltip.expanded, true);
assert.equal(tooltip.model, originalModel, 'Window controls retain the actual raw model');
assert.equal(find(tooltip.element,'map-tooltip-expand')['aria-pressed'], 'true');
assert.equal(find(tooltip.element,'map-tooltip-expand').title, 'Вернуть компактный размер');
find(tooltip.element,'map-tooltip-expand').listeners.click();
assert.equal(tooltip.expanded, false);
find(tooltip.element,'map-tooltip-pin').listeners.click();
assert.equal(tooltip.pinned, false, 'Pin icon can release the explicit window-control pin');
tooltip.element.listeners.keydown({ key: 'Escape', preventDefault() {}, stopPropagation() {} });
assert.equal(tooltip.element.hidden, true, 'Escape closes after focus restoration');
tooltip.show({ ...model, canDescribe: false }, anchor);
assert.ok(!find(tooltip.element,'map-tooltip-describe'));
tooltip.hide(true);
assert.deepEqual(tooltipPosition({ left: 0, top: 0, width: 400, height: 300 }, { left: 340, right: 380, top: 290 }, { width: 200, height: 100 }), { left: 190, top: 178, maxHeight: 100, placement: 'above' });
// Геометрический контракт: popup не пересекает карточку даже при малом экране и высоком содержимом.
const area = {left: 40, top: 30, width: 600, height: 500};
const nearTop = {left: 100, right: 300, top: 45, bottom: 155};
assert.equal(tooltipPosition(area, nearTop, {width: 300, height: 200}).placement, 'below');
const centeredCard = {left: 100, right: 300, top: 250, bottom: 350};
const constrained = tooltipPosition(area, centeredCard, {width: 300, height: 400});
assert.equal(constrained.placement, 'above'); assert.equal(constrained.maxHeight, 198);
assert.equal(tooltipPosition(area, {left: 100, right: 300, top: 35, bottom: 520}, {width: 300, height: 400}), null);
assert.equal(tooltipPosition(area, {left: 100, right: 300, top: 550, bottom: 650}, {width: 300, height: 200}), null);
for (const width of [240, 600, 1200]) {
  for (const height of [220, 500, 900]) {
    for (let y = 0; y < height; y += 17) {
      const box = {left: 70, top: 45, width, height};
      const card = {left: 80, right: 220, top: 45 + y, bottom: 45 + y + 100};
      const position = tooltipPosition(box, card, {width: Math.min(300, width - 20), height: 350});
      if (!position) continue;
      const top = position.top + box.top, bottom = top + position.maxHeight;
      assert.ok(bottom <= card.top - 12 || top >= card.bottom + 12, 'Anchor and all card actions stay uncovered');
      assert.ok(position.top >= 10 && position.top + position.maxHeight <= height - 10);
      assert.ok(position.left >= 10 && position.left + Math.min(300, width - 20) <= width - 10);
    }
  }
}
// Панель следует за реальной экранной карточкой при изменении transform.
let movingTop = 300;
const movingAnchor = {getBoundingClientRect: () => ({left: 100, right: 250, top: movingTop, bottom: movingTop + 40})};
tooltip.show(model, movingAnchor); assert.equal(tooltip.element.dataset.placement, 'above');
movingTop = 50; tooltip.reposition(); assert.equal(tooltip.element.dataset.placement, 'below');
movingAnchor.isConnected = false; tooltip.reposition(); assert.equal(tooltip.element.hidden, true);


// Раскрытое окружение наследует действие описания и один popup основного renderer.
globalThis.document.createElementNS = (_, tag) => new Svg(tag);
globalThis.ResizeObserver = class { observe() {} };
Svg.prototype.getBoundingClientRect = () => ({ left: 180, right: 220, top: 100, width: 40, height: 40 });
const svg = new Svg('svg'), viewport = new Svg('g');
viewport.setAttribute('id', 'viewport'); svg.append(viewport); svg.parentElement = host;
const view = new GraphView(svg, () => {}, () => {});
view.onDescribeNode = (id, anchor) => { described.push(id); openers.push(anchor); };
view.render([{ id: 'caller', label: 'Caller', file: 'Domain/Caller.cs' }], [], null);
view.renderExpansion({ nodes: [{ id: 'callee', label: 'Callee', file: 'Domain/Callee.cs' }], edges: [], selectedId: 'callee' });
assert.equal(view.expansion.child.onDescribeNode, view.onDescribeNode);
const camera = { ...view.camera };
view.expansion.child.nodeElements.get('callee').group.listeners.mouseenter();
assert.equal(view.mapTooltip, view.expansion.child.mapTooltip);
const descriptionAction = find(view.mapTooltip.element,'map-tooltip-describe');
descriptionAction.listeners.click({ stopPropagation() {} });
assert.equal(described.at(-1), 'callee');
assert.equal(openers.at(-1), view.expansion.child.nodeElements.get('callee').group);
assert.equal(view.mapTooltip.element.hidden, true, 'Expanded-child description closes the shared hover popup');
assert.deepEqual(view.camera, camera, 'Requesting description keeps the map camera');
view.clearExpansion();
assert.equal(view.mapTooltip.element.hidden, true, 'Collapsing context closes its popup');
console.log('Map tooltip source safety, raw actions, viewport bounds, pin and Escape focus PASS');
// Полная сигнатура/описание поля остаются обычным текстом, без нерелевантного перехода к параметрам метода.
tooltip.show({...model,canDescribe:false,fieldFacts:{type:'IReadOnlyList<string?>',nullable:false,description:'<script>Field documentation</script>'}},anchor);
assert.ok(tooltip.body.children.some(node=>node.textContent==='IReadOnlyList<string?> · NOT NULL'));
assert.ok(tooltip.body.children.some(node=>node.textContent==='<script>Field documentation</script>'));
assert.ok(!find(tooltip.element,'map-tooltip-describe'));
tooltip.hide(true);
console.log('Field hover full type, declared nullability and safe documentation PASS');
// Наблюдение за transform поддерживает тот же инвариант при pan/zoom и отключается после закрытия.
let transformObserver;
globalThis.MutationObserver = class {
  constructor(callback) { this.callback = callback; this.disconnected = false; transformObserver = this; }
  observe(target, options) { this.target = target; this.options = options; }
  disconnect() { this.disconnected = true; }
};
movingTop = 300; movingAnchor.isConnected = true;
tooltip.show(model, movingAnchor);
assert.deepEqual(transformObserver.options.attributeFilter, ['transform']);
assert.equal(transformObserver.target, host);
movingTop = 50; transformObserver.callback();
assert.equal(tooltip.element.dataset.placement, 'below');
tooltip.hide(true); assert.equal(transformObserver.disconnected, true);
console.log('Tooltip placement above/below, bounded scrolling, transformed anchor and observer cleanup PASS');
