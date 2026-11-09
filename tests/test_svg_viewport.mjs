import assert from 'node:assert/strict';
import { Svg } from './svg_fixture.mjs';
import { SvgViewport, cameraWindow, intersectsWindow, resetSvgViewport, withFullSvgScene } from '../web/svg-viewport.js';

// Этот DOM измеряет известные прямоугольники и учитывает перенос самого SVG-элемента.
const document = { activeElement: null };
let measurements = 0;
const identity = { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0, inverse() { return this; }, multiply(other) { return other; } };
class Element extends Svg {
  constructor(tag, box = { x: 0, y: 0, width: 0, height: 0 }, translation = 0) {
    super(tag); this.tagName = tag; this.box = box; this.translation = translation; this.ownerDocument = document;
  }
  get parentNode() { return this.parent; }
  get nextSibling() { return this.parent?.children[this.parent.children.indexOf(this) + 1] || null; }
  getBBox() { measurements++; return this.box; }
  getCTM() { return { ...identity, e: this.translation }; }
  contains(element) { return element === this || this.children.some(child => child.contains(element)); }
  remove() { super.remove(); this.parent = null; }
  insertBefore(element, next) {
    if (element.parent) element.remove();
    const index = next ? this.children.indexOf(next) : this.children.length;
    assert(index >= 0);
    this.children.splice(index, 0, element); element.parent = this;
  }
}
const svg = new Element('svg'), viewport = new Element('g');
svg.append(viewport); svg.clientWidth = 1000; svg.clientHeight = 600;
const view = { svg, viewport, camera: { x: 0, y: 0, scale: 1 } };
view.viewportWindow = new SvgViewport(view);
const definitions = new Element('defs'); viewport.append(definitions);
const cards = [];
for (let i = 0; i < 300; i++) {
  const card = new Element('g', { x: 0, y: i * 180, width: 240, height: 100 }, 50);
  card.setAttribute('id', `card-${i}`); card.addEventListener('click', () => i);
  cards.push(card); viewport.append(card);
}
// Оба конца связи за экраном; её кривая пересекает видимую область.
const crossing = new Element('path', { x: -5000, y: 250, width: 10000, height: 0 }); viewport.append(crossing);
const unknown = new Element('g'); unknown.getBBox = () => { throw new Error('unsupported geometry'); }; viewport.append(unknown);
view.viewportWindow.update();
assert(viewport.children.length < 12);
assert(viewport.children.includes(definitions) && viewport.children.includes(crossing) && viewport.children.includes(unknown));
assert.equal(view.viewportWindow.entries.length, 303);
assert.equal(cards[100].parentNode, null);
const readCount = measurements;
view.camera.y = -18000; view.viewportWindow.update();
assert(viewport.children.includes(cards[100])); assert(!viewport.children.includes(cards[0]));
assert.equal(cards[100].listeners.click(), 100, 'Возвращается тот же элемент с теми же обработчиками');
assert.equal(measurements, readCount, 'Панорамирование не измеряет SVG повторно');
const visible = [...viewport.children];
view.viewportWindow.update(); assert.deepEqual(viewport.children, visible, 'Порядок слоёв стабилен');
document.activeElement = cards[100]; view.camera.y = 0; view.viewportWindow.update();
assert(viewport.children.includes(cards[100]), 'Элемент с клавиатурным фокусом не демонтируется');
document.activeElement = null; view.viewportWindow.update(); assert(!viewport.children.includes(cards[100]));
const beforeExport = [...viewport.children], camera = { ...view.camera };
view.expansion = { frame: {} }; view.viewportWindow.update();
assert.equal(viewport.children.length, 303, 'Контекстная мини-карта сохраняет полную базовую сцену');
view.expansion = null; view.viewportWindow.update();
assert.deepEqual(viewport.children, beforeExport);
assert.throws(() => withFullSvgScene(svg, () => {
  assert.equal(viewport.children.length, 303);
  assert.deepEqual(viewport.children.slice(1, 301), cards);
  throw new Error('export failed');
}), /export failed/);
assert.deepEqual(viewport.children, beforeExport); assert.deepEqual(view.camera, camera);
svg.clientHeight = 1800; view.viewportWindow.update(); assert(viewport.children.includes(cards[10]), 'Resize расширяет окно');
view.camera.scale = .02; view.viewportWindow.update(); assert(viewport.children.includes(cards[299]), 'Общий обзор показывает все попавшие в кадр элементы');
view.camera = { x: -1500, y: 0, scale: 1 }; view.viewportWindow.update(); assert(!viewport.children.includes(cards[0]), 'Собственный transform элемента учтён');
view.viewportWindow.release(); assert.equal(viewport.children.length, 303, 'Перед переносом контекста возвращается полная сцена');
resetSvgViewport(view); const replacement = new Element('g', { x: 0, y: 0, width: 100, height: 100 }); viewport.append(replacement);
await Promise.resolve(); assert.equal(view.viewportWindow.entries.length, 1, 'Смена сцены освобождает старый индекс');
assert.deepEqual(cameraWindow({ x: 40, y: -20, scale: 2 }, 1000, 600, 0), { x: -20, y: 10, width: 500, height: 300 });
assert.equal(cameraWindow({ x: 0, y: 0, scale: 0 }, 1, 1), null);
assert.equal(cameraWindow({ x: NaN, y: 0, scale: 1 }, 1, 1), null);
assert(intersectsWindow({ x: 10, y: 0, width: 0, height: 10 }, { x: 0, y: 0, width: 10, height: 10 }));
view.camera = { x: 0, y: 0, scale: 1 }; view.viewportWindow.update();
replacement.box.x = 10000; view.viewportWindow.refreshBounds();
assert.equal(replacement.parentNode, null, 'Изменение геометрии обновляет индекс');
assert.equal(withFullSvgScene(svg, () => viewport.children.includes(replacement)), true, 'Экспорт работает даже при пустом экранном слое');
console.log('SVG viewport pan, zoom, resize, crossing edges, focus, full export and scene reset PASS');
