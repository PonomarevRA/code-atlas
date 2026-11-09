import {withLoading} from './loading-state.js';
import {requestLoadingLabel} from './loading-labels.js';
/** Отдельная страница авторских XML библиотек; чтение каталога никогда не запускает импорт. */
import {showReferencePage} from './reference-content.js';
const $ = id => document.getElementById(id);
let messages = {}, catalog = {packs: []}, catalogCount = 50, revision = 0, documentRevision = 0, currentDocument, polling = false;

/** Используем текстовые DOM узлы: XML, комментарии и пути не исполняются как разметка. */
function element(tag, text) { const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text); return node; }

/**
 * Читает локальный JSON с неблокирующим учётом всей операции.
 *
 * path — существующий API-маршрут; body задаёт POST, отсутствие body — GET.
 * Возвращает декодированный ответ; исходные сообщения и типы ошибок этой
 * страницы сохранены. Токен освобождается после JSON либо ошибки в finally.
 */
async function request(path, body) {
  return withLoading(requestLoadingLabel(path, {method: body === undefined ? 'GET' : 'POST'}), async () => {
    const response = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || data.detail?.[0]?.msg || messages.error);
    return data;
  });
}

/** Выбранный repository ID задаёт API scope, а не путь файловой системы. */
function prefix() { return `/api/projects/${encodeURIComponent($('project').value)}/library-documentation`; }

/** Локализации интерфейса отделены от языка исходной XML документации. */
async function localize(locale) {
  const english = await (await fetch('/locales/en/libraries.json')).json();
  const response = await fetch(`/locales/${encodeURIComponent(locale)}/libraries.json`);
  messages = {...english, ...(response.ok ? await response.json() : {})};
  document.documentElement.lang = locale;
  for (const node of document.querySelectorAll('[data-i18n]')) node.textContent = messages[node.dataset.i18n];
  document.title = `${messages.title} · Code Atlas`;
  document.dispatchEvent(new CustomEvent('atlas-locale-change', {detail: locale}));
  if (currentDocument) showDocumentText();
}

/** Кнопка освобождается и при ошибке; обработчики не запускают скрытые refresh операции. */
function action(status, run) {
  return async event => {
    event?.preventDefault();
    const button = event?.submitter || event?.currentTarget;
    if (button instanceof HTMLButtonElement) button.disabled = true;
    try { await run(); } catch (error) { $(status).textContent = error.message; }
    finally { if (button instanceof HTMLButtonElement) button.disabled = false; }
  };
}

/** Необязательные XML и отсутствующие packs не вытесняют рабочий каталог с экрана. */
function showWarnings(warnings) {
  $('warnings-panel').hidden = !warnings.length;
  $('warnings').textContent = warnings.join('\n');
}

/** Версия пакета, assembly asset и TFM остаются разными полями при отображении. */
function renderCatalog() {
  $('packs').replaceChildren();
  for (const pack of catalog.packs.slice(0, catalogCount)) {
    const row = element('tr'), library = pack.library;
    const name = element('td', library.package); name.append(element('small', library.assembly), element('small', library.kind)); row.append(name);
    row.append(element('td', pack.version));
    const targets = element('td');
    targets.append(element('span', [...new Set(library.usedBy.map(item => item.target))].join(', ')));
    for (const usage of library.usedBy.slice(0, 3)) targets.append(element('small', usage.manifest));
    if (library.usedBy.length > 3) targets.append(element('small', `+ ${library.usedBy.length - 3}`));
    row.append(targets, element('td', pack.locale), element('td', new Date(pack.importedAt).toLocaleString(document.documentElement.lang)), element('td', pack.documentCount), element('td', `${(pack.sizeBytes / 1048576).toFixed(1)} MiB`));
    $('packs').append(row);
  }
  $('catalog-more').hidden = catalogCount >= catalog.packs.length;
  const selected = $('pack').value;
  $('pack').replaceChildren();
  const all = element('option', messages.allLibraries); all.value = ''; $('pack').append(all);
  for (const pack of catalog.packs) { const option = element('option', `${pack.library.package} · ${pack.version} · ${pack.library.assembly} · ${pack.locale}`); option.value = pack.id; $('pack').append(option); }
  if (catalog.packs.some(pack => pack.id === selected)) $('pack').value = selected;
  $('catalog-status').textContent = catalog.capturedAt ? `${messages.captured}: ${new Date(catalog.capturedAt).toLocaleString()} · ${catalog.packs.length} · ${(catalog.sizeBytes / 1048576).toFixed(1)} MiB` : messages.empty;
  $('storage-path').textContent = `${messages.storage}: ${catalog.storagePath || ''}`;
  showWarnings(catalog.warnings || []);
}

/** Revision исключает показ документации предыдущего repository после переключения. */
async function loadCatalog() {
  if (!$('project').value) { $('catalog-status').textContent = messages.noProject; return; }
  const token = ++revision, base = prefix();
  const data = await request(base);
  if (token !== revision || base !== prefix()) return;
  catalog = data; renderCatalog();
  if (data.selectedManifest && !$('manifest').value) {
    if (![...$('manifest').options].some(option => option.value === data.selectedManifest)) {
      const option = element('option', data.selectedManifest); option.value = data.selectedManifest; $('manifest').append(option);
    }
    $('manifest').value = data.selectedManifest;
  }
  if (data.activeJob && !polling) watch(data.activeJob, base);
}

