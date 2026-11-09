import { el } from './dom.js';

/** Render captured literals safely; expression text is evidence, never evaluated. */
function valueElement(value, mapKeys = []) {
  if (!value) return el('span', 'привязка не установлена', 'test-value-missing');
  if (value.kind === 'missing') return el('span', 'не передано', 'test-value-missing');
  if (value.kind === 'null') return el('code', 'null');
  if (value.kind === 'string') return el('code', value.value === '' ? '"" · пустая строка' : JSON.stringify(value.value));
  if (value.kind === 'map') {
    const details = el('details', undefined, 'test-map-value');
    details.open = (value.entries?.length || 0) <= 8;
    details.append(el('summary', `Map · ${value.entries?.length || 0} ключей`));
    const keys = mapKeys.length ? mapKeys : (value.entries || []).map(item => item.key?.value ?? item.key?.expression);
    const list = el('dl');
    for (const key of keys) {
      const item = (value.entries || []).find(candidate => (candidate.key?.value ?? candidate.key?.expression) === key);
      list.append(el('dt', String(key)));
      const cell = el('dd');
      cell.append(item ? valueElement(item.value) : el('span', 'ключ отсутствует', 'test-value-missing'));
      list.append(cell);
    }
    details.append(list);
    return details;
  }
  if (['number', 'boolean'].includes(value.kind)) return el('code', String(value.value ?? value.expression));
  return el('code', value.expression || 'значение не определено');
}

/** Columns come from framework bindings, excluding resolver context and aggregators. */
function bindingColumns(rows, parameters) {
  const columns = new Map();
  for (const parameter of parameters || []) {
    if (parameter.role !== 'indexed') continue;
    columns.set(parameter.id || parameter.name, { ...parameter, key: parameter.id || parameter.name, mapKeys: [] });
  }
  for (const row of rows) for (const binding of row.bindings || []) {
    const key = binding.parameterId || binding.name;
    if (!columns.has(key)) columns.set(key, { ...binding, key, mapKeys: [] });
    const column = columns.get(key);
    for (const item of binding.value?.entries || []) {
      const mapKey = item.key?.value ?? item.key?.expression;
      if (!column.mapKeys.includes(mapKey)) column.mapKeys.push(mapKey);
    }
  }
  return [...columns.values()];
}

/** Show one bounded page with stable row selection and source anchors. */
function renderRows(root, rows, parameters, onSource, fallbackNodeId) {
  root.replaceChildren();
  const columns = bindingColumns(rows, parameters);
  if (!columns.length) {
    for (const row of rows) root.append(el('pre', typeof row === 'string' ? row : row.expression || row.label || 'Значения не сопоставлены с параметрами.', 'test-data-expression'));
    return;
  }
  const table = el('table', undefined, 'test-dataset-table');
  const head = el('thead'), headings = el('tr');
  headings.append(el('th', 'Вариант'));
  for (const column of columns) headings.append(el('th', `${column.name || 'параметр'}${column.type ? ` · ${column.type}` : ''}`));
  headings.append(el('th', 'Источник'));
  head.append(headings); table.append(head);
  const body = el('tbody');
  let selected = null;
  for (const [position, row] of rows.entries()) {
    const line = el('tr');
    const label = el('button', row.label || `Вариант ${(row.index ?? position) + 1}`, 'text-button');
    label.setAttribute('aria-pressed', 'false');
    label.addEventListener('click', () => {
      selected?.setAttribute('aria-pressed', 'false');
      selected = label;
      label.setAttribute('aria-pressed', 'true');
    });
    const title = el('td'); title.append(label); line.append(title);
    for (const column of columns) {
      const binding = (row.bindings || []).find(item => (item.parameterId || item.name) === column.key);
      const cell = el('td'); cell.append(valueElement(binding?.value, column.mapKeys));
      if (binding?.reason) cell.append(el('small', binding.reason));
      line.append(cell);
    }
    const location = row.sourceLocation || row;
    const nodeId = location.nodeId || row.sourceNodeId || fallbackNodeId;
    const cell = el('td');
    if (nodeId && Number.isInteger(location.line)) {
      const source = el('button', `Строка ${location.line}`, 'text-button');
      source.addEventListener('click', () => onSource(nodeId, location.line));
      cell.append(source);
    } else cell.append(el('small', 'Ссылка на строку недоступна'));
    line.append(cell); body.append(line);
  }
  table.append(body); root.append(table);
}

/** A preview stays bounded; optional snapshot-scoped loading replaces one page at a time. */
export function appendDatasetTable(root, source, entry, { onSource = () => {}, loadRows, isCurrent = () => true, onParameters = () => {} } = {}) {
  const section = el('section', undefined, 'test-dataset');
  const summary = el('p', undefined, 'small');
  const rowsRoot = el('div', undefined, 'test-dataset-scroll');
  const status = el('p', undefined, 'small muted');
  section.append(summary, rowsRoot, status);
  const preview = (source.rows || []).slice(0, 20);
  let nextCursor = null, loading = false, hasPage = false;
  const more = el('button', 'Открыть страницы данных', 'text-button');
  /** Replace the visible page, retaining captured count and truncation boundaries. */
  function show(rows, declaredCount = source.declaredRowCount, parameters = entry.parameters) {
    summary.textContent = `Объявленные строки · показано ${rows.length} из ${declaredCount ?? 'неизвестного количества'}`;
    renderRows(rowsRoot, rows, parameters, onSource, source.nodeIds?.[0] || entry.handlerNodeIds?.[0]);
  }
  show(preview);
  const truncated = source.rowsTruncated || (source.rows?.length || 0) > 20 || (source.declaredRowCount ?? 0) > preview.length;
  if (truncated) status.textContent = 'Список сокращён. Значения выражений не вычислялись.';
  /** Failed requests keep retry available; stale selections cannot update their former panel. */
  async function fetchPage() {
    if (loading) return;
    loading = true; more.disabled = true; status.textContent = 'Читаем объявленные строки снимка…';
    try {
      const page = await loadRows(source.id, hasPage ? nextCursor : null);
      if (!isCurrent()) return;
      show((page.rows || []).slice(0, 20), page.declaredRowCount, page.parameters || entry.parameters);
      if (page.parameters) onParameters(page.parameters);
      nextCursor = page.nextCursor || null; hasPage = true;
      more.hidden = !nextCursor;
      more.textContent = 'Следующие строки';
      status.textContent = page.captureTruncated ? 'Захват источника ограничен; остальные значения смотрите в исходнике.' : 'Объявленные варианты; выполнение тестов не проверялось.';
    } catch (error) {
      if (!isCurrent()) return;
      status.textContent = `Строки недоступны: ${error.message}`;
      more.textContent = 'Повторить загрузку строк';
    } finally { loading = false; more.disabled = false; }
  }
  if (loadRows && source.id && (truncated || !preview.length)) {
    more.addEventListener('click', fetchPage); section.append(more);
  }
  root.append(section);
  return section;
}
