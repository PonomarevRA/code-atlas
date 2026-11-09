import {withLoading} from './loading-state.js';
import {requestLoadingLabel} from './loading-labels.js';
/** Локальный каталог версий: начальный индекс готовится автоматически, редакции обновляются явно. */
import {showReferencePage} from './reference-content.js';
const $ = id => document.getElementById(id);
let messages = {}, catalog, currentDocument, searchRevision = 0, documentRevision = 0, localeRevision = 0, polling = false;

/** Текст интерфейса загружается независимо от корпуса; неизвестный locale использует English. */
async function localize(locale) {
  const revision = ++localeRevision;
  const response = await fetch(`/locales/${encodeURIComponent(locale)}/references.json`);
  if (!response.ok) throw new Error('Locale unavailable');
  const translated = await response.json();
  const english = locale === 'en' ? translated : await (await fetch('/locales/en/references.json')).json();
  if (revision !== localeRevision) return;
  messages = {...english, ...translated}; document.documentElement.lang = locale;
  for (const element of document.querySelectorAll('[data-i18n]')) element.textContent = messages[element.dataset.i18n] || element.dataset.i18n;
  document.title = `${messages.title} · Code Atlas`;
  document.dispatchEvent(new CustomEvent('atlas-locale-change', {detail:locale}));
  $('ui-locale').value = locale; $('doc-locale').value = locale;
  if (currentDocument) showDocumentText();
}

/**
 * Читает локальный JSON с неблокирующим учётом всей операции.
 *
 * path — существующий API-маршрут; body задаёт POST, отсутствие body — GET.
 * Возвращает декодированный ответ; исходные сообщения и типы ошибок этой
 * страницы сохранены. Токен освобождается после JSON либо ошибки в finally.
 */
async function request(path, body) {
  return withLoading(requestLoadingLabel(path, {method: body === undefined ? 'GET' : 'POST'}), async () => {
    const response = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || data.detail?.[0]?.msg || messages.error);
    return data;
  });
}

/** Создаёт текстовые DOM-элементы; документация никогда не вставляется как HTML. */
function element(tag, text) { const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text); return node; }

/** Ошибка остаётся рядом с операцией, кнопка освобождается и при отказе API. */
function action(status, fn) {
  return async event => {
    event?.preventDefault();
    const button = event?.currentTarget instanceof HTMLButtonElement ? event.currentTarget : event?.submitter;
    if (button) button.disabled = true;
    try { await fn(); } catch (error) { $(status).textContent = error.message; }
    finally { if (button) button.disabled = false; }
  };
}