/** Явное обнаружение показывает доступные .csproj; отсутствующие XML остаются предупреждениями. */
async function discover() {
  const token = ++revision, base = prefix(), manifest = $('manifest').value;
  const data = await request(`${base}/sources?manifest=${encodeURIComponent(manifest)}`);
  if (token !== revision || base !== prefix()) return;
  $('manifest').replaceChildren();
  const all = element('option', messages.allProjects); all.value = ''; $('manifest').append(all);
  for (const path of data.manifests) { const option = element('option', path); option.value = path; $('manifest').append(option); }
  if (data.manifests.includes(manifest)) $('manifest').value = manifest;
  $('source-status').textContent = `${messages.sources}: ${data.sources.length}${data.truncated ? ` · ${messages.partial}` : ''}`;
  showWarnings(data.warnings);
}

/** Ожидание относится к repository, который запустил задачу; переключение прекращает UI polling. */
async function watch(initial, base) {
  if (polling) return;
  polling = true;
  let job = initial;
  try {
    while (job.status === 'running' && base === prefix()) {
      $('job-status').textContent = `${messages.working}: ${job.progress.sources || 0}/${job.progress.totalSources || '…'}`;
      await new Promise(resolve => setTimeout(resolve, 1000));
      if (base !== prefix()) return;
      job = await request(`${base}/jobs/${job.id}`);
    }
    if (base === prefix()) $('job-status').textContent = job.status === 'completed' ? `${messages.completed} · ${job.packCount}` : `${messages.failed}: ${job.error}`;
  } finally { polling = false; await loadCatalog(); }
}

/** Поиск передаёт отдельную локаль документов и показывает честный fallback оригинала. */
async function search() {
  const token = ++revision, base = prefix();
  const locale = $('doc-locale').value;
  const data = await request(`${base}/search`, {query: $('query').value, packId: $('pack').value, locale, mode: $('mode').value, limit: 20});
  if (token !== revision || base !== prefix()) return;
  $('results').replaceChildren();
  $('search-status').textContent = `${data.results.length ? messages.found + ': ' + data.results.length : messages.none} ${messages.candidate} ${data.truncated ? messages.partial : ''}`;
  for (const hit of data.results) {
    const article = element('article'); article.className = 'reference-result';
    const title = element('h3'), button = element('button', hit.title); button.type = 'button';
    button.addEventListener('click', action('search-status', () => openDocument(hit.packId, hit.documentId)));
    title.append(button); article.append(title, element('small', `${hit.library.package} · ${hit.version} · ${hit.locale} · ${hit.library.asset}`), element('pre', hit.signature), element('p', hit.excerpt));
    if (hit.localeFallback) article.append(element('p', `${messages.fallback} (${hit.locale})`));
    $('results').append(article);
  }
}

/** Полный исходный текст читается страницами, с hash/path установленного XML и датой capture. */
async function openDocument(packId, documentId, offset = 0) {
  const token = ++documentRevision, base = prefix();
  const data = await request(`${base}/${encodeURIComponent(packId)}/documents/${encodeURIComponent(documentId)}?offset=${offset}`);
  if (token !== documentRevision || base !== prefix()) return;
  currentDocument = {packId, documentId, nextOffset: data.nextOffset, text: data.text};
  $('document').hidden = false; $('document-title').textContent = data.title;
  $('document-source').textContent = `${data.library.sourcePath} · ${data.pack.version} · ${data.pack.locale} · ${data.library.sourceHash} · ${data.capturedAt}`;
  showDocumentText(); $('document-more').hidden = data.nextOffset === null;
  $('document').scrollIntoView({behavior: 'instant', block: 'start'});
}

/** Ссылка ищет API только в выбранном проекте и текущем каталоге библиотек. */
function showDocumentText() {
  showReferencePage(currentDocument.text, {messages, onReference: uid => action('search-status', async () => {
    $('query').value = uid.slice(0, 500); $('mode').value = 'exact';
    await search(); $('search-form').scrollIntoView({block: 'start'});
  })()});
}

$('project').addEventListener('change', action('catalog-status', async () => {
  ++revision; ++documentRevision; catalogCount = 50;
  $('results').replaceChildren(); $('document').hidden = true; $('warnings').textContent = ''; $('job-status').textContent = ''; $('source-status').textContent = '';
  $('manifest').replaceChildren(); const all = element('option', messages.allProjects); all.value = ''; $('manifest').append(all);
  const url = new URL(location.href); url.searchParams.set('repo', $('project').value); history.replaceState(null, '', url);
  await loadCatalog();
}));
$('discover').addEventListener('click', action('source-status', discover));
$('capture-form').addEventListener('submit', action('job-status', async () => { const base = prefix(); await watch(await request(`${base}/capture`, {manifest: $('manifest').value}), base); }));
$('reload').addEventListener('click', action('catalog-status', loadCatalog));
$('catalog-more').addEventListener('click', () => { catalogCount += 50; renderCatalog(); });
$('search-form').addEventListener('submit', action('search-status', search));
$('ui-locale').addEventListener('change', action('catalog-status', async () => { await localize($('ui-locale').value); renderCatalog(); }));
$('close-document').addEventListener('click', () => { ++documentRevision; $('document').hidden = true; });
$('document-more').addEventListener('click', action('search-status', () => openDocument(currentDocument.packId, currentDocument.documentId, currentDocument.nextOffset)));
await localize('ru');
await action('catalog-status', async () => {
  const data = await request('/api/projects');
  for (const project of data.projects) { const option = element('option', project.name); option.value = project.id; $('project').append(option); }
  const requested = new URLSearchParams(location.search).get('repo');
  if (data.projects.some(project => project.id === requested)) $('project').value = requested;
  for (const id of ['capture', 'discover']) $(id).disabled = !data.projects.length;
  await loadCatalog();
})();
