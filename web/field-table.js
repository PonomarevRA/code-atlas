/** Компактные строки модели сохраняют raw ID, выбор и порты; типы берутся только из захваченных фактов. */
export const FIELD_TABLE = { width: 700, gap: 2, heading: 24 };
import { appendCodeText } from './code-text.js';

/** Только подтверждённое объявление поля/свойства становится строкой таблицы. */
export function fieldOf(item) {
  const facts = item?.node?.fieldFacts;
  return item?.kind === 'raw' && ['field', 'property'].includes(facts?.kind) ? facts : null;
}

/** Перенос описания ограничен тремя строками; полный текст остаётся в title/инспекторе. */
function descriptionLines(value) {
  const lines = [], words = String(value || '').split(/\s+/u).filter(Boolean);
  let line = '';
  for (const word of words) {
    if (line && line.length + word.length > 90) { lines.push(line); line = ''; }
    line += `${line ? ' ' : ''}${word}`;
  }
  if (line) lines.push(line);
  return lines.slice(0, 3).map((value, index) => index === 2 && lines.length > 3 ? `${value}…` : value);
}

/** Константа видна уже в строке поля; пары словаря раскрываются отдельным деревом данных. */
function fieldDescription(item) {
  const facts = fieldOf(item);
  const data = item.node?.dataFacts || item.node?.staticData || facts?.staticData;
  const value = data?.kind === 'constant' ? `Значение: ${data.initializer}` : '';
  const member = item.node?.memberFacts;
  const access = member ? `${member.visibility}${member.static ? ' · static' : ''}${member.readonly ? ' · readonly' : ''}` : '';
  return [access, facts?.description, value].filter(Boolean).join(' · ');
}

/** Геометрия использует те же строки, что и SVG: описание никогда не выходит из bounds. */
export function fieldRowHeight(item) {
  return 44 + descriptionLines(fieldDescription(item)).length * 16;
}

/** Безопасный SVG-текст: документация проекта не может исполнять HTML. */
function append(group, tag, attrs, value) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, item] of Object.entries(attrs)) node.setAttribute(key, item);
  if (value !== undefined) node.textContent = value;
  group.append(node);
  return node;
}

/** Рисует поверхность строки, оставляя события и действия общему renderer. Боковые порты обслуживают реальные рёбра. */
export function appendFieldRow(group, { item, width, height, left, top, color, counts }) {
  const facts = fieldOf(item);
  const nullable = facts.nullable === true ? 'NULL' : facts.nullable === false ? 'NOT NULL' : 'NULL ?';
  group.setAttribute('data-card-design', 'field-table');
  group.setAttribute('data-field-name', facts.name);
  group.setAttribute('data-field-type', facts.type);
  group.setAttribute('data-field-nullable', String(facts.nullable));
  append(group, 'rect', { x: left, y: top, width, height, rx: 3, fill: '#fbfcfa', stroke: color, class: 'inline-hierarchy-card-body field-table-body' });
  const shorten = (value, size) => value.length > size ? `${value.slice(0, size - 1)}…` : value;
  appendCodeText(group, { x: left + 18, y: top + 24, class: 'field-table-name' }, shorten(facts.name, 29));
  appendCodeText(group, { x: left + 263, y: top + 24, class: 'field-table-type' }, shorten(facts.type, 30));
  const badge = append(group, 'text', { x: left + 542, y: top + 24, class: 'field-table-nullable' }, nullable);
  append(badge, 'title', {}, `${facts.nullable === null ? 'Nullability неизвестна' : 'Объявленная nullability'}: ${facts.nullableEvidence}\n${facts.warning || ''}`);
  for (const [index, line] of descriptionLines(fieldDescription(item)).entries()) {
    const attributes = { x: left + 18, y: top + 44 + index * 16, class: 'field-table-description' };
    if ((item.node?.dataFacts || item.node?.staticData)?.kind === 'constant') appendCodeText(group, attributes, shorten(line, 96));
    else append(group, 'text', attributes, shorten(line, 96));
  }
  for (const [side, count] of [['in', counts.incoming], ['out', counts.outgoing]]) {
    const port = append(group, 'circle', { cx: left + (side === 'in' ? 0 : width), cy: top + height / 2,
      r: 4, fill: color, stroke: '#fff', class: `flow-port ${side}`, 'data-port': side });
    append(port, 'title', {}, `${side === 'in' ? 'Входящих' : 'Исходящих'} связей: ${count}`);
  }
  append(group, 'title', {}, `${facts.name}: ${facts.type} · ${nullable}\n${facts.description || 'Описание в исходнике отсутствует'}\n${facts.file}:${facts.line}\n${facts.nullableEvidence}\nIN ${counts.incoming} · OUT ${counts.outgoing}`);
}
