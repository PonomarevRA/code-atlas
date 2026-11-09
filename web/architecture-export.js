/**
 * Safe, deterministic architecture serialization without DOM, I/O or executable configuration.
 */
const MAX_NODES = 4096, MAX_EDGES = 8192, MAX_BYTES = 4 * 1024 * 1024;
const NODE_FIELDS = ['id', 'kind', 'dependencyKind', 'resourceKind', 'label', 'logicalName', 'host', 'scheme',
  'databaseType', 'repositoryId', 'repositoryIds', 'ownerId', 'identityScope', 'status',
  'originalCodeProjectId', 'codeProjectId', 'ownershipKnown', 'configurationBindingKnown',
  'configurationCandidatesTruncated', 'runtimeSelectionKnown'];
const EDGE_FIELDS = ['id', 'factId', 'source', 'target', 'relation', 'operation', 'weight', 'status',
  'evidenceTruncated', 'runtimeSelectionKnown', 'factIds', 'operations', 'statuses'];
const EVIDENCE_FIELDS = ['file', 'line', 'sourceHash', 'rawNodeId', 'repositoryId', 'type', 'confidence', 'configVariant', 'provider', 'basis'];
const PROOF_FIELDS = ['basis', 'provider', 'sourceKind', 'runtimeSelectionKnown'];
const CONFIG_FIELDS = ['kind', 'name', 'engine', 'host', 'file', 'line', 'status', 'key'];
const COVERAGE_FIELDS = ['repositoryId', 'candidateFiles', 'readFiles', 'skippedFiles', 'unavailableFiles',
  'parseErrors', 'limited', 'complete', 'facts', 'maxFiles', 'maxBytes', 'maxFacts', 'providerVersion',
  'analysisScope', 'scope', 'unresolvedReferences', 'ambiguousConfigDirectories', 'ambiguousFiles',
  'discoveryLimited', 'httpUnavailableFiles'];

/**
 * Reject unsafe textual values rather than interpreting URI, source or credential syntax.
 * @param {unknown} value Public scalar from a whitelisted field.
 * @param {number} limit Maximum string length; identifiers permit 4096, other text 500.
 * @returns {string|number|boolean|null|undefined} Bounded scalar or undefined when redacted.
 */
function scalar(value, limit = 500) {
  if (value === null || typeof value === 'boolean') return value;
  if (typeof value === 'number') return Number.isFinite(value) ? value : undefined;
  if (typeof value !== 'string' || value.length > limit) return undefined;
  if (/\b(?:[a-z][a-z0-9+.-]*:\/\/|password\s*[=:]|passwd\s*[=:]|token\s*[=:]|secret\s*[=:]|connectionstring\s*[=:])/i.test(value)) return undefined;
  if (/^(?:\/|[a-z]:[\\/]|\\\\)/i.test(value)) return undefined;
  if (/\b(?:host|server|database|data\s*source|user\s*id|uid)\s*=[^;]*(?:;|$)/i.test(value)) return undefined;
  if (/\b(?:select[\s\S]+from|insert\s+into|delete\s+from|update[\s\S]+set)\b/i.test(value)) return undefined;
  return value.replace(/[\u0000-\u001f\u007f]/g, ' ');
}

/**
 * Copy only declared scalars, retaining finite string arrays in stable order.
 * @param {object} input Source record, never recursively copied.
 * @param {string[]} fields Explicit field allowlist.
 * @returns {object} Sanitized plain record.
 */
function record(input, fields) {
  const result = {};
  for (const field of fields) {
    const value = input?.[field];
    if (Array.isArray(value)) {
      const items = [];
      for (const item of value.slice(0, field === 'factIds' ? MAX_EDGES : 64)) {
        const idArray = ['visitedNodeIds', 'processedNodeIds', 'unprocessedNodeIds', 'factIds'].includes(field);
        const safe = scalar(item, idArray ? 4096 : 500);
        if (typeof safe === 'string') items.push(safe);
      }
      result[field] = items.sort();
    } else {
      const safe = scalar(value, ['id', 'nodeId', 'source', 'target', 'rawNodeId'].includes(field) ? 4096 : 500);
      if (safe !== undefined) result[field] = safe;
    }
  }
  return result;
}

