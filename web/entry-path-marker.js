const NS = 'http://www.w3.org/2000/svg';

/** Ромб и подпись входа повторяют обозначение API на обычной карте; синтетический ID не выбирается как метод. */
export function appendEntryPathMarker(view, node, point) {
  const group = document.createElementNS(NS, 'g');
  group.setAttribute('class', 'path-entry-marker');
  group.setAttribute('transform', `translate(${point.x},${point.y})`);
  const diamond = document.createElementNS(NS, 'polygon');
  diamond.setAttribute('points', '0,-8 8,0 0,8 -8,0');
  diamond.setAttribute('fill', '#367a99');
  group.append(diamond);
  const kind = node.entryKind === 'http' || node.httpMethod ? 'HTTP API' : 'Точка входа';
  const route = `${node.httpMethod ? `${node.httpMethod} ` : ''}${node.label || 'Вход'}`;
  for (const [label, y] of [[kind, 24], [route.length > 40 ? `${route.slice(0, 39)}…` : route, 42]]) {
    const text = document.createElementNS(NS, 'text');
    text.setAttribute('y', y);
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('class', 'path-card-owner');
    text.textContent = label;
    group.append(text);
  }
  const title = document.createElementNS(NS, 'title');
  title.textContent = `${kind}\n${route}`;
  group.append(title);
  view.viewport.append(group);
  view.nodeElements.set(node.id, { group, radius: 8 });
}
