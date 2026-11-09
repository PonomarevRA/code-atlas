import { renderReferenceContent } from './reference-content.js';
import { requestJson } from './bounded-request.js';

const MAX_QUERY = 500;
const MAX_RESULTS = 5;
const MAX_DOCUMENT = 12000;

/** Создаёт текстовый DOM: пути, XML и подписи проекта не становятся HTML. */
function element(document, tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

/** Контекстный поиск читает только явно сохранённые XML библиотек; точная привязка вызова неизвестна. */
export class SourceLibraryDocs {
  /** Host остаётся частью исходного диалога; fetcher подменяется только для проверки транспорта. */
  constructor(root, { fetcher = (...args) => fetch(...args) } = {}) {
    this.root = root;
    this.document = root.ownerDocument || globalThis.document;
    this.fetcher = fetcher;
    this.context = null;
    this.revision = 0;
    this.build();
    this.reset();
  }

  /** Строит небольшой поиск без собственного popup, клавиатурных перехватов и автоматической загрузки. */
  build() {
    const make = (tag, text, className) => element(this.document, tag, text, className);
    const title = make('h3', 'Документация библиотек');
    const explanation = make('p', 'Возможная документация, привязка к вызову не подтверждена.', 'small muted');
    const scope = make('p', 'Поиск по сохранённым авторским XML зависимостей .NET. Другие языки и отсутствующие пакеты здесь не покрываются.', 'small muted');
    this.form = make('form', undefined, 'source-library-form');
    const label = make('label', 'Метод или фрагмент имени ');
    this.query = make('input');
    this.query.type = 'search';
    this.query.maxLength = MAX_QUERY;
    this.query.required = true;
    this.query.placeholder = 'Например, DbContext.SaveChangesAsync';
    label.append(this.query);
    const localeLabel = make('label', 'Язык документации ');
    this.locale = make('select');
    for (const [value, text] of [['ru', 'Русский, с исходным языком при отсутствии'], ['en', 'English']]) {
      const option = make('option', text); option.value = value; this.locale.append(option);
    }
    this.locale.value = 'ru';
    localeLabel.append(this.locale);
    this.submit = make('button', 'Найти документацию');
    this.submit.type = 'submit';
    this.form.append(label, localeLabel, this.submit);
    this.status = make('p', '', 'small muted');
    this.status.setAttribute('role', 'status');
    this.status.setAttribute('aria-live', 'polite');
    this.results = make('div', undefined, 'source-library-results');
    this.documentPanel = make('section', undefined, 'source-library-document');
    this.documentPanel.setAttribute('aria-label', 'Открытый документ библиотеки');
    this.catalogLink = make('a', 'Открыть полный каталог библиотек');
    this.catalogLink.href = '/library-docs.html';
    this.root.replaceChildren(title, explanation, scope, this.form, this.status, this.results, this.documentPanel, this.catalogLink);
    this.form.addEventListener('submit', event => { event.preventDefault(); this.search(); });
  }

  /** Смена repository, снимка, узла или файла отменяет прошлый поиск, сохраняя только новый scope. */
  setContext(context) {
    const next = context?.projectId ? { ...context } : null;
    const key = JSON.stringify([next?.projectId, next?.snapshotId, next?.nodeId, next?.file]);
    if (key === this.contextKey) return;
    this.contextKey = key;
    this.context = next;
    this.reset();
    this.catalogLink.href = next ? `/library-docs.html?repo=${encodeURIComponent(next.projectId)}` : '/library-docs.html';
  }

  /** Подстановка выделенного текста никогда сама не отправляет поиск. */
  setQuery(text) { this.query.value = String(text || '').slice(0, MAX_QUERY); }

  /** Отмена инвалидирует даже транспорт, который проигнорировал AbortSignal. */
  cancel() {
    this.controller?.abort();
    this.controller = null;
    this.revision++;
  }

  /** Очищает локальные результаты; текущий repository сохраняется для следующего явного поиска. */
  reset() {
    this.cancel();
    this.query.value = '';
    this.results.replaceChildren();
    this.documentPanel.replaceChildren();
    this.documentPanel.hidden = true;
    this.status.textContent = this.context ? 'Введите имя метода и запустите поиск. Импорт автоматически не запускается.' : 'Выберите исходник проекта.';
    this.submit.disabled = !this.context;
  }

  /** Закрытие исходника удаляет контекст, чтобы скрытый блок не мог отправить старый запрос. */
  close() {
    this.context = null;
    this.contextKey = null;
    this.reset();
  }

  /** Читает локальный JSON с общим пределом ожидания 30 с; HTTP ошибки показывает вызывающий метод. */
  async request(path, body, signal) {
    const options = { signal };
    if (body !== undefined) {
      options.method = 'POST';
      options.headers = { 'Content-Type': 'application/json' };
      options.body = JSON.stringify(body);
    }
    return await requestJson(path, options, { fetcher: this.fetcher });
  }

  /** Явный submit ищет до пяти кандидатов; каждый новый запрос отменяет старое чтение документа. */
  async search() {
    if (!this.context) return;
    const query = this.query.value.trim();
    if (!query || query.length > MAX_QUERY) {
      this.status.textContent = 'Введите от 1 до 500 символов.';
      return;
    }
    this.cancel();
    const revision = this.revision, context = this.context;
    this.controller = new AbortController();
    this.results.replaceChildren();
    this.documentPanel.replaceChildren();
    this.documentPanel.hidden = true;
    this.submit.disabled = true;
    this.status.textContent = 'Поиск в сохранённой документации…';
    try {
      const data = await this.request(`/api/projects/${encodeURIComponent(context.projectId)}/library-documentation/search`, {
        query, locale: this.locale.value, mode: 'fulltext', limit: MAX_RESULTS,
      }, this.controller.signal);
      if (revision !== this.revision) return;
      if (data.projectId && data.projectId !== context.projectId) throw new Error('Документация относится к другому проекту.');
      const hits = Array.isArray(data.results) ? data.results.slice(0, MAX_RESULTS) : [];
      this.status.textContent = hits.length
        ? `Возможные документы: ${hits.length}${data.truncated ? ' · показана часть результатов' : ''}. Привязка к вызову не подтверждена.`
        : data.capturedAt ? 'Совпадений нет. Уточните имя или откройте полный каталог.' : 'Сохранённой документации нет. Доступны авторские XML зависимостей .NET; импорт можно явно запустить в каталоге.';
      for (const hit of hits) this.renderHit(hit, data.capturedAt);
    } catch (error) {
      if (revision === this.revision && error.name !== 'AbortError') this.status.textContent = error.message;
    } finally {
      if (revision === this.revision) this.submit.disabled = !this.context;
    }
  }

  /** Метаданные кандидата не смешивают версию пакета, assembly, язык и дату capture. */
  metadata(library = {}, version, locale, capturedAt) {
    const parts = [
      `Пакет: ${library.package || 'не указан'}`, `версия: ${version || library.version || 'не указана'}`,
      `assembly: ${library.assembly || 'не указана'}`, `язык: ${locale || 'не указан'}`,
      `источник: ${library.sourcePath || 'не указан'}`, `сохранено: ${capturedAt || 'дата не указана'}`,
    ];
    if (library.sourceHash) parts.push(`SHA-256: ${library.sourceHash}`);
    return parts.join(' · ');
  }

  /** Кандидат открывается только кнопкой; разные перегрузки остаются отдельными строками. */
  renderHit(hit, capturedAt) {
    const row = element(this.document, 'article', undefined, 'source-library-hit');
    const button = element(this.document, 'button', hit.title || hit.signature || 'Открыть документ');
    button.type = 'button';
    button.addEventListener('click', () => this.openDocument(hit));
    row.append(button, element(this.document, 'pre', hit.signature || ''), element(this.document, 'p', this.metadata(hit.library, hit.version, hit.locale, capturedAt), 'small muted'));
    if (hit.localeFallback) row.append(element(this.document, 'p', `Запрошенного перевода нет. Показан исходный язык: ${hit.locale || 'не указан'}.`, 'small muted'));
    row.append(element(this.document, 'p', hit.excerpt || ''));
    this.results.append(row);
  }

  /** Документ читается одной ограниченной страницей; продолжение доступно через полный каталог. */
  async openDocument(hit) {
    if (!this.context || !hit.packId || !hit.documentId) return;
    this.cancel();
    const revision = this.revision, context = this.context;
    this.controller = new AbortController();
    this.submit.disabled = false;
    this.documentPanel.replaceChildren();
    this.documentPanel.hidden = true;
    this.status.textContent = 'Чтение сохранённого документа…';
    try {
      const base = `/api/projects/${encodeURIComponent(context.projectId)}/library-documentation`;
      const data = await this.request(`${base}/${encodeURIComponent(hit.packId)}/documents/${encodeURIComponent(hit.documentId)}?offset=0&limit=${MAX_DOCUMENT}`, undefined, this.controller.signal);
      if (revision !== this.revision) return;
      if (data.projectId && data.projectId !== context.projectId) throw new Error('Документ относится к другому проекту.');
      const text = String(data.text || '').slice(0, MAX_DOCUMENT);
      this.documentPanel.append(element(this.document, 'h4', data.title || hit.title || 'Документ библиотеки'));
      this.documentPanel.append(element(this.document, 'p', this.metadata(data.library, data.pack?.version || hit.version, data.pack?.locale || hit.locale, data.capturedAt), 'small muted'));
      const content = element(this.document, 'div', undefined, 'source-library-content');
      renderReferenceContent(content, text, { messages: { remarks: 'Примечания', examples: 'Примеры' } });
      this.documentPanel.append(content);
      const raw = element(this.document, 'details');
      raw.append(element(this.document, 'summary', 'Исходный текст страницы'), element(this.document, 'pre', text));
      this.documentPanel.append(raw);
      if (data.nextOffset != null || String(data.text || '').length > MAX_DOCUMENT) this.documentPanel.append(element(this.document, 'p', 'Показана часть документа. Продолжение доступно в полном каталоге библиотек.', 'small muted'));
      this.documentPanel.hidden = false;
      this.status.textContent = 'Возможная документация, привязка к вызову не подтверждена.';
    } catch (error) {
      if (revision === this.revision && error.name !== 'AbortError') this.status.textContent = error.message;
    }
  }
}