/**
 * Keep only relative source references; source text and arbitrary nested payloads are excluded.
 * @param {object} input One evidence/configuration record.
 * @param {string[]} fields Allowed reference fields.
 * @returns {object} Source reference with unsafe paths removed.
 */
function reference(input, fields) {
  const result = record(input, fields);
  if (result.file && (result.file.includes('\\') || result.file.split('/').includes('..'))) delete result.file;
  return result;
}

/**
 * Compare serialized values by UTF-16 code units, avoiding locale/ICU-dependent export order.
 * @param {string} left First canonical string.
 * @param {string} right Second canonical string.
 * @returns {number} Negative, zero or positive ordering value.
 */
function compare(left, right) {
  if (left === right) return 0;
  return left < right ? -1 : 1;
}

/**
 * Stable noncryptographic identifier; collisions are checked by the caller before emission.
 * @param {string} value Canonical identity, distinct from a presentation label.
 * @returns {string} Legal DSL identifier containing an FNV-1a64 digest.
 */
function identifier(value) {
  let hash = 14695981039346656037n;
  for (const byte of new TextEncoder().encode(value)) hash = BigInt.asUintN(64, (hash ^ BigInt(byte)) * 1099511628211n);
  return `a${hash.toString(16).padStart(16, '0')}`;
}

/**
 * Serialize one DSL token without permitting new statements or directives.
 * @param {unknown} value Sanitized public label.
 * @returns {string} Quoted token with escaped backslashes and quotes.
 */
function quote(value) {
  return `"${String(value ?? '').replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/[\r\n]/g, ' ')}"`;
}

/**
 * Build the bounded canonical archive from selected graph pages and explicit session geometry.
 * @param {object} graph Complete caller-selected graph, not the whole raw code graph.
 * @param {object} options Snapshot/project identity and optional pins/positions.
 * @returns {object} Versioned sanitized archive.
 * @throws {Error} Invalid identities, dangling edges, duplicate IDs or size limits.
 */
