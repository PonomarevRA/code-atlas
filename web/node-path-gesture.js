const CARDS = '.graph-node, .flow-card, .inline-hierarchy-card, .inline-method-card, .path-card, .ingress-node';
const ACTIONS = '[role="button"], [role="link"], a, button, input, select, textarea, .node-action';

/** Resolve only a card body inside this canvas; source links and expansion buttons are separate actions. */
function cardTarget(svg, event) {
  const card = event.target.closest?.(CARDS);
  if (!card || !svg.contains(card)) return null;
  const action = event.target.closest?.(ACTIONS);
  return action && action !== card ? null : card;
}

/** Return the ingress only when this real symbol has one captured entry; never choose among alternatives. */
export function uniqueNodeEntry(entries, nodeId) {
  const matches = entries.filter(entry => entry.handlerNodeIds?.includes(nodeId));
  return matches.length === 1 ? matches[0] : null;
}

/**
 * Open a card's execution path on double-click or Ctrl/Cmd+Enter using delegated SVG events.
 * Real inspection IDs take precedence over occurrence IDs. Nested actions retain their own behavior;
 * aggregate IDs are passed to the host for validation against the current graph.
 */
export function bindNodePathGesture(svg, { onNode, onEntry,
  schedule = (callback, delay) => setTimeout(callback, delay), cancel = id => clearTimeout(id) }) {
  svg.setAttribute('aria-description', 'Двойное нажатие на карточку или Ctrl/Cmd+Enter открывает схему пути от этого узла.');
  let pointerSelection = null, disclosure = null;
  /** Selection stays immediate; postpone only the drawer that would move or cover the second click. */
  function revealSelection(reveal) {
    if (disclosure !== null) cancel(disclosure);
    disclosure = null;
    if (!pointerSelection?.eventPhase) { reveal(); return; }
    disclosure = schedule(() => { disclosure = null; reveal(); }, 500);
  }
  /** Identify selection dispatched by a pointer, without delaying keyboard or list navigation. */
  const markPointerSelection = event => {
    // Native eventPhase stays nonzero through target listeners, then becomes zero after dispatch.
    // A microtask can run between native listeners and would clear the flag before selection.
    pointerSelection = event.detail > 0 && cardTarget(svg, event) ? event : null;
  };
  /** Cancel a pending drawer and route an explicit path gesture through the host's existing navigation. */
  const activate = event => {
    if (event.type === 'keydown' && (event.key !== 'Enter' || !(event.ctrlKey || event.metaKey) || event.repeat)) return;
    const card = cardTarget(svg, event);
    if (!card) return;
    const { inspectionId, rawId, nodeId, ingressId } = card.dataset;
    const id = inspectionId || rawId || nodeId;
    if (!id && !ingressId) return;
    if (disclosure !== null) cancel(disclosure);
    disclosure = null;
    pointerSelection = null;
    event.preventDefault();
    event.stopPropagation();
    if (ingressId) onEntry?.(ingressId);
    else onNode(id);
  };
  svg.addEventListener('dblclick', activate);
  svg.addEventListener('click', markPointerSelection, true);
  // Capture the modified shortcut before a card's ordinary Enter selection consumes it.
  svg.addEventListener('keydown', activate, true);
  /** Remove listeners and a pending disclosure when the host tears down the canvas. */
  function destroy() {
    if (disclosure !== null) cancel(disclosure);
    svg.removeEventListener('dblclick', activate);
    svg.removeEventListener('click', markPointerSelection, true);
    svg.removeEventListener('keydown', activate, true);
  }
  return { revealSelection, destroy };
}
