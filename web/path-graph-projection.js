import { branchLabels } from './path-annotations.js';

/** Один заголовок используется при первом открытии, восстановлении истории и смене режима чтения. */
export function pathReadingTitle(data, entry = null) {
  if (entry) return `Путь от входа · ${entry.label || entry.route || ''}`;
  return data?.rootKind === 'class' ? 'Методы класса' : data?.rootKind === 'field' ? 'Статические данные' : 'Путь выполнения';
}

/** Превращает pinned API path в raw-карточки карты, сохраняя условия, повторы и свидетельства вызовов. */
export function projectExecutionPath(data, rawNodes, entry = null) {
  const steps = new Map((data.steps || []).map(step => [step.nodeId, step]));
  const nodes = [];
  const seen = new Set();
  const root = rawNodes.get(data.rootNodeId);
  const entryId = entry ? `path-entry:${entry.id}` : null;
  if (entry && root && entryId) {
    nodes.push({
      id: entryId,
      label: entry.route || entry.label || 'Вход',
      kind: 'entry',
      entryKind: entry.kind || (entry.httpMethod ? 'http' : ''),
      httpMethod: entry.httpMethod || '',
      file: (entry.evidence || [])[0]?.file || '',
      codeProjectId: root.codeProjectId,
      community: String(root.community ?? 'path'),
    });
    seen.add(entryId);
  }
  for (const step of data.steps || []) {
    const node = rawNodes.get(step.nodeId);
    if (!node || seen.has(node.id)) continue;
    seen.add(node.id);
    nodes.push({ ...node, controlFlow: step.controlFlow, methodFlow: step.methodFlow, events: step.events || [],
      complexity: step.complexity || node.complexity, health: step.health || node.health,
      staticData: step.staticData || node.staticData || node.dataFacts,
      infrastructure: step.infrastructure || [],
      infrastructureTotal: step.infrastructureTotal,
      infrastructureTruncated: step.infrastructureTruncated,
      declarationKind: step.callableFacts?.kind || step.kind,
      callableFacts: step.callableFacts || node.callableFacts,
      documentation: step.documentation, typeContext: step.typeContext,
      file: step.file || node.file, line: step.line || node.line });
  }
  const edges = [];
  if (entryId && seen.has(entryId) && seen.has(data.rootNodeId)) {
    const caption = entry.httpMethod ? `${entry.httpMethod} вход` : 'вход';
    edges.push({ source: entryId, target: data.rootNodeId, relation: 'вход', caption, path: true });
  }
  for (const edge of data.edges || []) {
    if (!seen.has(edge.source) || !seen.has(edge.target)) continue;
    const labels = branchLabels(steps.get(edge.source)?.controlFlow, edge.branchIds || []);
    let caption = labels.join(' · ');
    if (!caption && edge.relation === 'dispatches_to') caption = 'реализация';
    if (!caption) caption = { method: 'метод класса', field: 'поле класса', property: 'свойство класса', reads: 'читает данные', writes: 'записывает поле',
      initializes_from: 'создаётся методом', inherits: 'наследует', inherits_from: 'наследует', extends: 'наследует', implements: 'реализует интерфейс' }[edge.relation] || '';
    if (caption.length > 56) caption = `${caption.slice(0, 55)}…`;
    edges.push({
      source: edge.source,
      target: edge.target,
      relation: edge.relation || 'calls',
      path: true,
      branchIds: edge.branchIds || [],
      caption,
      confidence: edge.confidence,
      graphConfidence: edge.graphConfidence,
      evidence: edge.evidence,
      source_file: edge.sourceFile,
      sourceLine: edge.sourceLine,
      lineKind: edge.lineKind,
    });
  }
  return { nodes, edges };
}