function archive(graph, options) {
  if (!Array.isArray(graph?.nodes) || !Array.isArray(graph?.edges)) throw new Error('Architecture graph is unavailable.');
  if (graph.nodes.length > MAX_NODES || graph.edges.length > MAX_EDGES) throw new Error('Architecture export exceeds node/edge limits.');
  const nodes = [], edges = [], ids = new Set();
  for (const input of graph.nodes) {
    const node = record(input, NODE_FIELDS);
    if (typeof node.id !== 'string' || !node.id || ids.has(node.id)) throw new Error('Architecture node identity is invalid or duplicated.');
    ids.add(node.id);
    node.proof = record(input.proof, PROOF_FIELDS);
    node.configurationCandidates = (input.configurationCandidates || []).slice(0, 12).map(item => reference(item, CONFIG_FIELDS));
    nodes.push(node);
  }
  nodes.sort((a, b) => compare(a.id, b.id));
  for (const input of graph.edges) {
    const edge = record(input, EDGE_FIELDS);
    if (!ids.has(edge.source) || !ids.has(edge.target)) throw new Error('Architecture export contains a dangling edge.');
    edge.proof = record(input.proof, PROOF_FIELDS);
    edge.evidence = (input.evidence || []).slice(0, 12).map(item => reference(item, EVIDENCE_FIELDS));
    edge.evidence.sort((a, b) => compare(JSON.stringify(a), JSON.stringify(b)));
    edges.push(edge);
  }
  edges.sort((a, b) => compare(JSON.stringify(a), JSON.stringify(b)));
  const projection = record(graph.projection, ['scale']);
  const displayIds = graph.projection?.displayNodeIds || [];
  if (displayIds.length > MAX_NODES || (options.pinnedIds || []).length > MAX_NODES) throw new Error('Architecture presentation exceeds node limits.');
  projection.displayNodeIds = [];
  for (const id of displayIds) {
    const safe = scalar(id, 4096);
    if (typeof safe === 'string') projection.displayNodeIds.push(safe);
  }
  projection.displayNodeIds = [...new Set(projection.displayNodeIds)].sort();
  const presentationIds = new Set([...ids, ...projection.displayNodeIds]);
  const pinnedIds = [...new Set(options.pinnedIds || [])].filter(id => presentationIds.has(id)).sort();
  const positions = Object.create(null);
  for (const id of [...presentationIds].sort()) {
    const position = options.positions?.[id];
    if (Number.isFinite(position?.x) && Number.isFinite(position?.y)
        && Math.abs(position.x) <= 1e9 && Math.abs(position.y) <= 1e9) positions[id] = { x: position.x, y: position.y };
  }
  const coverage = record(graph.coverage, COVERAGE_FIELDS);
  coverage.perRepository = (graph.coverage?.perRepository || []).slice(0, 9).map(item => record(item, COVERAGE_FIELDS));
  const repositories = (graph.repositories || []).slice(0, 9).map(item => record(item, ['id', 'name', 'snapshotId']));
  repositories.sort((a, b) => compare(a.id || '', b.id || ''));
  const exploration = record(graph.exploration, ['visitedNodeIds', 'processedNodeIds', 'unprocessedNodeIds', 'consideredFactCount', 'omittedFactCount', 'addedNodes', 'addedEdges',
    'limited', 'runtimeSelectionKnown']);
  const factIds = new Set();
  for (const edge of edges) {
    if (edge.factId) factIds.add(edge.factId);
    for (const id of edge.factIds || []) factIds.add(id);
  }
  exploration.appliedFactIds = [...factIds].sort();
  exploration.coverage = record(graph.exploration?.coverage, COVERAGE_FIELDS);
  exploration.rejectedVisits = (graph.exploration?.rejectedVisits || []).slice(0, 64).map(item => record(item, ['nodeId', 'reason']));
  return { schemaVersion: 'atlas-architecture-v1', projectId: scalar(options.projectId),
    snapshotId: scalar(options.snapshotId), sourceKind: scalar(graph.sourceKind),
    architectureVersion: scalar(graph.architectureVersion), repositories, nodes, edges, coverage,
    exploration, projection, pinnedIds, positions };
}

/**
 * Disambiguate top-level custom-element names while reserving every original public label.
 * @param {object[]} nodes Canonical nodes already sorted by stable identity.
 * @returns {Map<string,string>} Unique readable names; original labels remain atlas.label properties.
 * @throws {Error} Bounded collision attempts are exhausted; no nondeterministic fallback is used.
 */
function elementNames(nodes) {
  const counts = new Map(), reserved = new Set(), used = new Set(), result = new Map();
  for (const node of nodes) {
    const label = String(node.label || node.id).trim() || node.id;
    const key = label.toLowerCase();
    counts.set(key, (counts.get(key) || 0) + 1); reserved.add(key);
  }
  for (const node of nodes) {
    const label = String(node.label || node.id).trim() || node.id;
    if (counts.get(label.toLowerCase()) === 1 && !used.has(label.toLowerCase())) {
      result.set(node.id, label); used.add(label.toLowerCase()); continue;
    }
    let chosen;
    for (let attempt = 0; attempt <= MAX_NODES * 2; attempt++) {
      const suffix = identifier(node.id) + (attempt ? `-${attempt}` : '');
      const candidate = `${label} [${suffix}]`;
      const key = candidate.toLowerCase();
      if (reserved.has(key) || used.has(key)) continue;
      chosen = candidate; used.add(key); break;
    }
    if (!chosen) throw new Error('Architecture element-name collision limit exceeded.');
    result.set(node.id, chosen);
  }
  return result;
}

/**
 * Group parallel equal descriptions because Structurizr forbids duplicate relationship tuples.
 * @param {object[]} edges Canonical directed edges, preserving individual facts in JSON.
 * @returns {object[]} Sorted relationship groups with explicit source-edge multiplicity.
 */
