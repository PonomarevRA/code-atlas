const MAX_ITEMS = 5000;
const MAX_TEXT = 2000;
const CARD_SELECTOR = '.graph-node,.community-node,.path-card,.path-reference,.flow-card,.inline-method-card,.inline-hierarchy-card,[data-node-id]';

/** Search each retained card once; caps remain visible even when a truncated tail has no match. */
export function findMapItems(items, query, { maxItems = MAX_ITEMS, maxMatches = 1000, maxTextChars = MAX_TEXT, maxQueryChars = 200 } = {}) {
  const matches = [], keys = new Set(), elements = new Set();
  const needle = String(query || '').toLowerCase();
  if (!needle) return { matches, truncated: false };
  if (needle.length > maxQueryChars) return { matches, truncated: true };
  let inspected = 0, truncated = false;
  for (const item of items || []) {
    if (inspected++ >= maxItems) { truncated = true; break; }
    if (!item || typeof item.key !== 'string' || keys.has(item.key) || (item.element && elements.has(item.element))) continue;
    keys.add(item.key);
    if (item.element) elements.add(item.element);
    const text = String(item.text || '');
    if (item.textTruncated || text.length > maxTextChars) truncated = true;
    if (!text.slice(0, maxTextChars).toLowerCase().includes(needle)) continue;
    if (matches.length >= maxMatches) { truncated = true; break; }
    matches.push(item);
  }
  return { matches, truncated };
}

/** Center a world point in usable SVG pixels, preserving the existing scale exactly. */
export function centeredMapCamera(camera, center, viewport) {
  if (!camera || !center || !viewport) return null;
  const values = [camera.x, camera.y, camera.scale, center.x, center.y, viewport.x, viewport.y, viewport.width, viewport.height];
  if (!values.every(Number.isFinite) || camera.scale <= 0 || viewport.width <= 0 || viewport.height <= 0) return null;
  const x = viewport.x + viewport.width / 2 - center.x * camera.scale;
  const y = viewport.y + viewport.height / 2 - center.y * camera.scale;
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return { x, y, scale: camera.scale };
}

/** Read a card's own visible text, excluding nested cards and tooltip/source descriptions. */
function cardText(group, cardRoots) {
  let text = '', truncated = false;
  const pending = [...(group.childNodes || [])].reverse();
  while (pending.length) {
    const node = pending.pop();
    if (node.nodeType === 3) {
      const value = node.textContent || '';
      if (text.length + value.length > MAX_TEXT) { text += value.slice(0, MAX_TEXT - text.length); truncated = true; break; }
      text += value;
      continue;
    }
    const tag = String(node.tagName || '').toLowerCase();
    if (['title', 'desc', 'script', 'style'].includes(tag) || node.hidden || cardRoots.has(node) || node.matches?.(CARD_SELECTOR)) continue;
    if (node.getAttribute?.('aria-hidden') === 'true' || node.getAttribute?.('display') === 'none' || node.style?.display === 'none') continue;
    const children = node.childNodes || [];
    for (let index = children.length - 1; index >= 0; index--) pending.push(children[index]);
  }
  if (!text) {
    const label = group.getAttribute?.('aria-label') || '';
    text = label.slice(0, MAX_TEXT); truncated ||= label.length > MAX_TEXT;
  }
  return { text, truncated };
}

/** Use stored world geometry: viewport virtualization may have detached the SVG card. */
function cardCenter(point) {
  if (!point) return null;
  const bounds = point.cardBounds;
  if (bounds && [bounds.left, bounds.right, bounds.top, bounds.bottom].every(Number.isFinite)) {
    const x = bounds.left / 2 + bounds.right / 2, y = bounds.top / 2 + bounds.bottom / 2;
    return [x, y].every(Number.isFinite) ? { x, y } : null;
  }
  if (bounds && [bounds.x, bounds.y, bounds.width, bounds.height].every(Number.isFinite)) {
    const x = bounds.x + bounds.width / 2, y = bounds.y + bounds.height / 2;
    return [x, y].every(Number.isFinite) ? { x, y } : null;
  }
  return [point.x, point.y].every(Number.isFinite) ? { x: point.x, y: point.y } : null;
}

/** Adapt existing scene inventories to Find; no raw graph, filtering, selection or data loading. */
export function createMapFindScene(view, { getScope = () => '', getViewport } = {}) {
  return {
    getScope,
    /** Detached offscreen cards remain candidates; separate visual occurrences retain their IDs. */
    getItems() {
      const cards = [], items = [], keys = new Set(), elements = new Set();
      const inventories = [['node', view.nodeElements], ['community', view.communityElements]];
      inventoryLoop:
      for (const [kind, inventory] of inventories) {
        for (const [id, item] of inventory || []) {
          const element = item.group, key = `${kind}:${id}`;
          if (!element || keys.has(key) || elements.has(element) || element.hidden) continue;
          if (element.getAttribute?.('display') === 'none' || element.style?.display === 'none') continue;
          const center = cardCenter(item.point || view.positions?.get(id) || view.positions?.get(String(id)));
          if (!center) continue;
          keys.add(key); elements.add(element);
          cards.push({ key, element, center });
          // One sentinel lets the bounded matcher report incomplete inventory honestly.
          if (cards.length > MAX_ITEMS) break inventoryLoop;
        }
      }
      // Inventory identity excludes nested extension cards even when their CSS names are custom.
      for (const card of cards) {
        const content = cardText(card.element, elements);
        items.push({ ...card, text: content.text, textTruncated: content.truncated });
      }
      return items;
    },
    getViewport: getViewport || (() => ({ x: 0, y: 0, width: view.svg.clientWidth, height: view.svg.clientHeight })),
    getCamera: () => ({ ...view.camera }),
    /** Pan only: keep zoom and clear the anchored tooltip before virtualized elements move. */
    setCamera(camera) {
      if (![camera?.x, camera?.y, camera?.scale].every(Number.isFinite) || camera.scale <= 0 || camera.scale !== view.camera?.scale) return false;
      view.mapTooltip?.hide(true);
      view.camera = { ...camera };
      view.transform();
      view.onCameraChange?.({ ...camera }, { kind: 'pan', source: 'map-find' });
      return true;
    },
  };
}