/** Отображает версии, источник, дату и охват отдельно от состояния смыслового индекса. */
async function loadCatalog() {
  catalog = await request('/api/language-references');
  const selected = $('pack').value;
  $('packs').replaceChildren(); $('pack').replaceChildren();
  const all = element('option', messages.all); all.value = ''; $('pack').append(all);
  for (const pack of catalog.packs) {
    const row = element('tr'), name = element('td'), source = element('a', pack.title);
    source.href = pack.sourceUrl; source.target = '_blank'; source.rel = 'noopener noreferrer';
    name.append(source, element('small', `${pack.language} · ${pack.locale} · ${pack.bundled ? messages.bundled : messages.user}`)); row.append(name);
    for (const text of [`${pack.version} · ${pack.edition}`, new Date(pack.importedAt).toLocaleString(document.documentElement.lang), pack.documentCount.toLocaleString(), `${(pack.sizeBytes / 1024 / 1024).toFixed(1)} MiB`]) row.append(element('td', text));
    const index = element('td');
    if (pack.vectorIndex) {
      index.append(element('span', messages.ready), element('small', `${pack.vectorIndex.documentCount} · ${messages.summaryCoverage}`));
    } else {
      const preparing = catalog.activeJob?.operation === 'index-missing';
      index.append(element('span', preparing ? messages.initialIndexing : messages.notIndexed));
    }
    const build = element('button', messages.build); build.type = 'button'; build.disabled = !catalog.modelAvailable || Boolean(catalog.activeJob);
    if (!catalog.modelAvailable) build.title = messages.noModel;
    build.addEventListener('click', action('job-status', async () => watch(await request(`/api/language-references/${pack.id}/index`, {}))));
    index.append(build); row.append(index); $('packs').append(row);
    const option = element('option', `${pack.language} · ${pack.version} · ${pack.locale} · ${pack.edition} · ${pack.title}`); option.value = pack.id; $('pack').append(option);
  }
  if (catalog.packs.some(p => p.id === selected)) $('pack').value = selected;
  const docLocale = $('doc-locale').value || document.documentElement.lang;
  $('doc-locale').replaceChildren();
  for (const locale of [...new Set(['ru', 'en', ...catalog.packs.map(p => p.locale)])]) {
    const option = element('option', {ru:'Русский',en:'English'}[locale] || locale); option.value = locale; $('doc-locale').append(option);
  }
  $('doc-locale').value = docLocale;
  $('catalog-status').textContent = catalog.packs.length ? '' : messages.empty;
  const importLanguage = $('import-language').value;
  $('import-language').replaceChildren();
  for (const language of catalog.languages) { const option = element('option', language); option.value = language; $('import-language').append(option); }
  if (catalog.languages.includes(importLanguage)) $('import-language').value = importLanguage;
  if (catalog.activeJob && !polling) watch(catalog.activeJob);
  else if (!polling && !$('job-status').textContent && catalog.initialIndexing) {
    const initial = catalog.initialIndexing;
    if (initial.status === 'completed') $('job-status').textContent = messages.initialIndexReady;
    else if (initial.status === 'failed') $('job-status').textContent = `${messages.failed}: ${initial.error}`;
  }
}

/** Фоновая задача отображает реальный прогресс; 404 после restart ведёт к каталогу. */
async function watch(initial) {
  if (polling) return;
  polling = true;
  let job = initial;
  try {
    while (job.status === 'running') {
      if (job.operation === 'index-missing') {
        const progress = job.progress;
        $('job-status').textContent = `${messages.initialIndexing} · ${progress.processedPacks || 0}/${progress.totalPacks || 0} · ${progress.title || ''} · ${progress.indexed || 0}/${progress.total || 0}`;
      } else $('job-status').textContent = `${messages.working} · ${job.operation} · ${JSON.stringify(job.progress)}`;
      await new Promise(resolve => setTimeout(resolve, 1200));
      job = await request(`/api/language-references/jobs/${job.id}`);
      // Каждый завершённый корпус доступен сразу, не только в конце всей очереди.
      if (job.operation === 'index-missing') await loadCatalog();
    }
    $('job-status').textContent = job.status === 'completed' ? messages.completed : `${messages.failed}: ${job.error}`;
  } catch (error) { $('job-status').textContent = error.message; }
  finally { polling = false; await loadCatalog(); }
}

/** Поиск возвращает provenance и честный fallback оригинального языка документа. */
async function search() {
  const revision = ++searchRevision;
  $('search-status').textContent = messages.loading;
  const data = await request('/api/language-references/search', { query: $('query').value, packId: $('pack').value, locale: $('doc-locale').value, mode: $('mode').value, limit: 20 });
  if (revision !== searchRevision) return;
  $('results').replaceChildren();
  const notices = [data.results.length ? `${messages.found}: ${data.results.length}` : messages.none, messages.candidate];
  if (data.warnings.some(warning => warning.includes('semantic index unavailable') || warning.includes('different model'))) notices.push(messages.semanticFallback);
  if (data.truncated) notices.push(messages.partial);
  $('search-status').textContent = notices.join(' ');
  for (const result of data.results) {
    const article = element('article'); article.className = 'reference-result';
    const button = element('button', result.title); button.type = 'button'; button.title = messages.read;
    button.addEventListener('click', action('search-status', () => openDocument(result.packId, result.documentId)));
    const title = element('h3'); title.append(button); article.append(title);
    article.append(element('small', `${result.language} · ${result.version} · ${result.edition} · ${result.locale} · ${result.match}`));
    if (result.signature) article.append(element('pre', result.signature));
    article.append(element('p', result.excerpt));
    if (result.locale !== $('doc-locale').value) article.append(element('p', `${messages.fallback} (${result.locale})`));
    $('results').append(article);
  }
}

