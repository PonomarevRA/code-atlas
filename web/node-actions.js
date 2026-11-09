const NS = 'http://www.w3.org/2000/svg';

/** Создаёт SVG через атрибуты и textContent; данные проекта никогда не становятся HTML. */
function svg(tag, attributes, text) {
  const node = document.createElementNS(NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Отдельный action не должен выбирать родительскую карточку или начинать перетаскивание карты. */
function action(group, { id, name, label, x, y, callback, expanded, text = null, align = 'start' }) {
  const button = svg('g', {
    class: `node-action node-action-${name}`,
    transform: `translate(${x},${y})`,
    'data-node-action': name,
    'data-owner-id': id,
    tabindex: 0,
    role: 'button',
    'aria-label': label,
  });
  if (name === 'inline') button.setAttribute('aria-expanded', String(Boolean(expanded)));
  button.append(svg('title', {}, label));
  if (text) {
    const width = text.length * 7 + 8;
    button.append(svg('rect', { x: align === 'end' ? -width : -4, y: -14, width, height: 22,
      fill: 'transparent', 'pointer-events': 'all' }));
    button.append(svg('text', { 'text-anchor': align, class: 'path-card-expand', 'font-size': 12 }, text));
  } else {
    button.append(svg('circle', { r: 12, class: 'node-action-hit' }));
    button.append(svg('circle', { r: 9, class: 'node-action-circle' }));
    const glyph = name === 'enter' ? 'M-3,-4 L1,0 L-3,4 M1,0 L5,0' : expanded ? 'M-4,0 L4,0' : 'M-4,0 L4,0 M0,-4 L0,4';
    button.append(svg('path', { d: glyph, class: 'node-action-glyph', 'aria-hidden': 'true' }));
  }
  button.addEventListener('pointerdown', event => event.stopPropagation());
  button.addEventListener('click', event => {
    event.stopPropagation();
    event.preventDefault();
    callback(id);
  });
  button.addEventListener('keydown', event => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.stopPropagation();
    event.preventDefault();
    if (!event.repeat) callback(id);
  });
  group.append(button);
  return button;
}

/**
 * Добавляет независимые кнопки в координатах group: toggle центр (x,y), enter (x+30,y).
 * Передавайте callbacks только доступных действий. Raw/synthetic id передаётся без преобразований.
 * Вызывающий renderer сохраняет фокус после relayout, поскольку только он знает новую карточку.
 */
export function appendNodeActions(group, { id, label = id, enter, enterLabel, onToggle, expanded = false, x = 0, y = 0, reading = null }) {
  if (reading) return readingActions(group, { id, label, enter, enterLabel, onToggle, expanded, ...reading });
  const result = { enter: null, toggle: null };
  if (typeof onToggle === 'function') {
    result.toggle = action(group, { id, name: 'inline', label: `${expanded ? 'Свернуть' : 'Раскрыть здесь'}: ${label}`,
      x, y, callback: onToggle, expanded });
  }
  if (typeof enter === 'function') {
    result.enter = action(group, { id, name: 'enter', label: enterLabel || `Войти в блок: ${label}`,
      x: x + 30, y, callback: enter });
  }
  return result;
}

/** Path-style footer rows keep source, separate diagram and local disclosure apart from the card title. */
function readingActions(group, { id, label, enter, enterLabel, onToggle, expanded, left, top, width, height,
  source, sourceId = id, enterText = 'Схема →', compact = false }) {
  const result = { enter: null, toggle: null, source: null };
  if (typeof enter === 'function') result.enter = action(group, { id, name: 'enter',
    label: enterLabel || `Открыть схему: ${label}`, text: enterText, x: left + 12, y: top + height - 10, callback: enter });
  if (typeof onToggle === 'function') result.toggle = action(group, { id, name: 'inline',
    label: `${expanded ? 'Свернуть' : 'Раскрыть здесь'}: ${label}`, text: expanded ? 'Свернуть' : 'Раскрыть здесь',
    align: 'end', x: left + width - 12, y: top + height - 10, callback: onToggle, expanded });
  if (typeof source === 'function') result.source = action(group, { id: sourceId, name: 'source',
    label: `Открыть исходник: ${label}`, text: 'Исходник ↗', align: 'end',
    x: left + width - 12, y: top + height - (compact ? 10 : 32), callback: source });
  return result;
}
