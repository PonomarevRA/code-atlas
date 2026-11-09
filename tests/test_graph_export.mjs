import assert from 'node:assert/strict';
import { exportDimensions, exportFilename, bindGraphExport } from '../web/graph-export.js';
import { createExportNotice } from '../web/export-notice.js';

const normal = exportDimensions({ x: -300, y: -200, width: 800, height: 400 });
assert.equal(normal.x, -332); assert.equal(normal.y, -232);
assert.equal(normal.width, 864); assert.equal(normal.height, 464);
assert.equal(normal.pixelWidth, 1728); assert.equal(normal.pixelHeight, 928);
for (const bounds of [{ x: -70000, y: -60000, width: 150000, height: 90000 },
  { x: 0, y: 0, width: 1000000, height: 1 }, { x: 0, y: 0, width: 1, height: 1000000 }]) {
  const result = exportDimensions(bounds);
  assert(result.pixelWidth > 0 && result.pixelHeight > 0);
  assert(result.pixelWidth <= 8192 && result.pixelHeight <= 8192);
  assert(result.pixelWidth * result.pixelHeight <= 16000000);
  assert(Math.abs(result.pixelWidth - result.width * result.scale) < 1);
  assert(Math.abs(result.pixelHeight - result.height * result.scale) < 1);
}
for (const bounds of [{ x: 0, y: 0, width: 0, height: 50 },
  { x: 0, y: 0, width: 40, height: Infinity }, { x: NaN, y: 0, width: 50, height: 50 }]) {
  assert.throws(() => exportDimensions(bounds), /нет элементов/);
}
assert.equal(exportFilename('Расчёт/CostChange:API'), 'Расчёт-CostChange-API-map.png');
assert.equal(exportFilename(''), 'code-atlas-map.png');
assert.equal(exportFilename('Расчёт', 'svg'), 'Расчёт-map.svg');
assert.throws(() => exportFilename('Test', '../bad'), /формат SVG или PNG/);
// Ошибка пустой карты возвращает управление без блокировки кнопки и без скачивания.
let onClick;
const button = { disabled: false, addEventListener: (_, callback) => { onClick = callback; } };
const status = { hidden: true, addEventListener() {}, append() {}, ownerDocument: { createElement: () => ({ setAttribute() {}, addEventListener() {} }) } };
bindGraphExport({ button, status, svg: { ownerDocument: { defaultView: {} }, querySelector: () => null }, name: () => 'Empty' });
await onClick();
assert.equal(button.disabled, false); assert.equal(button.textContent, 'Экспорт');
assert.equal(status.hidden, false); assert.match(status.textContent, /нет элементов/);
// Управляемое время проверяет реальные переходы: успех, чтение ссылки, новый запуск и ошибка.
const pending = new Map(); let serial = 0;
const timers = { setTimeout(fn, delay) { assert.equal(delay, 8000); pending.set(++serial, fn); return serial; }, clearTimeout(id) { pending.delete(id); } };
const events = {}, closes = [];
const noticeStatus = { hidden: false, textContent: '', reading: false,
  matches() { return this.reading; }, addEventListener(name, fn) { events[name] = fn; },
  append(button) { closes.push(button); }, ownerDocument: { createElement() { return { setAttribute() {}, addEventListener(_, fn) { this.click = fn; } }; } } };
const tick = () => { const callbacks = [...pending.values()]; pending.clear(); for (const callback of callbacks) callback(); };
const notice = createExportNotice(noticeStatus, timers);
notice.start(); notice.success(); assert.equal(pending.size, 1);
tick(); assert.equal(noticeStatus.hidden, true);
notice.start(); notice.success(); noticeStatus.reading = true; events.focusin();
tick(); assert.equal(noticeStatus.hidden, false, 'Чтение ссылки с клавиатуры сохраняет уведомление');
noticeStatus.reading = false; events.focusout(); assert.equal(pending.size, 1);
notice.start(); tick(); assert.equal(noticeStatus.hidden, false, 'Старый таймер не скрывает новый экспорт');
notice.error(); tick(); assert.equal(noticeStatus.hidden, false, 'Ошибка не исчезает автоматически');
closes.at(-1).click(); assert.equal(noticeStatus.hidden, true);
notice.start(); notice.success(); events.pointerenter(); assert.equal(pending.size, 0);
events.pointerleave(); tick(); assert.equal(noticeStatus.hidden, true);
console.log('Graph export bounds, safe filenames and error recovery PASS');
