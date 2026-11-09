/** Семантика ресурсов архитектуры; имена узлов не определяют цвет или тип. */
export const ARCHITECTURE_RESOURCE_TYPES = Object.freeze({
  database: Object.freeze({ label: 'БД' }),
  kafka: Object.freeze({ label: 'Kafka' }),
  rabbit: Object.freeze({ label: 'RabbitMQ' }),
  http: Object.freeze({ label: 'HTTP' }),
});
/** Постоянная строка типа не пересекается с IN/OUT и не меняет высоту карточки при раскрытии. */
export const ARCHITECTURE_RESOURCE_FOOTER = 32;

/** Возвращает только известный тип; логические блоки наследуют kind своего ресурса. */
export function architectureResourceKind(node) {
  return Object.hasOwn(ARCHITECTURE_RESOURCE_TYPES, node?.kind) ? node.kind : null;
}

/** Создаёт SVG без интерпретации внешнего текста как разметки. */
function svg(tag, attributes, text) {
  const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  if (text !== undefined) element.textContent = text;
  return element;
}

/** Маркирует только системные карточки; обычная архитектура кода сохраняет своё оформление. */
export function decorateArchitectureResource(group, node) {
  const kind = node.system && architectureResourceKind(node);
  if (!kind) return null;
  group.classList.toggle('architecture-resource-card', true);
  group.setAttribute('data-architecture-kind', kind);
  return ARCHITECTURE_RESOURCE_TYPES[kind];
}

/**
 * Рисует четыре статических line-глифа в общей сетке18×18.
 * Иконка декоративная: назначение продублировано видимой подписью типа и aria-label карточки.
 */
function appendResourceIcon(parent, kind) {
  const icon = svg('g', { class: 'architecture-resource-icon', 'data-resource-icon': kind,
    'aria-hidden': 'true', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6,
    'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
  if (kind === 'database') {
    icon.append(svg('ellipse', { cx: 9, cy: 4, rx: 6, ry: 2.5 }));
    icon.append(svg('path', { d: 'M3 4v10c0 3.3 12 3.3 12 0V4M3 9c0 3.3 12 3.3 12 0' }));
  } else if (kind === 'kafka') {
    icon.append(svg('path', { d: 'M6 6l5-2M6 10l5 4M4 7v2' }));
    for (const [cx, cy] of [[4, 4], [4, 12], [14, 3], [14, 15]]) icon.append(svg('circle', { cx, cy, r: 2.4 }));
  } else if (kind === 'rabbit') {
    for (const y of [3, 8, 13]) icon.append(svg('rect', { x: 2, y, width: 8, height: 3, rx: 1 }));
    icon.append(svg('path', { d: 'M12 9h4m-2-2l2 2-2 2' }));
  } else {
    icon.append(svg('circle', { cx: 9, cy: 9, r: 7 }));
    icon.append(svg('ellipse', { cx: 9, cy: 9, rx: 3, ry: 7 }));
    icon.append(svg('path', { d: 'M2 9h14' }));
  }
  parent.append(icon);
}

/** Добавляет текстовую метку и глиф в зарезервированную строку карточки. */
export function appendArchitectureTypeBadge(group, node, { x, y }) {
  const kind = architectureResourceKind(node);
  if (!kind) return;
  const badge = svg('g', { class: 'architecture-resource-type', transform: `translate(${x},${y})` });
  appendResourceIcon(badge, kind);
  badge.append(svg('text', { x: 25, y: 13, class: 'architecture-resource-type-label' }, ARCHITECTURE_RESOURCE_TYPES[kind].label));
  group.append(badge);
}

/** Рисует некликабельные области кластеров под связями; факты и идентификаторы узлов не меняются. */
export function appendArchitectureClusters(viewport, clusters = []) {
  for (const cluster of clusters) {
    if (![cluster.x, cluster.y, cluster.width, cluster.height].every(Number.isFinite)) continue;
    const group = svg('g', { class: 'architecture-resource-cluster', 'data-cluster-kind': cluster.kind,
      'pointer-events': 'none', 'aria-hidden': 'true' });
    group.append(svg('rect', { x: cluster.x, y: cluster.y, width: cluster.width, height: cluster.height,
      rx: 18, class: 'architecture-cluster-surface' }));
    group.append(svg('text', { x: cluster.x + 18, y: cluster.y + 24,
      class: 'architecture-cluster-heading' }, cluster.label));
    viewport.append(group);
  }
}