/** Читает оригинал страницами; revision исключает подмену после выбора другого документа. */
async function openDocument(packId, documentId, offset = 0) {
  const revision = ++documentRevision;
  const data = await request(`/api/language-references/${encodeURIComponent(packId)}/documents/${encodeURIComponent(documentId)}?offset=${offset}`);
  if (revision !== documentRevision) return;
  currentDocument = { packId, documentId, nextOffset: data.nextOffset, text: data.text, language: data.pack.language };
  $('document').hidden = false; $('document-title').textContent = data.title;
  const fallback = data.pack.locale !== $('doc-locale').value ? ` · ${messages.fallback} (${data.pack.locale})` : '';
  $('document-source').textContent = `${data.sourceUrl || data.pack.sourceUrl} · ${data.path} · ${data.pack.version} · ${data.pack.locale} · ${data.pack.license}${fallback}`;
  showDocumentText();
  $('document-more').hidden = data.nextOffset === null;
  $('document').scrollIntoView({ behavior: 'instant', block: 'start' });
}

/** xref запускает точный поиск с текущими фильтрами; версия API не подменяется автоматически. */
function showDocumentText() {
  showReferencePage(currentDocument.text, {messages, enabled: ['csharp', 'dotnet', 'efcore', 'ef6'].includes(currentDocument.language),
    onReference: uid => action('search-status', async () => {
      $('query').value = uid.slice(0, 500); $('mode').value = 'exact';
      await search(); $('search-form').scrollIntoView({block: 'start'});
    })()});
}

/** Фильтр framework относится только к .NET API; руководство можно импортировать без него. */
function showFrameworkSelector() { $('framework-field').hidden = $('import-language').value !== 'dotnet'; }
$('import-language').addEventListener('change', showFrameworkSelector);

$('reload').addEventListener('click', action('catalog-status', loadCatalog));
$('search-form').addEventListener('submit', action('search-status', search));
$('import-form').addEventListener('submit', action('job-status', async () => {
  const manifest = {};
  for (const key of ['language', 'version', 'edition', 'title', 'locale', 'license']) manifest[key] = $(`import-${key}`).value;
  if (manifest.language === 'dotnet' && $('import-framework').value.trim()) manifest.framework = $('import-framework').value.trim();
  manifest.sourceUrl = $('import-source').value;
  await watch(await request('/api/language-references/import', { archivePath: $('archive-path').value, manifest }));
}));
$('ui-locale').addEventListener('change', action('catalog-status', async () => {
  await localize($('ui-locale').value); await loadCatalog();
  if ($('results').childElementCount && $('query').value.trim()) await search();
}));
$('close-document').addEventListener('click', () => { ++documentRevision; $('document').hidden = true; });
$('document-more').addEventListener('click', action('search-status', () => openDocument(currentDocument.packId, currentDocument.documentId, currentDocument.nextOffset)));
const uiLocales = await (await fetch('/locales/catalog.json')).json();
$('ui-locale').replaceChildren();
for (const locale of uiLocales) { const option = element('option', locale.label); option.value = locale.id; $('ui-locale').append(option); }
const requestedLocale = new URLSearchParams(location.search).get('locale') || 'ru';
await localize(uiLocales.some(locale => locale.id === requestedLocale) ? requestedLocale : 'en');
await action('catalog-status', loadCatalog)();
showFrameworkSelector();
const query = new URLSearchParams(location.search).get('q');
if (query) { $('query').value = query.slice(0, 500); await action('search-status', search)(); }
