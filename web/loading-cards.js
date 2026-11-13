import { el } from './dom.js';

/** Render decorative placeholders alongside the caller's existing loading announcement. */
export function appendLoadingCards(container, count = 1) {
  for (let index = 0; index < count; index++) {
    const card = el('div', undefined, 'ingress-catalog-card atlas-loading-card');
    card.setAttribute('aria-hidden', 'true');
    for (const shape of ['heading', 'line', 'actions']) card.append(el('span', undefined, `atlas-loading-card-${shape}`));
    container.append(card);
  }
}
