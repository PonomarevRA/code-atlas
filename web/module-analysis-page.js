/**
 * A read-only comparison page: request identity and source scope own every result.
 * Text from graphs is rendered as text nodes; algorithms never move source files.
 */
const form = document.querySelector('#module-form');
const project = document.querySelector('#project');
const snapshot = document.querySelector('#snapshot');
const results = document.querySelector('#module-results');
const status = document.querySelector('#module-status');
const projects = new Map();
let generation = 0;
let controller = null;

/** Construct a node without treating graph labels or failures as HTML. */
function element(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  return node;
}

/** Use a real deadline and reject HTTP errors before consuming a result. */
async function request(url, options = {}) {
  const response = await fetch(url, options);
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || value.detail?.[0]?.msg || 'Не удалось прочитать результат.');
  return value;
}

/** Invalidate ignored-abort responses when either the user or the scope changes. */
function resetRequest() {
  generation += 1;
  controller?.abort();
  controller = null;
  results.removeAttribute('aria-busy');
  form.querySelector('button').disabled = false;
  return generation;
}

/** Keep the selected project explicit in the URL without changing its map. */
function selectProject() {
  resetRequest();
  snapshot.value = projects.get(project.value)?.snapshotId || '';
  results.hidden = true;
  results.replaceChildren();
  const url = new URL(location.href);
  if (project.value) url.searchParams.set('repo', project.value);
  else url.searchParams.delete('repo');
  history.replaceState(null, '', url);
  status.textContent = snapshot.value ? 'Выберите область и запустите сравнение.' : 'У проекта нет доступного снимка.';
}

/** Render an observation and its limits before the algorithm comparison. */
function renderReport(report) {
  results.replaceChildren();
  results.append(element('h2', 'Сравнение модулей'));
  const projection = report.projection;
  results.append(element('p', `Снимок: ${report.snapshotId}. Модулей: ${projection.unitTotal}. Покрытие: ${projection.coverage.status}.`));
  results.append(element('p', 'Измерены структура и зависимости; функциональная согласованность требует проверки примеров.'));
  const scroll = element('div'); scroll.className = 'module-table-scroll';
  const table = element('table');
  const head = element('thead'), header = element('tr');
  for (const label of ['Алгоритм', 'Доступность', 'Группы', 'Максимум модулей в группе', 'Q наблюдаемого графа']) {
    const cell = element('th', label); cell.scope = 'col'; header.append(cell);
  }
  head.append(header); table.append(head);
  const body = element('tbody');
  for (const proposal of report.comparison.proposals) {
    const row = element('tr'), metrics = proposal.assessment?.metrics;
    for (const value of [proposal.algorithm, proposal.status === 'available' ? 'Доступен' : 'Недоступен',
      metrics?.groupCount ?? '—', metrics?.maxGroupUnits ?? '—',
      metrics?.directedModularity == null ? '—' : metrics.directedModularity.toFixed(4)]) row.append(element('td', value));
    body.append(row);
  }
  table.append(body); scroll.append(table); results.append(scroll);
  const unitNames = new Map();
  for (const unit of projection.units) {
    const files = unit.files.length ? ' · ' + unit.files.join(', ') : '';
    unitNames.set(unit.id, (unit.displayLabel || unit.label) + files);
  }
  for (const proposal of report.comparison.proposals) {
    const details = element('details');
    details.append(element('summary', proposal.algorithm));
    if (proposal.reason) details.append(element('p', proposal.reason));
    for (const limit of proposal.limitations || []) details.append(element('p', limit));
    if (proposal.partition) {
      const groups = element('ol');
      for (const members of proposal.partition) {
        const labels = members.map(id => unitNames.get(id) || `Модуль ${id} (подпись не загружена)`);
        groups.append(element('li', labels.join(', ')));
      }
      details.append(groups);
      if (projection.unitsTruncated) details.append(element('p', 'Часть подписей не загружена. Сузьте область для полного состава групп.'));
    }
    results.append(details);
  }
  const evidence = element('details');
  evidence.append(element('summary', 'Покрытие и идентичность исходных данных'),
    element('pre', JSON.stringify({snapshotId: report.snapshotId, sourceGraphHash: projection.sourceGraphHash,
      projectionHash: projection.projectionHash, scope: projection.scope, coverage: projection.coverage}, null, 2)));
  results.append(evidence);
  results.hidden = false;
}

/** Run only an explicit request; late responses never replace a new scope. */
form.addEventListener('submit', async event => {
  event.preventDefault();
  const revision = resetRequest();
  if (!snapshot.value) { status.textContent = 'Выберите проект с готовым снимком.'; return; }
  const algorithms = [...form.querySelectorAll('[name="algorithm"]:checked')].map(input => input.value);
  if (!algorithms.length) { status.textContent = 'Выберите хотя бы один алгоритм.'; return; }
  controller = new AbortController();
  const active = controller;
  const timeout = setTimeout(() => active.abort(), 30000);
  form.querySelector('button').disabled = true;
  results.setAttribute('aria-busy', 'true');
  status.textContent = 'Сравниваем выбранный снимок…';
  const folders = document.querySelector('#folders').value.split('\n').map(value => value.trim()).filter(Boolean);
  const level = document.querySelector('#level').value;
  const body = {snapshotId: snapshot.value, level, folders: level === 'folder' && folders.length ? folders : null, algorithms};
  try {
    const report = await request('/api/projects/' + encodeURIComponent(project.value) + '/module-analysis',
      {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal: active.signal});
    if (revision !== generation) return;
    renderReport(report);
    status.textContent = report.projection.coverage.status === 'complete' ? 'Сравнение готово.' : 'Сравнение готово; часть данных неизвестна.';
  } catch (error) {
    if (revision !== generation) return;
    results.hidden = true;
    status.textContent = error.name === 'AbortError' ? 'Сравнение не завершилось за отведённое время. Попробуйте меньшую область.' : error.message;
  } finally {
    clearTimeout(timeout);
    if (revision === generation) {
      controller = null;
      results.removeAttribute('aria-busy');
      form.querySelector('button').disabled = false;
    }
  }
});

/** Invalidate the previous scope as soon as text changes, including before blur. */
function parametersChanged(event) {
  if (event.target !== project) {
    resetRequest();
    results.hidden = true;
    status.textContent = 'Параметры изменены. Запустите сравнение снова.';
  }
}
project.addEventListener('change', selectProject);
document.querySelector('#level').addEventListener('change', event => {
  const classMode = event.target.value === 'class';
  document.querySelector('#folders').disabled = classMode;
  document.querySelector('#folder-help').textContent = classMode
    ? 'Первый вариант анализа классов ограничен 10 000 исходных узлов. Для более крупной карты используйте уровень папок.'
    : 'Необязательно. Одна папка на строку; пути относительно проекта. Для большой карты выберите меньшую область.';
});
form.addEventListener('input', parametersChanged);
form.addEventListener('change', event => {
  if (event.target.tagName !== 'TEXTAREA') parametersChanged(event);
});
window.addEventListener('pagehide', resetRequest);

/** Load registrations once; no rebuilding or analysis is triggered by navigation. */
try {
  const value = await request('/api/projects', {signal: AbortSignal.timeout(15000)});
  for (const item of value.projects) {
    projects.set(item.id, item);
    const option = element('option', item.name); option.value = item.id;
    if (!item.snapshotId) option.disabled = true;
    project.append(option);
  }
  const requested = new URLSearchParams(location.search).get('repo');
  if (projects.has(requested)) project.value = requested;
  selectProject();
} catch (error) {
  status.textContent = 'Не удалось загрузить проекты. ' + error.message;
}
