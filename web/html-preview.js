import { el } from './dom.js';
import { requestJson } from './bounded-request.js';

const SHAPES = new Set(['heading', 'text', 'list', 'item', 'control', 'media', 'link', 'container']);
const MAX_ROWS = 60;
const MAX_DEPTH = 5;

/** Reconstruct a passive sketch with host-owned divs and literal text only. */
export function renderPreviewRows(container, rows) {
  const cards = [];
  for (const [index, row] of (Array.isArray(rows) ? rows.slice(0, MAX_ROWS) : []).entries()) {
    const parent = row?.parent;
    const owner = parent === null && index === 0 ? container : Number.isInteger(parent) && parent < index ? cards[parent] : null;
    if (!owner || !Number.isInteger(row.depth) || row.depth < 0 || row.depth > MAX_DEPTH || !SHAPES.has(row.kind)) continue;
    const card = el('div', undefined, 'html-sketch-row');
    card.dataset.sketchKind = row.kind;
    card.append(el('span', typeof row.label === 'string' ? row.label.slice(0, 96) : 'Элемент'));
    owner.append(card);
    cards[index] = card;
  }
}

/** Add an opt-in sketch for the selected snapshot; closing/reselecting cannot mount a stale response. */
export function renderHtmlPreview(panel, node, {projectId, snapshotId, fetcher} = {}) {
  if (!projectId || !snapshotId || !['page', 'fragment', 'block'].includes(node?.htmlFacts?.role)) return;
  const details = el('details', undefined, 'html-preview');
  const explanation = el('p', 'Схема разметки. Цвета, размеры и оформление задаёт Atlas.', 'small muted');
  const status = el('p', '', 'small muted');
  status.setAttribute('role', 'status');
  const sketch = el('div', undefined, 'html-sketch');
  details.append(el('summary', 'Статический эскиз'), explanation, status, sketch);
  let pending = false, loaded = false;
  details.addEventListener('toggle', async () => {
    if (!details.open || pending || loaded) return;
    pending = true;
    status.textContent = 'Строим эскиз по снимку…';
    try {
      const path = `/api/projects/${encodeURIComponent(projectId)}/html-preview`;
      const payload = await requestJson(path, {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({snapshotId, nodeId: node.id})}, {fetcher, activity: false, timeoutMs: 10000});
      if (!details.isConnected || !details.open) return;
      if (payload.snapshotId !== snapshotId || payload.nodeId !== node.id) throw new Error('Карта изменилась. Выберите элемент снова.');
      renderPreviewRows(sketch, payload.rows);
      status.textContent = payload.truncated ? 'Показана часть структуры.' : '';
      loaded = true;
    } catch (error) {
      if (details.isConnected && details.open) status.textContent = error.message;
    } finally {
      pending = false;
    }
  });
  panel.append(details);
}
