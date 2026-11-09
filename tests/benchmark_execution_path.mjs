/** CPU benchmark; SVG fixture excludes browser layout, painting and network.
 * Usage: node tests/benchmark_execution_path.mjs GRAPH.json PATH.json [BASELINE_WEB_DIRECTORY]
 * Three samples per scope report medians, including a rerender with unchanged geometry.
 */
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { Svg } from './svg_fixture.mjs';
const webRoot = process.argv[4] || path.resolve('web');
const load = name => import(pathToFileURL(path.join(webRoot, name)));
const { buildPathScope } = await load('path-scope.js');
const { buildEntryPathScene } = await load('entry-path-scene.js');
const { inlineFlowEdgeRouter } = await load('inline-flow-edges.js');
const { GraphView } = await load('graph.js');
const { projectExecutionPath } = await load('path-graph-projection.js');
const graph = JSON.parse(fs.readFileSync(process.argv[2]));
const data = JSON.parse(fs.readFileSync(process.argv[3]));
const projected = projectExecutionPath(data, new Map(graph.nodes.map(node => [node.id, node])));

/** A realistic intermediate expansion follows six levels of reachable calls. */
function partialExpansion() {
  const depth = new Map([[data.rootNodeId, 0]]), pending = [data.rootNodeId], outgoing = new Map();
  for (const edge of projected.edges) {
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
    outgoing.get(edge.source).push(edge.target);
  }
  for (let index = 0; index < pending.length; index++) {
    const id = pending[index];
    if (depth.get(id) >= 6) continue;
    for (const target of outgoing.get(id) || []) {
      if (depth.has(target)) continue;
      depth.set(target, depth.get(id) + 1); pending.push(target);
    }
  }
  return [...depth.keys()];
}

globalThis.document = { createElementNS: (_, tag) => new Svg(tag) };
globalThis.ResizeObserver = class { observe() {} };

/** Measures scene geometry, cold routing, expansion rendering and identical-scene rendering separately. */
function measure(expanded) {
  const view = { pathData: projected, pathRootId: data.rootNodeId, pathReading: true,
    groupingProvider: 'natural', pathSnapshotId: 'benchmark' };
  let start = performance.now();
  const scope = buildPathScope(projected.nodes, projected.edges, { rootId: data.rootNodeId, expandedIds: expanded });
  const scopeMs = performance.now() - start;
  start = performance.now();
  const scene = buildEntryPathScene(view, scope, data.rootNodeId);
  const sceneMs = performance.now() - start;
  start = performance.now();
  const route = inlineFlowEdgeRouter(scene.positions), hash = createHash('sha256');
  let routed = 0;
  for (const edge of scene.visualEdges) {
    const source = scene.positions.get(edge.source), target = scene.positions.get(edge.target);
    if (source && target) hash.update(route(source, target, routed++).d);
  }
  const routesMs = performance.now() - start;
  const svg = new Svg('svg'), viewport = new Svg('g');
  viewport.setAttribute('id', 'viewport'); svg.append(viewport);
  const graphView = new GraphView(svg, () => {}, () => {});
  Object.assign(graphView, { pathReading: true, groupingProvider: 'natural', pathRootId: data.rootNodeId });
  graphView.setDisplayProvider('flow');
  graphView.renderPathCards(projected.nodes, projected.edges, data.rootNodeId);
  graphView.pathExpandedIds = new Set(expanded);
  start = performance.now();
  graphView.renderPathCards(projected.nodes, projected.edges, data.rootNodeId);
  const renderMs = performance.now() - start;
  start = performance.now();
  graphView.renderPathCards(projected.nodes, projected.edges, data.rootNodeId);
  const repeatRenderMs = performance.now() - start;
  return { methods: scope.nodes.length, visualNodes: scene.visuals.length, edges: routed,
    routeHash: hash.digest('hex'), scopeMs, sceneMs, routesMs, renderMs, repeatRenderMs };
}

const expansions = { root: [], partial: partialExpansion(), all: projected.nodes.map(node => node.id) };
const results = [];
for (const [mode, expanded] of Object.entries(expansions)) {
  const samples = [];
  for (let sample = 0; sample < 3; sample++) samples.push(measure(expanded));
  const result = { mode, ...samples[0] };
  for (const key of ['scopeMs', 'sceneMs', 'routesMs', 'renderMs', 'repeatRenderMs']) {
    result[key] = samples.map(item => item[key]).sort((a, b) => a - b)[1];
  }
  results.push(result);
}
console.log(JSON.stringify({ measurement: 'Node CPU with SVG fixture; excludes browser layout, paint and network',
  samples: 3, results }, null, 2));
