/** Local facts UI keeps run/project identity explicit and renders every source as text. */
import { requestJson } from './bounded-request.js';
import { renderResearchDocument } from './research-document.js';
const $ = id => document.getElementById('research-' + id);
const params = new URLSearchParams(location.search);
const labels = { queued:'В очереди', running:'Собираем факты', completed:'Готово', failed:'Ошибка', stale:'Источники изменились', cancelled:'Отменено', interrupted:'Остановлено · можно продолжить' };
let projects = [], workflows = [], currentRun = '', revision = 0, timer;
const element = (tag, text) => { const item = document.createElement(tag); if (text != null) item.textContent = text; return item; };
const post = (url, body = {}) => requestJson(url, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
const project = () => projects.find(item => item.id === $('project').value);

/** Errors stay visible and never replace a completed document with guessed prose. */
async function action(task) {
  try { await task(); } catch (error) { $('message').textContent = error.message; }
}

/** History is scoped to the selected project; delayed responses cannot cross the selector boundary. */
async function history() {
  const id = project()?.id;
  if (!id) return;
  const data = await requestJson('/api/research/runs?projectId=' + encodeURIComponent(id));
  if (project()?.id !== id) return;
  $('history').replaceChildren();
  for (const run of data.runs) {
    const button = element('button', `${labels[run.status] || run.status} · ${run.question || run.nodeId}`);
    button.addEventListener('click', () => action(() => watch(run.id)));
    $('history').append(button);
  }
}

/** Captured facts remain readable after source or mod changes; source links open only host-relative URLs. */
function render(run) {
  $('result').hidden = false;
  $('title').textContent = run.document?.title || 'Исследование';
  $('progress').textContent = `${labels[run.status] || run.status} · ${run.phase}${run.error ? ' · ' + run.error : ''}`;
  $('cancel').hidden = !['queued','running','interrupted','failed'].includes(run.status);
  $('resume').hidden = !['interrupted','failed'].includes(run.status);
  $('freshness').hidden = !run.document;
  $('download').hidden = !run.document;
  $('download').href = `/api/research/runs/${encodeURIComponent(run.id)}/document.md`;
  renderResearchDocument($('document'), run.document?.markdown || '');
  $('evidence').replaceChildren();
  for (const source of run.evidence || []) {
    const card = element('details'); card.id = source.id;
    card.append(element('summary', `${source.label} · ${source.kind} · ${source.file || source.version || 'версия не указана'}`));
    card.append(element('p', source.sourceHash || 'Хэш исходника недоступен'));
    card.append(element('pre', source.source || source.text || source.sourceWarning || 'Исходник недоступен'));
    const cite = run.document?.citations.find(item => item.id === source.id);
    if (cite?.url.startsWith('/') && !cite.url.startsWith('//')) {
      const link = element('a', 'Открыть источник в Atlas'); link.href = cite.url; card.append(link);
    }
    $('evidence').append(card);
  }
}

/** Poll only the selected run while active; a project switch invalidates older responses. */
async function watch(id, expected = ++revision) {
  if (expected !== revision) return;
  clearTimeout(timer); currentRun = id;
  const run = await requestJson('/api/research/runs/' + encodeURIComponent(id));
  if (expected !== revision || run.projectId !== project()?.id) return;
  render(run);
  if (['queued','running'].includes(run.status)) timer = setTimeout(() => action(() => watch(id, expected)), 1000);
  else await history();
}

/** Existing snapshot search finds raw IDs without reindexing or loading project code as a plugin. */
async function search() {
  const selected = project(), query = $('query').value.trim();
  if (!selected || !query) return;
  const data = await post(`/api/projects/${selected.id}/search-nodes`, { snapshotId:selected.snapshotId, query, limit:20, excludeKinds:['field'] });
  if (selected.id !== project()?.id) return;
  $('node').replaceChildren(new Option('По вопросу, без выбранного символа', ''));
  for (const node of data.nodes) $('node').append(new Option(`${node.label} · ${node.file || ''}:${node.line || '?'}`, node.id));
  $('message').textContent = data.nodes.length ? `Найдено ${data.total}; выберите нужный символ.` : 'Совпадений нет. Попробуйте имя метода или путь файла.';
}

$('search').addEventListener('click', () => action(search));
$('project').addEventListener('change', () => action(async () => {
  revision++; currentRun = ''; clearTimeout(timer); $('result').hidden = true;
  $('node').replaceChildren(new Option('По вопросу, без выбранного символа', '')); await history();
}));
$('start').addEventListener('click', () => action(async () => {
  const selected = project(), workflow = workflows[$('workflow').value];
  if (!selected || !workflow) throw new Error('Выберите проект и включённый процесс мода.');
  $('start').disabled = true;
  try {
    const run = await post('/api/research/runs', { projectId:selected.id, snapshotId:selected.snapshotId,
      modId:workflow.modId, fingerprint:workflow.fingerprint, workflowId:workflow.id,
      nodeId:$('node').value, question:$('question').value, maxNodes:Number($('nodes').value),
      maxDepth:Number($('depth').value), requestKey:crypto.randomUUID() });
    if (project()?.id === selected.id) { $('freshness-result').textContent = ''; await watch(run.id); }
  } finally { $('start').disabled = false; }
}));
for (const operation of ['cancel','resume']) $(operation).addEventListener('click', () => action(async () => {
  if (!currentRun) return;
  await post(`/api/research/runs/${currentRun}/${operation}`); await watch(currentRun);
}));
$('freshness').addEventListener('click', () => action(async () => {
  const id = currentRun;
  const result = await requestJson(`/api/research/runs/${id}/freshness`);
  if (currentRun !== id) return;
  let message = 'Текущие исходники совпадают с захваченными. Документ не изменён.';
  if (result.status === 'stale') message = 'Исходники изменились: ' + result.changedFiles.join(', ');
  else if (result.status === 'partial') message = 'Не все исходники удалось проверить: ' + result.unavailableFiles.join(', ') + '. Документ не изменён.';
  $('freshness-result').textContent = message;
}));
await action(async () => {
  const [catalog, capabilities] = await Promise.all([requestJson('/api/projects'), requestJson('/api/research/capabilities')]);
  projects = catalog.projects.filter(item => item.snapshotId); workflows = capabilities.workflows;
  for (const item of projects) $('project').append(new Option(item.name, item.id));
  if (projects.some(item => item.id === params.get('repo'))) $('project').value = params.get('repo');
  for (const [index, workflow] of workflows.entries()) {
    $('workflow').append(new Option(workflow.label, String(index)));
    if (workflow.modId === params.get('mod') && workflow.id === params.get('workflow')) $('workflow').value = String(index);
  }
  $('capabilities').textContent = !capabilities.engineAvailable ? 'В этой сборке недоступен LangGraph.' : workflows.length ? 'Оба процесса используют локальные факты; модель не подключена.' : 'Включите «Помощник по проекту · LangGraph» и при необходимости «Автоматическая документация» на странице модов.';
  $('start').disabled = !capabilities.engineAvailable || !workflows.length;
  if (params.get('node') && project()) {
    const data = await post(`/api/projects/${project().id}/graph-node`, { snapshotId:project().snapshotId, nodeId:params.get('node') });
    if (data.node) { $('node').append(new Option(data.node.label, data.node.id)); $('node').value = data.node.id; }
  }
  await history();
});
window.addEventListener('pagehide', () => { revision++; clearTimeout(timer); });
