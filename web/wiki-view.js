import { el } from './dom.js';
import { requestJson } from './bounded-request.js';
import { DialogFocus } from './dialog-focus.js';

/** Рисует сохранённый Markdown как текстовые блоки; HTML и удалённые ресурсы не исполняются. */
export function renderWikiText(container, text, format = 'text') {
  container.replaceChildren();
  if (format !== 'markdown') { container.append(el('pre', text, 'wiki-plain')); return; }
  let paragraph = [], code = [], fenced = false;
  const flush = () => { if (paragraph.length) container.append(el('p', paragraph.join('\n'))); paragraph = []; };
  for (const line of String(text).split('\n')) {
    if (/^\s*```/.test(line)) {
      flush();
      if (fenced) { container.append(el('pre', code.join('\n'), 'wiki-plain')); code = []; }
      fenced = !fenced; continue;
    }
    if (fenced) { code.push(line); continue; }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) { flush(); container.append(el(`h${Math.min(heading[1].length + 1, 6)}`, heading[2])); }
    else if (!line.trim()) flush();
    else if (/^\s*[-*+]\s/.test(line)) { flush(); container.append(el('p', `• ${line.replace(/^\s*[-*+]\s+/, '')}`, 'wiki-bullet')); }
    else paragraph.push(line);
  }
  flush();
  if (code.length) container.append(el('pre', code.join('\n'), 'wiki-plain'));
}

/** Локальный читатель wiki с отменой запросов и адресуемым документом поверх текущей карты. */
export class WikiViewer {
  /** Scope задаётся картой; история и переход к коду остаются в контроллере приложения. */
  constructor(root, { getScope, getNode = () => null, onNavigate = () => {}, onCode = () => {}, onBuild = () => {} }) {
    this.root = root; this.getScope = getScope; this.onNavigate = onNavigate; this.onCode = onCode; this.onBuild = onBuild;
    this.getNode = getNode;
    this.revision = 0; this.nodeId = null; this.documentId = null; this.offset = 0;
    this.title = root.querySelector('[data-wiki="title"]');
    this.status = root.querySelector('[data-wiki="status"]');
    this.content = root.querySelector('[data-wiki="content"]');
    this.closeButton = root.querySelector('[data-wiki="close"]');
    this.closeButton.addEventListener('click', () => this.close(true));
    this.dialog = new DialogFocus(root, { onClose: () => this.close(true), initialFocus: () => this.closeButton });
  }

  /** Отмена и поколение защищают также от fetch, игнорирующего AbortSignal. */
  async request(endpoint, body, scope, revision) {
    const data = await requestJson(`/api/projects/${encodeURIComponent(scope.projectId)}/${endpoint}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: this.controller.signal,
      body: JSON.stringify({ ...body, snapshotId: scope.snapshotId }),
    });
    if (!this.current(scope, revision)) return null;
    if (data.snapshotId && data.snapshotId !== scope.snapshotId) throw new Error('Документ относится к другому снимку.');
    return data;
  }

  /** Проверяет выбранный репозиторий, snapshot и поколение читателя. */
  current(scope, revision) {
    const current = this.getScope();
    return revision === this.revision && current.projectId === scope.projectId && current.snapshotId === scope.snapshotId && !this.root.hidden;
  }

  /** Начинает один переход без копирования текста в browser history. */
  begin(title, notify) {
    this.controller?.abort(); this.controller = new AbortController();
    const revision = ++this.revision;
    this.root.hidden = false; this.title.textContent = title;
    this.status.textContent = 'Читаем сохранённую документацию…'; this.content.replaceChildren();
    this.dialog.open();
    // Новый документ удаляет прежний активный link; trigger диалога сохраняется самим shell.
    this.closeButton.focus({ preventScroll: true });
    if (notify) this.onNavigate();
    return revision;
  }

  /** Документы выбранного узла, документы его соседей и рекомендации показаны раздельно. */
  async openNode(nodeId, { notify = true, signal } = {}) {
    this.nodeId = nodeId; this.documentId = null; this.offset = 0;
    const scope = { ...this.getScope() }, revision = this.begin('Wiki узла', notify);
    const abort = () => { if (revision === this.revision) this.close(false); };
    signal?.addEventListener('abort', abort, { once: true });
    try {
      const data = await this.request('node-wiki', { nodeId, limit: 8, includeRecommendations: false }, scope, revision);
      if (!data) return;
      this.title.textContent = `Wiki · ${data.node?.label || nodeId}`;
      this.status.textContent = `Снимок ${scope.snapshotId.slice(0, 8)} · документы сохранены ${data.index?.capturedAt || 'в индексе'}`;
      this.documentSection('Документы узла', 'Явная привязка к символу или файлу. Область привязки подписана у документа.', data.directDocuments);
      this.documentSection('Документы связанных узлов', 'Один переход по сохранённой связи графа. Возможные реализации помечены уверенностью анализа.', data.neighborDocuments);
      this.recommendationSection(data, scope, revision);
      if (data.graphNeighbors?.length) this.neighborSection(data.graphNeighbors);
      for (const warning of data.warnings || []) this.content.append(el('p', warning, 'small muted'));
    } catch (error) { this.showError(error, scope, revision); }
    finally { signal?.removeEventListener('abort', abort); }
  }

  /** Смысловой поиск запускается отдельно: сохранённые документы доступны до загрузки модели. */
  recommendationSection(data, scope, revision) {
    const section = el('section', undefined, 'wiki-section');
    section.append(el('h3', 'Похожие по смыслу'), el('p', 'MiniLM ищет похожие материалы локально. Результат помогает выбрать, что читать дальше.', 'small muted'));
    const status = el('p', 'Документы и связи уже доступны. Смысловой поиск запускается по кнопке.', 'small muted');
    status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    const button = el('button', 'Найти похожие материалы'); button.type = 'button';
    const results = el('div');
    button.addEventListener('click', () => this.loadRecommendations(data.node.id, scope, revision, results, status, button));
    section.append(status, button, results); this.content.append(section);
  }

  /** Поздний ответ не заменяет открытый документ; ошибка сохраняет текущую wiki и возможность повтора. */
  async loadRecommendations(nodeId, scope, revision, results, status, button) {
    if (button.busy || !this.current(scope, revision)) return;
    // Сохраняем кнопку в Tab-порядке и в фокусе, блокируя повторный запуск через guard.
    button.busy = true; button.setAttribute('aria-disabled', 'true'); button.setAttribute('aria-busy', 'true');
    status.textContent = 'Ищем похожие материалы…';
    try {
      const data = await this.request('node-wiki', { nodeId, limit: 8, includeRecommendations: true }, scope, revision);
      if (!data) return;
      if (['unavailable', 'model-unavailable'].includes(data.recommendationStatus)) {
        status.textContent = (data.warnings || []).join(' ') || 'Смысловой поиск сейчас недоступен.';
        button.textContent = 'Повторить смысловой поиск';
        return;
      }
      const replacingFocusedResult = results.contains(document.activeElement);
      results.replaceChildren();
      if (data.recommendationStatus === 'no-context') status.textContent = 'У этого узла недостаточно текста для смыслового поиска.';
      else {
        status.textContent = `Найдено похожих материалов: ${(data.recommendations || []).length}.`;
        this.documentSection('Результаты поиска', 'Сходство текста помогает выбрать материалы для чтения.', data.recommendations, results);
      }
      for (const warning of data.warnings || []) results.append(el('p', warning, 'small muted'));
      button.textContent = 'Обновить похожие материалы';
      if (replacingFocusedResult) button.focus({ preventScroll: true });
    } catch (error) {
      if (this.current(scope, revision) && error.name !== 'AbortError') {
        status.textContent = error.message; button.textContent = 'Повторить смысловой поиск';
      }
    } finally {
      button.busy = false; button.setAttribute('aria-disabled', 'false'); button.setAttribute('aria-busy', 'false');
    }
  }

  /** Карточки документа сохраняют его ID; заголовок/путь никогда не становятся исполняемым HTML. */
  documentSection(title, hint, documents = [], parent = this.content) {
    const section = el('section', undefined, 'wiki-section'); section.append(el('h3', title), el('p', hint, 'small muted'));
    if (!documents.length) section.append(el('p', 'Документы в этом разделе не найдены.', 'small muted'));
    for (const item of documents) {
      const doc = item.document || item;
      const card = el('article', undefined, 'wiki-document-card');
      const button = el('button', doc.title || doc.file || doc.id || doc.documentId);
      button.addEventListener('click', () => this.openDocument(doc.id || doc.documentId));
      const matches = doc.matches || [];
      const scopes = [...new Set(matches.map(match => match.scope === 'symbol' ? 'символ' : 'файл'))];
      const association = scopes.length ? `привязка: ${scopes.join(', ')}` : doc.reason === 'semantic-similarity' ? 'смысловая рекомендация' : doc.association === 'declaration' ? 'комментарий к объявлению' : 'документ файла';
      card.append(button, el('p', `${doc.file || ''}${doc.line ? `:${doc.line}` : ''} · ${association}`, 'small muted'));
      for (const via of item.via || []) card.append(el('p', `${via.direction === 'incoming' ? '←' : '→'} ${via.edge?.relation || ''} · ${via.node?.label || ''} · ${via.edge?.confidence || 'факт графа'}`, 'small wiki-evidence'));
      if (item.excerpt || doc.excerpt || doc.preview) card.append(el('p', item.excerpt || doc.excerpt || doc.preview, 'wiki-excerpt'));
      section.append(card);
    }
    parent.append(section);
  }

  /** Соседи перечислены как факты графа; переход открывает именно raw-ID. */
  neighborSection(neighbors) {
    const section = el('section', undefined, 'wiki-section'); section.append(el('h3', 'Связанные узлы карты'));
    for (const item of neighbors) {
      const node = item.node || item, edge = item.edge || {};
      const button = el('button', `${item.direction === 'incoming' ? '←' : '→'} ${edge.relation || item.relation || ''} · ${node.label || node.id}`);
      button.title = `${node.file || ''} · ${edge.confidence || item.confidence || 'факт графа'}`;
      button.addEventListener('click', () => { this.close(false); this.onCode(node.id); }); section.append(button);
    }
    this.content.append(section);
  }

  /** Читает страницу captured-текста; изменение файла на диске не меняет открытый документ. */
  async openDocument(documentId, { offset = 0, nodeId = this.nodeId, notify = true, signal } = {}) {
    this.nodeId = nodeId; this.documentId = documentId; this.offset = offset;
    const scope = { ...this.getScope() }, revision = this.begin('Документ wiki', notify);
    const abort = () => { if (revision === this.revision) this.close(false); };
    signal?.addEventListener('abort', abort, { once: true });
    try {
      const data = await this.request('wiki-document', { documentId, offset, maxChars: 12000 }, scope, revision);
      if (!data) return;
      const doc = data.document;
      this.title.textContent = doc.title || doc.file || 'Документ wiki';
      this.status.textContent = `${doc.file || ''}${doc.line ? `:${doc.line}` : ''} · сохранено ${doc.capturedAt || data.index?.capturedAt || ''} · SHA ${String(doc.sourceHash || '').slice(0, 12)} · ${data.textOffsetStart}–${data.textOffsetEnd} / ${data.totalChars}`;
      if (this.nodeId) {
        const context = el('button', '← Документы и связи узла'); context.addEventListener('click', () => this.openNode(this.nodeId)); this.content.append(context);
      }
      const article = el('article', undefined, 'wiki-reading'); renderWikiText(article, data.text, doc.format); this.content.append(article);
      this.documentCodeLinks(doc);
      const paging = el('div', undefined, 'wiki-paging');
      if (offset > 0) { const previous = el('button', '← Предыдущая часть'); previous.addEventListener('click', () => this.openDocument(documentId, { offset: Math.max(0, offset - 12000) })); paging.append(previous); }
      if (data.hasMore) { const next = el('button', 'Следующая часть →'); next.addEventListener('click', () => this.openDocument(documentId, { offset: data.nextOffset })); paging.append(next); }
      this.content.append(paging);
      if (doc.truncated) this.content.append(el('p', 'Документ сокращён при сохранении: показана только захваченная часть.', 'small muted'));
    } catch (error) { this.showError(error, scope, revision); }
    finally { signal?.removeEventListener('abort', abort); }
  }

  /** Показывает только сохранённые raw-ID; текстовые упоминания не превращаются в связи кода. */
  documentCodeLinks(doc) {
    const ids = [...new Set(doc.nodeIds || [])].slice(0, 12);
    const section = el('section', undefined, 'wiki-section'); section.append(el('h3', 'Привязанные узлы карты'));
    for (const id of ids) {
      const node = this.getNode(id);
      const button = el('button', `Открыть узел · ${node?.label || id.slice(-100)}`); button.title = `${node?.file || ''} · ${id}`;
      button.addEventListener('click', () => { this.close(false); this.onCode(id); }); section.append(button);
    }
    if (ids.length) this.content.append(section);
    const sources = doc.sourceLinks || [];
    if (!sources.length) return;
    const links = el('section', undefined, 'wiki-section'); links.append(el('h3', 'Ссылки из документа'), el('p', 'Явные ссылки сохранённого текста. Ссылка на файл не доказывает принадлежность документа отдельному символу.', 'small muted'));
    for (const source of sources.slice(0, 40)) {
      if (source.documentId) {
        const button = el('button', `Wiki · ${source.file}`);
        button.addEventListener('click', () => this.openDocument(source.documentId)); links.append(button);
        continue;
      }
      const nodeIds = (source.nodeIds || []).slice(0, 12);
      links.append(el('p', `${source.file}${source.line ? `:${source.line}` : ''} · ссылка на файл`, 'small muted'));
      for (const id of nodeIds) {
        const node = this.getNode(id), button = el('button', `Узел файла · ${node?.label || id.slice(-100)}`);
        button.addEventListener('click', () => { this.close(false); this.onCode(id); }); links.append(button);
      }
      if (!nodeIds.length) links.append(el('p', 'Файл не связан с доступным узлом или captured-документом.', 'small muted'));
    }
    this.content.append(links);
  }

  /** Ошибки индекса дают понятный следующий шаг; stale ответы не заменяют текущий документ. */
  showError(error, scope, revision) {
    if (!this.current(scope, revision) || error.name === 'AbortError') return;
    this.status.textContent = error.message;
    if (error.status !== 409) {
      const button = el('button', 'Повторить чтение wiki');
      button.type = 'button';
      button.addEventListener('click', () => this.documentId
        ? this.openDocument(this.documentId, { offset: this.offset }) : this.openNode(this.nodeId));
      this.content.append(button);
    }
    if (error.status === 409 || (error.status === 503 && error.message.includes('Пересоберите индекс документации'))) {
      const button = el('button', 'Обновить индекс документации');
      button.addEventListener('click', () => { this.close(true); this.onBuild(); }); this.content.append(button);
    }
  }

  /** Закрытие отменяет запрос и возвращает фокус; смена проекта не создаёт запись истории. */
  close(notify = false) {
    this.controller?.abort(); this.revision++; this.root.hidden = true;
    this.nodeId = null; this.documentId = null; this.offset = 0;
    this.dialog.close();
    if (notify) this.onNavigate();
  }
}
