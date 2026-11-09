/** CPU regression evidence; excludes browser painting, network, and resident-memory profiling.
 * node tests/benchmark_frontend_review.mjs [BASELINE_WEB_DIRECTORY]
 * Coordinates, object keys and array order are hashed to compare unchanged integer geometry.
 */
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { chain, randomTree, denseScene } from './frontend_review_fixtures.mjs';
const root = process.argv[2] || path.resolve('web');
const load = name => import(pathToFileURL(path.join(root, name)));
const { packMethodFrames } = await load('path-row-layout.js');
const { inlineFlowEdgeRouter } = await load('inline-flow-edges.js');
const { buildPathScene } = await load('path-scene.js');

/** Возвращает медиану пяти прогретых запусков и хеш результата вне измеряемой операции. */
function measure(name, run) {
  run();
  const samples = [];
  let result;
  for (let index = 0; index < 5; index++) {
    const start = performance.now();
    result = run();
    samples.push(performance.now() - start);
  }
  samples.sort((a, b) => a - b);
  return { name, medianMs: samples[2], hash: createHash('sha256').update(JSON.stringify(result)).digest('hex') };
}
const results = [];
for (const size of [320, 1000, 3000]) {
  const root = chain(size);
  results.push(measure(`pack-chain-${size}`, () => packMethodFrames([root])));
}
for (let seed = 1; seed <= 32; seed++) {
  const tree = randomTree(seed);
  results.push(measure(`geometry-seed-${seed}`, () => packMethodFrames([tree], { innerBudget: 580 + seed * 10 })));
}
for (const size of [320, 1000, 3000]) {
  const positions = new Map();
  for (let index = 0; index < size; index++) positions.set(`node-${index}`, { id: `node-${index}`, x: index * 400, y: 0, w: 220, h: 80 });
  results.push(measure(`clear-curves-${size}`, () => {
    const route = inlineFlowEdgeRouter(positions), curves = [];
    for (let index = 1; index < size; index++) curves.push(route(positions.get(`node-${index - 1}`), positions.get(`node-${index}`), index).d);
    return curves;
  }));
}
for (const size of [20, 100, 300]) {
  const fixture = denseScene(size);
  results.push(measure(`scene-${size}`, () => buildPathScene(fixture.scope, fixture.options)));
}
console.log(JSON.stringify({ measurement: 'Node CPU only; five warm samples; hashes include integer geometry and order', results }, null, 2));
