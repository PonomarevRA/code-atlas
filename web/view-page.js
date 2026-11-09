import { el, $ } from './dom.js';
import { renderSavedView } from './saved-view-renderer.js';
import { requestJson } from './bounded-request.js';
let snapshot = null;
let zoom = 1;
let fittedWidth = 0;
/** Масштабируется только viewport SVG, координаты сохранённой карты неизменны. */
function resize() {
  if (!snapshot) return;
  const svg = $('saved-graph');
  svg.style.width = `${fittedWidth * zoom}px`;
  svg.style.height = `${fittedWidth * zoom * snapshot.layout.height / snapshot.layout.width}px`;
}
/** Одна измеренная база делает обе кнопки масштаба монотонными даже для больших карт. */
function fit() {
  if (!snapshot) return;
  fittedWidth = Math.max(1, $('saved-graph').parentElement.clientWidth - 2);
  zoom = 1; resize();
}
/** Инспектор использует только содержимое сохранённого артефакта. */
function select(node, { annotation = false } = {}) {
  const panel = $('saved-details');
  panel.replaceChildren(el('h2', node.label || node.title));
  if (annotation) {
    panel.append(el('p', 'Пояснение автора · не факт графа', 'pill'), el('pre', node.text, 'documentation-text'));
  } else {
    panel.append(el('p', `Тип: ${node.kind || '—'}`), el('p', `${node.file || 'Исходный файл не указан'}${node.line ? `:${node.line}` : ''}`), el('p', `ID: ${node.id}`, 'small muted'));
    if (node.description) panel.append(el('p', node.description));
  }
}
/** Загружает только immutable JSON; текущий граф репозитория не нужен. */
async function load() {
  const controls = ['saved-zoom-in','saved-zoom-out','saved-fit'];
  for (const id of controls) $(id).disabled = true;
  $('saved-retry').hidden = true;
  $('saved-status').textContent = 'Загружаем снимок…';
  try {
    const id = new URLSearchParams(location.search).get('id');
    if (!id) throw new Error('В ссылке не указан ID снимка.');
    const data = await requestJson(`/api/views/${encodeURIComponent(id)}`);
    snapshot = data.view || data;
    $('saved-title').textContent = snapshot.title;
    $('saved-query').textContent = snapshot.query ? `Запрос: ${snapshot.query}` : '';
    $('saved-provenance').textContent = `Снимок исходного графа: ${snapshot.snapshotId} · создан ${snapshot.createdAt} · ID ${snapshot.id}`;
    const limited = snapshot.metadata?.truncated;
    $('saved-status').textContent = `${snapshot.nodes.length} узлов · ${snapshot.edges.length} связей · ${(snapshot.annotations || []).length} пояснений${limited ? ' · область ограничена настройками выборки' : ''}.`;
    if (snapshot.metadata?.kindFilterWarning) $('saved-status').append(el('p', snapshot.metadata.kindFilterWarning, 'small muted'));
    const selection = snapshot.metadata?.selection;
    if (selection) $('saved-provenance').append(el('span', ` · Глубина ${selection.depth}, лимит ${selection.maxNodes} узлов; исключены типы: ${(selection.excludeKinds || []).join(', ') || 'нет'}. Исключено по ID: ${(selection.excludeNodeIds || []).length}. Окружение включает входящие и исходящие связи.`));
    $('saved-png').href = `/api/views/${encodeURIComponent(snapshot.id)}/image.png`;
    $('saved-png').hidden = false;
    renderSavedView($('saved-graph'), snapshot, select);
    fit();
    for (const id of controls) $(id).disabled = false;
  } catch (error) { $('saved-status').textContent = error.message; $('saved-retry').hidden = false; }
}
$('saved-zoom-in').addEventListener('click', () => { zoom = Math.min(3, zoom * 1.3); resize(); });
$('saved-zoom-out').addEventListener('click', () => { zoom = Math.max(.2, zoom / 1.3); resize(); });
$('saved-fit').addEventListener('click', fit);
$('saved-retry').addEventListener('click', load);
window.addEventListener('resize', () => { if (zoom === 1) fit(); });
load();
