/** Сворачивает подпроекты в репозитории, сохраняя внешние зависимости и их основания. */
export function solutionArchitecture(data, primary) {
  const nodes = new Map();
  const owners = new Map();
  const projects = new Map();
  for (const node of data.nodes || []) {
    if (node.kind === 'package') continue;
    if (node.kind !== 'project') { nodes.set(node.id, { ...node }); continue; }
    const repositoryId = node.repositoryId || primary.id;
    const id = `solution:${repositoryId}`;
    owners.set(node.id, id);
    projects.set(node.id, node);
    if (!nodes.has(id)) nodes.set(id, {
      id, kind: 'project', solution: true, codeProjectId: null,
      label: node.repositoryName || primary.name, repositoryId,
      repositoryName: node.repositoryName || primary.name, memberProjects: [],
    });
    nodes.get(id).memberProjects.push({ id: node.originalCodeProjectId || node.codeProjectId, label: node.label, repositoryId });
  }
  const edges = new Map();
  for (const edge of data.edges || []) {
    const source = owners.get(edge.source) || edge.source;
    const target = owners.get(edge.target) || edge.target;
    if (source === target || !nodes.has(source) || !nodes.has(target)) continue;
    const key = JSON.stringify([source, target, edge.relation]);
    if (!edges.has(key)) edges.set(key, { source, target, relation: edge.relation, weight: 0, evidence: [], evidenceTruncated: false, participants: [] });
    const merged = edges.get(key);
    merged.weight += edge.weight || 1;
    merged.evidenceTruncated ||= Boolean(edge.evidenceTruncated);
    for (const endpoint of [edge.source, edge.target]) {
      const project = projects.get(endpoint);
      if (project && !merged.participants.some(item => item.id === project.id)) merged.participants.push({ id: project.id, label: project.label });
    }
    for (const evidence of edge.evidence || []) {
      if (merged.evidence.length < 50) merged.evidence.push({ ...evidence });
      else merged.evidenceTruncated = true;
    }
  }
  return { ...data, nodes: [...nodes.values()], edges: [...edges.values()] };
}
