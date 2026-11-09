/** Use a declared owner to distinguish methods with the same name; missing facts stay unnamed. */
export function qualifiedMethodLabel(node, description, getNode = () => null, fallback = 'Метод') {
  const label = description?.label || node?.label || fallback;
  let owner = description?.typeContext?.name || node?.callableFacts?.declaringType;
  if (!owner && node?.memberFacts?.ownerNodeId) owner = getNode(node.memberFacts.ownerNodeId)?.label;
  if (!owner) return label;
  const shortOwner = owner.split('.').pop();
  if (label.startsWith(`${owner}.`) || label.startsWith(`${shortOwner}.`)) return label;
  return `${shortOwner}.${label.replace(/^\./, '')}`;
}

/** Describe the exact call occurrence independently of its selected implementation. */
export function readingCallContext(site, getNode, getDescription = () => null) {
  const owner = qualifiedMethodLabel(getNode(site?.ownerNodeId), getDescription(site?.ownerNodeId), getNode,
    site?.ownerNodeId || 'Метод');
  const occurrenceId = site?.occurrenceId || '';
  const line = site?.line > 0 ? site.line : '?';
  return { owner, occurrenceId, line, label: `${owner} · строка ${line}`,
    title: `${owner} · строка ${line}${occurrenceId ? ` · вызов ${occurrenceId}` : ''}` };
}

/** Build a bounded trail from reader addresses, without deriving execution order from the graph. */
export function readingBreadcrumbs({ origin, callSite, trail = [], focusId, entryNodeId }, getNode, getDescription) {
  const label = id => qualifiedMethodLabel(getNode(id), getDescription?.(id), getNode, id || 'Варианты');
  const items = [];
  if (entryNodeId && entryNodeId !== origin.ownerNodeId) items.push({ kind: 'entry', label: label(entryNodeId) });
  items.push({ kind: 'call', label: label(origin.ownerNodeId), site: origin });
  for (const [index, frame] of trail.entries()) {
    const id = frame.focusId || frame.callSite?.ownerNodeId;
    if (index === 0 && id === origin.ownerNodeId) continue;
    items.push({ kind: 'frame', label: frame.mode === 'compare' ? 'Варианты' : label(id), index, site: frame.callSite });
  }
  items.push({ kind: 'current', label: focusId ? label(focusId) : 'Варианты', site: callSite });
  return items;
}
