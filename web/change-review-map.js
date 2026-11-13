const SIDES = [['before', 'До'], ['after', 'После']];

/** Index each directed connection at both endpoints. */
function indexEdges(edges) {
  const index = new Map();
  for (const [source, target, relation, candidate] of edges) {
    if (!index.has(source)) index.set(source, []);
    if (!index.has(target)) index.set(target, []);
    index.get(source).push({peer: target, direction: 'outgoing', relation, candidate});
    index.get(target).push({peer: source, direction: 'incoming', relation, candidate});
  }
  return index;
}

/** Older reviews only retain call evidence from the selected entry path. */
function pathEdges(path) {
  return (path?.edges ?? []).map(edge => [edge.source, edge.target, edge.relation, edge.confidence === 'candidate']);
}

/** Render a file map and a selectable neighbourhood of changed functions. */
export function createReviewMap(root, {text, kinds, locationText, result}) {
  const files = root.querySelector('#review-map-files');
  const selection = root.querySelector('#review-map-selection');
  const groups = new Map();
  const nodes = new Map();
  const sides = SIDES.map(([name, label]) => {
    const captured = result.mapEdges?.[name];
    return {name, label, captured, index: indexEdges(captured?.edges ?? pathEdges(result.paths[name])), rawIds: new Map()};
  });
  let selected = null;

  /** Select a function and reveal its file group when following a connection. */
  function select(index, focus = false) {
    selected?.button.removeAttribute('aria-current');
    selected = nodes.get(index);
    selected.button.setAttribute('aria-current', 'true');
    selected.group.details.open = true;
    renderSelection();
    if (focus) selected.button.focus();
  }

  /** List linked functions that are already present on the map. */
  function renderNeighbours(links, direction, heading, version) {
    const block = text('div', '', 'review-map-neighbours');
    block.append(text('h5', heading));
    const list = text('ul', '');
    for (const edge of links) {
      if (edge.direction !== direction || !edge.peer) continue;
      const row = text('li', '');
      const button = text('button', edge.peer.declaration.label, 'review-map-node');
      button.type = 'button';
      button.dataset.reviewLink = [version, direction, edge.peer.index, edge.relation, edge.candidate].join(':');
      button.title = locationText(edge.peer.declaration);
      button.addEventListener('click', () => select(edge.peer.index, true));
      row.append(button);
      if (edge.candidate) {
        const qualifier = edge.relation === 'dispatches_to' ? 'Вариант реализации' : 'Возможный вызов';
        row.append(text('small', qualifier, 'muted'));
      }
      list.append(row);
    }
    block.append(list.children.length ? list : text('p', 'Нет на карте', 'muted'));
    return block;
  }

  /** Display directional evidence and the selected function’s saved source positions. */
  function renderSelection() {
    const active = root.ownerDocument.activeElement;
    const focusedLink = selection.contains(active) ? active.dataset.reviewLink : null;
    selection.replaceChildren(text('h3', selected.declaration.label), text('p', kinds[selected.row.kind], 'muted'));
    const versions = text('div', '', 'review-map-versions');
    for (const side of sides) {
      const panel = text('div', '', 'review-map-version');
      panel.append(text('h4', side.label));
      const declaration = selected.row[side.name];
      if (!declaration) {
        panel.append(text('p', 'В снимке не найдена', 'muted'));
      } else {
        panel.append(text('p', locationText(declaration), 'review-location'));
        const key = side.captured ? selected.index : declaration.nodeId;
        const links = (side.index.get(key) ?? []).map(edge => ({
          ...edge, peer: nodes.get(side.captured ? edge.peer : side.rawIds.get(edge.peer)),
        }));
        const neighbours = text('div', '', 'review-map-directions');
        neighbours.append(renderNeighbours(links, 'incoming', 'Вызывают эту функцию →', side.name),
          renderNeighbours(links, 'outgoing', 'Эта функция вызывает →', side.name));
        panel.append(neighbours);
      }
      versions.append(panel);
    }
    selection.append(versions);
    if (focusedLink) {
      const restored = Array.from(selection.querySelectorAll('button')).find(button => button.dataset.reviewLink === focusedLink);
      (restored ?? selected.button).focus();
    }
  }

  /** Create one expandable group per file, preserving the review's source order. */
  function groupFor(file) {
    if (groups.has(file)) return groups.get(file);
    const details = text('details', '');
    const label = text('summary', '');
    const list = text('ul', '', 'review-map-functions');
    details.append(label, list);
    files.append(details);
    const group = {details, label, list, count: 0};
    groups.set(file, group);
    return group;
  }

  /** Append the same ordered page as the review list and return its first file control. */
  function append(changes) {
    let firstFile = null;
    for (const row of changes) {
      const index = nodes.size;
      const declaration = row.after || row.before;
      const group = groupFor(declaration.file);
      firstFile ??= group.label;
      const button = text('button', declaration.label, 'review-map-node');
      button.type = 'button';
      button.append(text('small', kinds[row.kind], 'muted'));
      button.addEventListener('click', () => select(index));
      const item = text('li', '');
      item.append(button);
      group.list.append(item);
      group.count += 1;
      group.label.textContent = `${declaration.file} (${group.count})`;
      nodes.set(index, {index, row, declaration, group, button});
      for (const {name, rawIds} of sides) {
        if (row[name]) rawIds.set(row[name].nodeId, index);
      }
    }
    if (!selected && nodes.size) select(0);
    else if (selected) renderSelection();
    root.querySelector('#review-map-count').textContent = `На карте ${nodes.size} из ${result.totalChanges} функций.`;
    return firstFile;
  }

  const scope = root.querySelector('#review-map-scope');
  scope.textContent = sides.some(side => !side.captured)
    ? 'Для старых снимков доступны связи из сохранённого пути от точки входа.'
    : 'Статические связи между изменёнными функциями из сохранённых снимков.';
  if (sides.some(side => side.captured?.truncated)) scope.textContent += ' Часть связей не вошла в снимок обзора.';
  if (result.before.coverage.partial || result.after.coverage.partial) scope.textContent += ' Покрытие анализа частичное.';
  return {append};
}