function relationshipGroups(edges) {
  const groups = new Map();
  for (const edge of edges) {
    const description = edge.operation || edge.relation || 'related';
    const key = JSON.stringify([edge.source, edge.target, description]);
    let group = groups.get(key);
    if (!group) {
      group = { key, source: edge.source, target: edge.target, description, count: 0, factIds: new Set() };
      groups.set(key, group);
    }
    group.count += Number.isFinite(edge.weight) && edge.weight > 0 ? edge.weight : 1;
    if (edge.factId) group.factIds.add(edge.factId);
    for (const id of edge.factIds || []) group.factIds.add(id);
  }
  return [...groups.values()].sort((a, b) => compare(a.key, b.key));
}

/**
 * Export a selected architecture model as safe JSON or Structurizr custom-element DSL.
 * @param {object} graph Complete selected architecture nodes/edges and coverage.
 * @param {object} options format ('structurizr'|'json'), snapshotId, projectId, pinnedIds=[], positions={}.
 * @returns {{text:string,filename:string,mimeType:string,losses:string[]}} Deterministic UTF-8 artifact.
 * @throws {Error} Unsupported format, invalid graph or output larger than 4MiB; performs no I/O.
 */
export function architectureExport(graph, options = {}) {
  if (!['json', 'structurizr'].includes(options.format)) throw new Error('Choose Structurizr or Atlas JSON.');
  const model = archive(graph, options), losses = [];
  if (!model.projectId || !model.snapshotId) throw new Error('Architecture project/snapshot identity is required.');
  let text, filename, mimeType;
  if (options.format === 'json') {
    losses.push('Only whitelisted public architecture fields are archived; raw source, credentials, full URLs and connection strings are excluded.');
    text = JSON.stringify(model, null, 2) + '\n';
    filename = 'atlas-architecture.json'; mimeType = 'application/json';
  } else {
    const displayNames = elementNames(model.nodes);
    const names = new Map(), used = new Set(), lines = ['workspace "Atlas architecture" {', '  !impliedRelationships false', '  model {'];
    for (const node of model.nodes) {
      const id = identifier(node.id);
      if (used.has(id)) throw new Error('Architecture DSL identifier collision.');
      used.add(id); names.set(node.id, id);
      lines.push(`    ${id} = element ${quote(displayNames.get(node.id))} ${quote(node.kind || 'unknown')} {`,
        '      properties {',
        `        "atlas.id" ${quote(node.id)}`);
      for (const field of ['label', 'kind', 'resourceKind', 'ownerId', 'scheme', 'status', 'runtimeSelectionKnown']) {
        const value = node[field];
        // Structurizr requires nonempty property values; false remains meaningful evidence.
        if (value === undefined || value === null) continue;
        if (typeof value === 'string' && !value.trim()) continue;
        lines.push(`        "atlas.${field}" ${quote(value)}`);
      }
      lines.push('      }', '    }');
    }
    for (const group of relationshipGroups(model.edges)) {
      lines.push(`    ${names.get(group.source)} -> ${names.get(group.target)} ${quote(group.description)} {`,
        '      properties {', `        "atlas.factCount" ${quote(group.count)}`,
        `        "atlas.factIds" ${quote(JSON.stringify([...group.factIds].sort()))}`, '      }', '    }');
    }
    lines.push('  }', '  views {', `    custom ${identifier('atlas-architecture:' + String(model.projectId))} "Atlas architecture" {`,
      '      include *', '      autoLayout lr', '    }', '  }', '}', '');
    text = lines.join('\n'); filename = 'atlas-architecture.dsl'; mimeType = 'text/plain';
    losses.push('Structurizr custom elements preserve source kinds; no inferred C4 container/deployment semantics.',
      'Geometry and pins are replaced by autoLayout; Atlas JSON preserves session positions.',
      'Detailed source evidence, configuration alternatives, confidence and individual edge records require Atlas JSON.',
      'Parallel edges with equal source, target and description are grouped; atlas.factCount and atlas.factIds retain multiplicity.',
      'Duplicate element names are disambiguated by stable IDs; atlas.label preserves the original public label.');
  }
  if (new TextEncoder().encode(text).length > MAX_BYTES) throw new Error('Architecture export exceeds 4MiB.');
  return { text, filename, mimeType, losses };
}
