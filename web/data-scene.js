/** Ограниченная карта структуры или колонок «ключ → значение → записи». */
const NS = 'http://www.w3.org/2000/svg';
/** Одна кубическая кривая с контрольными точками на трети расстояния. */
export function curve(a, b) {
  const dx = (b.x - a.x) / 3;
  return `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`;
}
/** Соседние ветви идут вертикально; вложенность увеличивает горизонтальную глубину. */
export function positions(nodes) {
  const index = new Map(nodes.map(node => [node.id, node]));
  const columns = new Map();
  const result = new Map();
  for (const node of nodes) {
    let parent = node.owner, depth = 0;
    const seen = new Set([node.id]);
    while (index.has(parent) && !seen.has(parent)) {
      seen.add(parent); depth++; parent = index.get(parent).owner;
    }
    if (Number.isInteger(node.column) && node.column >= 0 && node.column <= 5) depth = node.column;
    const row = columns.get(depth) || 0;
    columns.set(depth, row + 1);
    result.set(node.id, { x: 30 + depth * 260, y: 30 + row * 92 });
  }
  return result;
}
/** SVG создаётся без интерпретации текста данных как разметки. */
function element(tag, attributes = {}, text = '') {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text) node.textContent = text;
  return node;
}
/** В карточке путь данных подписан обычными словами, включая тип значения. */
export function kindLabel(node) {
  const names = { 'data-key': 'Ключ', 'data-value': 'Значение', 'data-record': 'Запись' };
  const types = { string: 'строка', number: 'число', boolean: 'логическое', null: 'null' };
  const label = names[node.kind] || node.kind;
  return label + (node.valueType ? ' · ' + (types[node.valueType] || node.valueType) : '') + (node.count == null ? '' : ' · ' + node.count);
}
export class DataScene {
  /** Владеет только ограниченной сценой; чтение данных выполняет контроллер. */
  constructor(container, select) {
    this.container = container; this.select = select; this.nodes = new Map(); this.groups = new Map(); this.relations = []; this.links = [];
    this.svg = element('svg', { xmlns: NS, role: 'group', 'aria-label': 'Структура выбранной карты данных' });
    this.edges = element('g'); this.cards = element('g');
    this.svg.append(this.edges, this.cards); container.replaceChildren(this.svg);
  }
  /** Освобождает карточки предыдущей карты перед сменой снимка. */
  reset() { this.nodes.clear(); this.groups.clear(); this.relations = []; this.links = []; this.cards.replaceChildren(); this.edges.replaceChildren(); }
  /** Заменяет ограниченный путь, сохраняя DOM общих узлов для анимации изоляции. */
  setGraph(nodes, links) {
    this.nodes = new Map(nodes.slice(0, 120).map(node => [node.id, node]));
    for (const [id, group] of this.groups) {
      if (!this.nodes.has(id)) { group.remove(); this.groups.delete(id); }
    }
    this.links = links; this.relations = []; this.draw();
  }
  /** Совпадения видны лишь между уже раскрытыми коллекциями; они остаются кандидатами. */
  setRelations(relations) { this.relations = relations; this.draw(); }
  /** Добавляет новые карточки локально; остаток доступен через список узлов. */
  add(nodes) {
    for (const node of nodes) {
      if (this.nodes.size >= 120 && !this.nodes.has(node.id)) break;
      this.nodes.set(node.id, node);
    }
    this.draw();
    return this.nodes.size >= 120;
  }
  /** Сохраняет DOM карточек при раскрытии, позволяя анимировать новое положение. */
  draw() {
    const points = positions([...this.nodes.values()]);
    this.edges.replaceChildren();
    for (const link of this.links) {
      const source = points.get(link.source), target = points.get(link.target);
      if (!source || !target) continue;
      const path = element('path', { class: 'data-edge', d: curve({ x: source.x + 220, y: source.y + 34 }, { x: target.x, y: target.y + 34 }) });
      path.append(element('title', {}, link.label || link.relation)); this.edges.append(path);
    }
    for (const relation of this.relations) {
      const source = points.get(relation.sourceCollection), target = points.get(relation.targetCollection);
      if (!source || !target) continue;
      const a = { x: source.x + 220, y: source.y + 50 }, b = { x: target.x + 220, y: target.y + 50 };
      const outside = Math.max(a.x, b.x) + 60;
      const path = element('path', { class: 'data-edge data-relation', d: `M ${a.x} ${a.y} C ${outside} ${a.y}, ${outside} ${b.y}, ${b.x} ${b.y}` });
      path.append(element('title', {}, `${relation.status === 'candidate' ? 'Кандидат' : 'Заданное правило'}: ${relation.sourceField} → ${relation.targetField}; совпадений: ${relation.coverage.matched}`));
      this.edges.append(path);
    }
    for (const node of this.nodes.values()) {
      const point = points.get(node.id), parent = points.get(node.owner);
      if (parent) this.edges.append(element('path', { class: 'data-edge', d: curve({ x: parent.x + 220, y: parent.y + 34 }, { x: point.x, y: point.y + 34 }) }));
      if (!this.groups.has(node.id)) {
        const group = element('g', { class: 'data-node', tabindex: '0', role: 'button', 'aria-label': node.label || node.name || node.kind });
        group.append(element('title', {}, [node.label, node.description, node.file].filter(Boolean).join('\n')));
        group.append(element('rect', { width: 220, height: 68 }));
        group.append(element('text', { x: 12, y: 20, class: 'node-kind' }, kindLabel(node)));
        const label = node.label || node.name || node.kind;
        group.append(element('text', { x: 12, y: 42 }, label.length > 28 ? label.slice(0, 27) + '…' : label));
        if (node.description) group.append(element('text', { x: 12, y: 58, class: 'node-kind' }, node.description.slice(0, 32)));
        group.addEventListener('click', () => this.select(this.nodes.get(node.id)));
        group.addEventListener('keydown', event => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); this.select(this.nodes.get(node.id)); } });
        this.groups.set(node.id, group); this.cards.append(group);
      }
      this.groups.get(node.id).style.transform = `translate(${point.x}px,${point.y}px)`;
    }
    const values = [...points.values()];
    this.svg.setAttribute('width', Math.max(800, ...values.map(point => point.x + 320)));
    this.svg.setAttribute('height', Math.max(440, ...values.map(point => point.y + 90)));
  }
  /** Самодостаточный SVG содержит стили и текущую видимую структуру. */
  export() {
    const copy = this.svg.cloneNode(true);
    const style = element('style', {}, '.data-edge{fill:none;stroke:#98bdaa;stroke-width:1.5}.data-relation{stroke:#bb8a46;stroke-dasharray:4 4}.data-node rect{fill:white;stroke:#a7c9b7;rx:10}.data-node text{font-family:system-ui;fill:#233a34;font-size:13px}.data-node .node-kind{fill:#648575;font-size:10px}');
    copy.prepend(style);
    const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(copy)], { type: 'image/svg+xml' }));
    const link = document.createElement('a'); link.href = url; link.download = 'data-map.svg'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
