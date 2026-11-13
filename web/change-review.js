import {createReviewMap} from './change-review-map.js';

const element = id => document.getElementById(id);
const status = element('review-status');
const more = element('review-more');
const id = new URLSearchParams(location.search).get('id');
const kinds = {added: 'Добавлена', removed: 'Удалена', modified: 'Изменена', unconfirmed: 'Не удалось сопоставить'};
let nextOffset = null;
let reviewMap = null;

/** Create text-only UI nodes for data received from a local repository. */
function text(tag, value, className) {
  const node = document.createElement(tag);
  node.textContent = value;
  if (className) node.className = className;
  return node;
}

/** Request one immutable page, with paths included only in the initial request. */
async function read(offset, includePaths = false) {
  const response = await fetch(`/api/change-reviews/${encodeURIComponent(id)}?offset=${offset}&limit=50&includePaths=${includePaths}`);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Не удалось открыть обзор изменений.');
  return result;
}

/** Format a position from the recorded version, without linking to the current file. */
function locationText(item) {
  if (!item?.file) return 'Позиция недоступна';
  if (!item.line) return item.file;
  const end = item.endLine && item.endLine !== item.line ? `–${item.endLine}` : '';
  return `${item.file}:${item.line}${end}`;
}

/** List captured source events and outgoing links separately for this function. */
function renderStep(step, outgoing, labels, isEntry) {
  const details = document.createElement('details');
  details.open = isEntry;
  details.append(text('summary', step.label));
  details.append(text('p', locationText(step), 'muted review-meta'));
  if (!step.sourceVerified) details.append(text('p', 'Тело функции не подтверждено для этого снимка.', 'muted'));
  if (step.events.length) {
    const events = document.createElement('ul');
    for (const event of step.events) {
      const prefix = event.kind === 'branch' ? 'Ветка · ' : event.conditional ? 'Условно · ' : '';
      const line = event.line ? `${event.line} · ` : '';
      events.append(text('li', prefix + line + (event.text || event.kind), 'review-event'));
    }
    details.append(events);
  }
  if (outgoing.length) {
    const links = document.createElement('ul');
    for (const edge of outgoing) {
      const kind = edge.relation === 'dispatches_to' ? 'Вариант реализации' : 'Вызов';
      const confidence = edge.confidence === 'candidate' ? ' (возможный)' : '';
      links.append(text('li', `${kind}${confidence} → ${labels.get(edge.target) || edge.target}`));
    }
    details.append(links);
  }
  if (step.eventsTruncated || step.unresolvedCount) {
    details.append(text('p', 'Некоторые события или связи не раскрыты.', 'muted'));
  }
  return details;
}

/** Render one side using explicit graph connections, without implying call order. */
function renderSide(heading, side, path) {
  const pane = document.createElement('article');
  pane.className = 'review-side';
  pane.append(text('h3', heading));
  if (!side) {
    pane.append(text('p', 'Итоговый снимок ещё не сохранён.', 'muted'));
    return pane;
  }
  const date = new Date(side.snapshotCreatedAt || side.capturedAt).toLocaleString('ru-RU');
  pane.append(text('p', `Снимок ${side.snapshotId} · ${date}`, 'muted review-meta'));
  const {verifiedFiles, capturedFiles} = side.coverage;
  if (verifiedFiles < capturedFiles) {
    pane.append(text('p', `Сверено файлов снимка: ${verifiedFiles} из ${capturedFiles}.`, 'muted'));
  }
  if (!path) {
    pane.append(text('p', 'Точка входа с прежней сигнатурой не найдена однозначно.', 'muted'));
    return pane;
  }
  const labels = new Map();
  const outgoing = new Map();
  for (const step of path.steps) labels.set(step.nodeId, step.label);
  for (const edge of path.edges) {
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
    outgoing.get(edge.source).push(edge);
  }
  for (const step of path.steps) {
    pane.append(renderStep(step, outgoing.get(step.nodeId) || [], labels, step.nodeId === side.entry?.nodeId));
  }
  if (path.truncated) pane.append(text('p', 'Показана часть статического пути.', 'muted'));
  return pane;
}

/** Append comparison rows and return the first new map file control for focus recovery. */
function appendFunctions(result) {
  const list = element('review-functions');
  for (const row of result.changes) {
    const item = document.createElement('li');
    item.append(text('strong', `${kinds[row.kind]} · ${(row.after || row.before).label}`));
    const positions = document.createElement('div');
    positions.className = 'review-locations';
    if (row.before) positions.append(text('span', 'До · ' + locationText(row.before), 'review-location'));
    if (row.after) positions.append(text('span', 'После · ' + locationText(row.after), 'review-location'));
    item.append(positions);
    list.append(item);
  }
  nextOffset = result.nextOffset;
  more.hidden = nextOffset === null;
  return reviewMap?.append(result.changes);
}

/** Render the saved baseline or the complete review without consulting live source. */
function renderReview(result) {
  element('review-title').textContent = result.title;
  element('review-result').hidden = false;
  element('review-paths').replaceChildren(
    renderSide('До', result.before, result.paths.before),
    renderSide('После', result.after, result.paths.after),
  );
  if (result.status !== 'complete') {
    status.textContent = 'Базовый снимок сохранён. Агент ещё не завершил обзор.';
    return;
  }
  status.textContent = '';
  element('review-diff').hidden = false;
  const {added = 0, removed = 0, modified = 0, unconfirmed = 0} = result.counts;
  const unknown = unconfirmed ? ` · не сопоставлено: ${unconfirmed}` : '';
  element('review-counts').textContent = result.totalChanges
    ? `Изменено: ${modified} · добавлено: ${added} · удалено: ${removed}` + unknown
    : 'Изменений среди сопоставленных функций нет.';
  if (result.before.coverage.partial || result.after.coverage.partial) {
    element('review-coverage').textContent =
      `Покрытие частичное. Сохранено функций до: ${result.before.coverage.functions}, после: ${result.after.coverage.functions}. Добавление и удаление относятся к содержимому этих снимков.`;
  }
  if (result.totalChanges) {
    const map = element('review-map');
    reviewMap = createReviewMap(map, {text, kinds, locationText, result});
    map.hidden = false;
  }
  appendFunctions(result);
  if (nextOffset !== null && result.totalChanges <= 100) loadMore(false);
}

/** Load another page without allowing duplicate requests from rapid activations. */
async function loadMore(focusOnEnd = true) {
  if (nextOffset === null || more.disabled) return;
  more.disabled = true;
  try {
    const first = appendFunctions(await read(nextOffset));
    if (focusOnEnd && nextOffset === null) first?.focus();
    status.textContent = nextOffset === null ? 'Показаны все изменённые функции.' : 'Загружены следующие функции.';
  } catch (error) {
    status.textContent = error.message;
  } finally {
    more.disabled = false;
  }
}

more.addEventListener('click', () => loadMore());
if (id === null) {
  element('review-start').hidden = false;
} else if (!/^[a-f0-9]{32}$/.test(id)) {
  status.textContent = 'Ссылка на обзор имеет неверный формат.';
} else {
  status.textContent = 'Загружаем обзор…';
  read(0, true).then(renderReview).catch(error => { status.textContent = error.message; });
}
