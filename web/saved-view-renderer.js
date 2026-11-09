import { connectionCurve } from './edge-curves.js';
const NS = 'http://www.w3.org/2000/svg';
/** Создаёт SVG с текстом без интерпретации пользовательской разметки. */
function svgElement(name, attributes = {}, text) {
  const item = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attributes)) item.setAttribute(key, String(value));
  if (text !== undefined) item.textContent = text;
  return item;
}
/** Единственная геометрия стрелки: top-left карточки, выход справа и вход слева. */
export function savedEdgePath(source, target) {
  // Дальняя связь в одном ряду огибает карточки снизу, не изображая ложную цепочку.
  if (source.y === target.y && Math.abs(target.x - source.x) > source.width + 60) {
    const startX = source.x + source.width / 2;
    const endX = target.x + target.width / 2;
    const startY = source.y + source.height;
    const endY = target.y + target.height;
    const corridorY = Math.max(startY, endY) + 16;
    return `M ${startX} ${startY} C ${startX} ${corridorY}, ${endX} ${corridorY}, ${endX} ${endY}`;
  }
  const x1 = source.x + source.width;
  const y1 = source.y + source.height / 2;
  const x2 = target.x;
  const y2 = target.y + target.height / 2;
  return connectionCurve({ x: x1, y: y1 }, { x: x2, y: y2 });
}
/** Печатает ограниченное число строк; полный текст всегда доступен по клику. */
export function savedTextLines(text, width = 28, limit = 3) {
  const words = [];
  for (const word of String(text || '').split(/\s+/)) {
    for (let offset = 0; offset < word.length; offset += width) words.push(word.slice(offset, offset + width));
  }
  const lines = [];
  let line = '';
  for (const word of words) {
    if (line && (line.length + word.length + 1 > width)) { lines.push(line); line = ''; }
    line += `${line ? ' ' : ''}${word}`;
  }
  if (line) lines.push(line);
  const result = lines.slice(0, limit);
  if (lines.length > limit) result[limit - 1] += '…';
  return result;
}
/** Рисует только сохранённые координаты; UI и PNG читают один layout без force-layout. */
export function renderSavedView(svg, view, onSelect = () => {}) {
  svg.replaceChildren();
  const layout = view.layout;
  svg.setAttribute('viewBox', `0 0 ${layout.width} ${layout.height}`);
  const defs = svgElement('defs');
  const marker = svgElement('marker', { id: 'saved-arrow', markerWidth: 8, markerHeight: 8, refX: 7, refY: 4, orient: 'auto', markerUnits: 'userSpaceOnUse' });
  marker.append(svgElement('path', { d: 'M0 0 L8 4 L0 8 Z', fill: '#526c73' }));
  defs.append(marker);
  svg.append(defs);
  svg.append(svgElement('text', { x: 20, y: 30, 'font-size': 20, fill: '#233c43' }, view.title));
  svg.append(svgElement('text', { x: 20, y: 54, 'font-size': 12, fill: '#526c73' }, String(view.query || '').slice(0, 150)));
  svg.append(svgElement('text', { x: 20, y: 70, 'font-size': 11, fill: '#526c73' }, 'Сплошные связи — код; пунктир и жёлтые заметки — пояснения агента.'));
  for (const project of layout.projects || []) {
    svg.append(svgElement('rect', { x: project.x, y: project.y, width: project.width, height: project.height, rx: 18, fill: project.color?.fill || '#edf3f1', stroke: project.color?.stroke || '#708e86' }));
    const labelLimit = Math.max(1, Math.floor((project.width - 32) / 9));
    const label = String(project.label || '');
    const title = svgElement('text', { x: project.x + 16, y: project.y + 26, 'font-size': 16, fill: project.color?.text || '#263e43' }, label.length > labelLimit ? `${label.slice(0, labelLimit - 1)}…` : label);
    title.append(svgElement('title', {}, label));
    svg.append(title);
  }
  for (const edge of view.edges || []) {
    const source = layout.nodes[edge.source];
    const target = layout.nodes[edge.target];
    if (!source || !target) continue;
    const path = svgElement('path', { d: savedEdgePath(source, target), stroke: '#526c73', 'stroke-width': 1.6, fill: 'none', 'marker-end': 'url(#saved-arrow)' });
    path.append(svgElement('title', {}, `${edge.source} → ${edge.target} · ${edge.relation}`));
    svg.append(path);
  }
  for (const edge of view.annotationEdges || []) {
    const source = layout.annotations?.[edge.source];
    const target = layout.nodes[edge.target];
    if (!source || !target) continue;
    svg.append(svgElement('path', { d: `M ${source.x + source.width / 2} ${source.y + source.height / 2} L ${target.x + target.width / 2} ${target.y + target.height / 2}`, stroke: '#987b34', 'stroke-dasharray': '6 5', fill: 'none' }));
  }
  const cards = [];
  for (const node of view.nodes || []) cards.push({ node, note: false });
  for (const node of view.annotations || []) cards.push({ node, note: true });
  for (const { node, note } of cards) {
    const box = (note ? layout.annotations : layout.nodes)?.[node.id];
    if (!box) continue;
    const group = svgElement('g', { tabindex: 0, role: 'button', 'aria-label': node.label || node.title, 'data-node-id': node.id });
    group.append(svgElement('rect', { x: box.x, y: box.y, width: box.width, height: box.height, rx: 8, fill: note ? '#fff7d9' : '#fff', stroke: note ? '#987b34' : node.color || '#526c73', 'stroke-width': 2, ...(note ? { 'stroke-dasharray': '6 4' } : {}) }));
    group.append(svgElement('title', {}, note ? node.text : `${node.label}\n${node.file || ''}:${node.line || ''}`));
    const width = Math.max(1, Math.floor((box.width - 24) / 8));
    const lines = note ? box.titleLines || savedTextLines(node.title, width, 2) : box.displayLines || savedTextLines(node.label, 26, 3);
    for (let index = 0; index < lines.length; index++) group.append(svgElement('text', { x: box.x + 12, y: box.y + 24 + index * 16, 'font-size': 14, 'font-weight': note ? 'bold' : 'normal', fill: '#233c43' }, lines[index]));
    if (note) {
      const textLines = box.displayLines || savedTextLines(node.text, width, Math.max(1, Math.floor((box.height - 48 - lines.length * 16) / 16)));
      for (let index = 0; index < textLines.length; index++) group.append(svgElement('text', { x: box.x + 12, y: box.y + 24 + (lines.length + 1 + index) * 16, 'font-size': 13, fill: '#534a30' }, textLines[index]));
    } else {
      group.append(svgElement('text', { x: box.x + 12, y: box.y + box.height - 10, 'font-size': 11, fill: '#526c73' }, node.kind || ''));
    }
    group.addEventListener('click', () => onSelect(node, { annotation: note }));
    group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(node, { annotation: note }); } });
    svg.append(group);
  }
}
