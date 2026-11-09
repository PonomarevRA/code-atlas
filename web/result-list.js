import { el } from './dom.js';
import { complexityPresentation } from './complexity-badge.js';

/**
 * Build one accessible result button; the score tooltip explains local complexity and uncertainty.
 * @param {object} node Raw graph symbol with optional complexity facts.
 * @param {string|null} selectedId Current real symbol ID.
 * @param {Function} onSelect Receives the real symbol ID on activation.
 * @param {string} context File or short result context displayed below the name.
 * @returns {HTMLButtonElement} Button with no nested focusable controls.
 */
export function createNodeResult(node, selectedId, onSelect, context = node.file || 'Без исходного файла') {
  const button = el('button', undefined, 'result');
  button.type = 'button';
  button.dataset.nodeId = node.id;
  const metric = complexityPresentation(node.complexity);
  button.setAttribute('aria-label', `${node.label || node.id} · сложность ${metric.label}`);
  button.setAttribute('aria-description', `${metric.description} ${context}`);
  button.title = metric.description;
  const badge = el('span', metric.label, 'result-complexity');
  badge.style.backgroundColor = metric.color;
  badge.title = metric.description;
  badge.setAttribute('aria-label', metric.description);
  badge.dataset.complexityScore = metric.score ?? '';
  badge.dataset.complexityStatus = metric.status;
  const heading = el('span', undefined, 'result-heading');
  heading.append(badge, el('strong', node.label || node.id));
  button.append(heading, el('small', context));
  setResultSelected(button, node.id === selectedId);
  button.addEventListener('click', () => onSelect(node.id));
  return button;
}

/**
 * Keep visual and assistive selection synchronized after map and list interactions.
 * @param {HTMLButtonElement} button Existing result row.
 * @param {boolean} selected Whether this row represents the active real symbol.
 */
export function setResultSelected(button, selected) {
  button.classList.toggle('selected', selected);
  button.setAttribute('aria-pressed', String(selected));
}

/**
 * Select the real symbol, then center its visible occurrence without opening code or changing zoom.
 * @param {object} view Graph view containing the current scene and camera.
 * @param {string} id Real symbol ID, distinct from a sequence occurrence ID.
 * @param {Function} select Existing selection callback; rejected selections leave the camera alone.
 */
export function activateResultCard(view, id, select) {
  select(id, true, view.sequenceInspectionIds?.has(id) || false);
  if (!view.pathSceneActive || view.selectedId !== id) return;
  let occurrenceId = null;
  for (const [key, item] of view.nodeElements) {
    if (item.inspectionId !== id) continue;
    occurrenceId ??= key;
    // Prefer the method header over an arbitrary statement belonging to the same method.
    if (view.pathSequenceScene?.byId.get(key)?.kind === 'method') { occurrenceId = key; break; }
  }
  view.focusPathNode(occurrenceId ?? id);
}
