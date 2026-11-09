/** Воспроизводимые деревья и сцены для проверки геометрии и затрат CPU. */
export function chain(size) {
  let root = { id: `node-${size - 1}`, w: 220, h: 80, children: [] };
  for (let index = size - 2; index >= 0; index--) root = { id: `node-${index}`, w: 220, h: 80, children: [root] };
  return root;
}

/** Генерирует ветвления, ссылки и разные размеры без зависимости от Math.random. */
export function randomTree(seed, size = 120) {
  let state = seed;
  const random = maximum => { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state % maximum; };
  const nodes = [];
  for (let index = 0; index < size; index++) {
    const decision = random(4) === 0;
    const node = { id: `seed-${seed}-${index}`, w: 100 + random(200), h: 50 + random(80),
      decision, kind: decision ? ['if', 'switch', 'foreach'][random(3)] : '', children: [] };
    if (index && random(7) === 0) { node.reference = true; node.focusId = 'shared'; }
    nodes.push(node);
    if (index) nodes[random(index)].children.push(node);
  }
  return nodes[0];
}

/** Изолирует сборку сцены от раскладки: подготовленные координаты уже переданы renderer. */
export function denseScene(count) {
  const nodes = [], positions = new Map();
  for (let index = 0; index < count; index++) {
    const id = `method-${index}`, branches = [];
    for (let branch = 0; branch < 40; branch++) {
      branches.push({ kind: 'if', line: branch + 1, header: 'flag',
        arms: [{ id: `${id}-true-${branch}`, kind: 'true' }, { id: `${id}-false-${branch}`, kind: 'false' }] });
      positions.set(`${id}::branch:${branch + 1}:${branch}`, { x: index * 400, y: branch * 100 });
    }
    const terminals = [];
    for (let terminal = 0; terminal < 12; terminal++) terminals.push({ line: 100 + terminal, kind: 'return', text: 'return value;' });
    nodes.push({ id, label: id, controlFlow: { branches, terminals } });
    positions.set(id, { x: index * 400, y: 0 });
  }
  return { scope: { nodes, edges: [], focusId: 'method-0' },
    options: { flowPath: true, flatFlow: true, flowPositions: positions } };
}
