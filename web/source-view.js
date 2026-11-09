import { requestJson } from './bounded-request.js';
import { DialogFocus } from './dialog-focus.js';
import { ReaderHistory } from './source-reader-history.js';
import { SourceFileBrowser } from './source-file-browser.js';
import { SourceLibraryDocs } from './source-library-docs.js';
import { appendSourceTokens, matchingLines, validSourceLine } from './source-reading.js';

/** Возвращает ограниченное окно строк; номер из индекса не сдвигается к другой строке. */
export function sourceWindow(text, indexedLine, { mode = 'context', page = 0, pageSize = 200, context = 40 } = {}) {
  const all = String(text).split(/\r\n|\n|\r/);
  const size = Math.max(1, Math.min(200, Math.trunc(pageSize) || 200));
  const validLine = Number.isInteger(indexedLine) && indexedLine > 0 && indexedLine <= all.length;
  const pageCount = Math.max(1, Math.ceil(all.length / size));
  const currentPage = Math.min(pageCount - 1, Math.max(0, Math.trunc(page) || 0));
  const radius = Math.max(0, Math.min(99, Math.trunc(context) || 0));
  const start = mode === 'full' ? currentPage * size : Math.max(0, (validLine ? indexedLine - 1 : 0) - radius);
  const end = Math.min(all.length, mode === 'full' ? start + size : (validLine ? indexedLine : 1) + radius);
  const lines = [];
  for (let index = start; index < end; index++) lines.push({ number: index + 1, text: all[index], highlight: validLine && index + 1 === indexedLine });
  return { lines, start: start + 1, end, totalLines: all.length, page: currentPage, pageCount };
}
/** Read-only file workspace. Map selection/camera and browser history remain outside this dialog. */
/** Read-only source workspace with local navigation; map selection and browser history stay outside it. */
export class SourceViewer {
  /** getContext supplies the map anchor; fetcher reads snapshot-scoped JSON and never writes source. */
  constructor(root, getContext, fetcher = (...args) => fetch(...args)) {
    this.root = root; this.getContext = getContext; this.fetcher = fetcher;
    this.opened = false; this.revision = 0; this.key = null; this.data = null; this.mode = 'context'; this.page = 0;
    this.query = ''; this.matchIndex = -1; this.history = new ReaderHistory();
    this.find = name => root.querySelector(`[data-source="${name}"]`);
    this.dialog = new DialogFocus(root, {onClose: () => this.close(), initialFocus: () => this.find('close')});
    const bind = (name, event, action) => this.find(name)?.addEventListener(event, action);
    bind('close', 'click', () => this.close());
    bind('retry', 'click', () => { if (this.locationState) this.loadLocation(this.locationState,this.restoreLocation); });
    bind('context', 'click', () => { this.mode = 'context'; this.render(); });
    bind('full', 'click', () => { this.mode = 'full'; this.page = Math.floor(Math.max(0,(this.data?.line || 1)-1)/200); this.render(); });
    bind('previous', 'click', () => { this.page--; this.render(); });
    bind('next', 'click', () => { this.page++; this.render(); });
    bind('back', 'click', () => this.travel(-1)); bind('forward', 'click', () => this.travel(1));
    bind('goto-button', 'click', () => this.goToLine());
    bind('goto', 'keydown', event => { if (event.key === 'Enter') { event.preventDefault(); this.goToLine(); } });
    bind('find', 'input', () => { this.query = this.find('find').value; this.matchIndex = -1; this.previewFind(); });
    bind('find', 'keydown', event => { if(event.key === 'Enter') { event.preventDefault(); this.findNext(); } });
    bind('find-next', 'click', () => this.findNext());
    bind('maximize', 'click', () => this.maximize());
    bind('files-toggle', 'click', () => this.togglePane('file-pane','files-toggle'));
    bind('library-toggle', 'click', () => this.togglePane('library','library-toggle'));
    bind('docs-selection', 'click', () => this.useSelection());
    if (this.find('file-pane')) this.files = new SourceFileBrowser(root, context => this.navigate(context), fetcher);
    if (this.find('library')) this.library = new SourceLibraryDocs(this.find('library'), {fetcher});
    // Only a real window resize recenters selection. Async status/layout changes must preserve Back's scroll.
    this.onWindowResize = () => { if (this.opened) this.centerSelectedLine(true); };
    root.ownerDocument?.defaultView?.addEventListener?.('resize',this.onWindowResize);
  }
  /** Open from the map at the requested raw node. Repeated opens retain the original focus return target. */
  open() {
    // Safari pointer activation can leave focus on body. Keep a visible map return target in that case.
    const owner = this.root.ownerDocument, fallback = owner?.getElementById?.('source-open');
    if (!this.dialog.opened && owner?.activeElement === owner?.body && fallback && !fallback.disabled && !fallback.closest('[hidden],[inert]')) fallback.focus({preventScroll:true});
    this.opened = true; this.root.hidden = false; this.dialog.open(); this.key = null; this.anchorKey = null; return this.sync();
  }
  /** Cancel all readers, retain the current local location, and return focus to the map trigger. */
  close() {
    this.saveLocation(); this.opened = false; this.root.hidden = true; this.dialog.close();
    this.controller?.abort(); this.files?.close(); this.library?.close(); this.revision++; this.key = null; this.anchorKey = null;
  }
  /** Source locations use raw IDs and exact indexed lines, independently of display labels. */
  contextKey(context) { return JSON.stringify([context.projectId,context.snapshotId,context.nodeId,context.line || 0]); }
  /** Only an actual map selection change replaces local browsing; background redraws do not. */
  async sync() {
    if (!this.opened) return;
    const context = this.getContext(), key = this.contextKey(context);
    if (key === this.anchorKey && this.key) return;
    this.saveLocation(); this.anchorKey = key;
    this.history.reset(JSON.stringify([context.projectId,context.snapshotId]));
    const state = this.initialLocation(context); this.history.push(state);
    return this.loadLocation(state);
  }
  /** Newly selected locations start in bounded context mode without previous search or scroll. */
  initialLocation(context) { return {context:{...context},mode:'context',page:0,query:'',matchIndex:-1,scrollTop:0,scrollLeft:0,workspaceScrollTop:0}; }
  /** Narrow layouts add an outer pane scroller; the map/page must never be scrolled by the reader. */
  scrollContainer() {
    const parent = this.find('code').parentElement;
    return parent?.classList?.contains('source-workspace') ? parent : null;
  }
  /** Passive state capture updates the current entry without dropping its Forward branch. */
  saveLocation() {
    if (!this.activeContext || !this.data) return;
    const code = this.find('code');
    this.history.update({context:{...this.activeContext,line:this.data.line},mode:this.mode,page:this.page,
      query:this.query,matchIndex:this.matchIndex,scrollTop:code.scrollTop || 0,scrollLeft:code.scrollLeft || 0,
      workspaceScrollTop:this.scrollContainer()?.scrollTop || 0});
  }
  /** Explicit file selection commits a new local branch before requesting its source. */
  navigate(context) { this.saveLocation(); const state = this.initialLocation(context); this.history.push(state); return this.loadLocation(state); }
  /** Back/Forward restores captured reader settings; the map anchor remains unchanged. */
  travel(delta) { this.saveLocation(); const state = this.history.move(delta); if (state) return this.loadLocation(state,true); }
  /** Every source request is scoped and revision-guarded, including local history navigation. */
  async loadLocation(state, restore = false) {
    this.locationState = state;
    this.restoreLocation = restore;
    if (this.find('retry')) this.find('retry').hidden = true;
    const context = state.context; this.activeContext = {...context}; this.key = this.contextKey(context);
    this.controller?.abort(); this.controller = new AbortController(); const revision = ++this.revision;
    this.data = null; this.mode = state.mode; this.page = state.page; this.query = state.query; this.matchIndex = state.matchIndex;
    if (this.find('find')) this.find('find').value = this.query;
    for (const name of ['reader-status','language']) if (this.find(name)) this.find(name).textContent = '';
    this.files?.setContext(context); this.library?.setContext(context); this.render();
    this.find('title').textContent = context.label || 'Исходник';
    this.find('status').textContent = context.nodeId ? 'Чтение локального файла…' : 'Выберите узел для просмотра исходника.';
    if (!context.nodeId || !context.projectId) return;
    if (!context.snapshotId) { this.find('status').textContent = 'Снимок индекса недоступен. Перезагрузите карту.'; return; }
    if (this.files && !this.files.loaded && !this.find('file-pane').hidden) this.files.load();
    try {
      const data = await requestJson(`/api/projects/${encodeURIComponent(context.projectId)}/source`, {method:'POST',headers:{'Content-Type':'application/json'},signal:this.controller.signal,
        body:JSON.stringify({nodeId:context.nodeId,snapshotId:context.snapshotId})}, {fetcher:this.fetcher});
      if (revision !== this.revision || !this.opened) return;
      if (data.snapshotId !== context.snapshotId) throw new Error('Индекс изменился. Перезагрузите карту.');
      this.data = data;
      if (Number.isInteger(context.line) && context.line > 0) this.data.line = context.line;
      this.find('title').textContent = `${data.file}${this.data.line ? ':' + this.data.line : ''}`;
      this.find('status').textContent = `Текущая рабочая копия · только чтение. ${data.warning || 'Содержимое может отличаться от сохранённого индекса.'}`;
      if (this.find('language')) this.find('language').textContent = `${data.syntax?.language || 'text'} · ${data.syntax?.status === 'ready' ? 'лексическая подсветка' : 'обычный текст'}`;
      this.render();
      if (restore) {
        this.find('code').scrollTop = state.scrollTop; this.find('code').scrollLeft = state.scrollLeft;
        const workspace = this.scrollContainer();
        if (workspace) workspace.scrollTop = state.workspaceScrollTop || 0;
      }
    } catch (error) {
      if (revision === this.revision && this.opened && error.name !== 'AbortError') {
        this.find('status').textContent = error.message;
        if (this.find('retry')) this.find('retry').hidden = false;
      }
    }
  }
  /** Invalid positions stay explicit; jump locally without reopening the same source file. */
  goToLine(value = this.find('goto')?.value, nextMatchIndex = this.matchIndex) {
    if (!this.data) return;
    const total = this.data.totalLines || String(this.data.text).split(/\r\n|\n|\r/).length;
    const line = validSourceLine(value,total);
    if (line == null) { if (this.find('reader-status')) this.find('reader-status').textContent = `Введите строку от 1 до ${total}.`; return; }
    this.saveLocation(); this.matchIndex = nextMatchIndex;
    this.data.line = line; this.activeContext = {...this.activeContext,line};
    this.mode = 'full'; this.page = Math.floor((line-1)/200); this.render(); this.saveAsNewLocation();
    // A newly mounted long line can add a scrollbar after layout. Only this explicit jump gets a second reveal.
    const revision = this.revision, scrollTop = this.find('code').scrollTop;
    this.root.ownerDocument?.defaultView?.requestAnimationFrame?.(() => {
      if (this.opened && revision === this.revision && this.data?.line === line && this.find('code').scrollTop === scrollTop) this.centerSelectedLine(true);
    });
    if (this.find('reader-status')) this.find('reader-status').textContent = `Открыта строка ${line}.`;
  }
  /** A deliberate line jump commits its page/search/scroll to the bounded local history. */
  saveAsNewLocation() {
    const code = this.find('code'); this.history.push({context:{...this.activeContext},mode:this.mode,page:this.page,query:this.query,
      matchIndex:this.matchIndex,scrollTop:code.scrollTop || 0,scrollLeft:code.scrollLeft || 0,
      workspaceScrollTop:this.scrollContainer()?.scrollTop || 0}); this.historyControls();
  }
  /** Typing only previews matches; explicit Next/Enter commits a local history position. */
  previewFind() {
    const code = this.find('code'), scrollTop = code.scrollTop, scrollLeft = code.scrollLeft;
    const workspace = this.scrollContainer(), workspaceScrollTop = workspace?.scrollTop;
    this.render();
    code.scrollTop = scrollTop; code.scrollLeft = scrollLeft;
    if (workspace) workspace.scrollTop = workspaceScrollTop;
    const matches = this.data ? matchingLines(this.data.text,this.query) : [];
    if (this.find('reader-status')) this.find('reader-status').textContent = this.query ? `${matches.length}${matches.length === 1000 ? '+' : ''} строк с совпадениями · Enter для перехода · с учётом регистра` : '';
  }
  /** Find literal text in at most 1000 matching lines; no source-derived regex is executed. */
  findNext() {
    if (!this.data) return;
    const matches = matchingLines(this.data.text,this.query);
    const nextIndex = matches.length ? (this.matchIndex+1)%matches.length : -1;
    if (nextIndex >= 0) this.goToLine(matches[nextIndex],nextIndex);
    else { this.matchIndex = -1; this.render(); }
    if (this.find('reader-status')) this.find('reader-status').textContent = !this.query ? '' : matches.length ? `Строка совпадения ${this.matchIndex+1} / ${matches.length}${matches.length === 1000 ? '+' : ''} · с учётом регистра` : 'Совпадений нет · с учётом регистра.';
  }
  /** Reflect local history availability without changing source or focus. */
  historyControls() { if(this.find('back')) this.find('back').disabled = !this.history.canBack; if(this.find('forward')) this.find('forward').disabled = !this.history.canForward; }
  /** Toggle the reading area within this modal, retaining the loaded file and map state. */
  maximize() {
    this.expanded = !this.expanded; this.root.setAttribute?.('data-maximized',String(this.expanded));
    const button = this.find('maximize'); button.setAttribute('aria-pressed',String(this.expanded)); button.textContent = this.expanded ? 'Компактно' : 'Развернуть';
    this.centerSelectedLine(true);
  }
  /** Closed auxiliary readers cancel pending work; reopened docs regain the current source scope. */
  togglePane(name, buttonName) {
    const pane = this.find(name); pane.hidden = !pane.hidden; this.find(buttonName).setAttribute('aria-expanded',String(!pane.hidden));
    if (name === 'file-pane') { if (pane.hidden) this.files?.close(); else this.files?.load(); }
    if (name === 'library') { if (pane.hidden) this.library?.close(); else this.library?.setContext(this.activeContext); }
  }
  /** Selection is user initiated and limited to this source viewport, never other browser content. */
  useSelection() {
    const selection = this.root.ownerDocument?.defaultView?.getSelection?.() || globalThis.getSelection?.();
    const code = this.find('code');
    if (!selection?.anchorNode || !code.contains?.(selection.anchorNode) || !code.contains?.(selection.focusNode)) {
      if (this.find('reader-status')) this.find('reader-status').textContent = 'Выделите имя метода в коде или введите запрос в документации.'; return;
    }
    if (this.find('library').hidden) this.togglePane('library','library-toggle');
    this.library?.setQuery(selection.toString().slice(0,500));
  }
  /** A resized code viewport keeps the selected row visible; offsets are relative to the positioned code area. */
  centerSelectedLine(onlyIfOutside = false) {
    const code = this.find('code'), selected = code.querySelector('.source-line-selected');
    if (!selected) return;
    const top = selected.offsetTop, height = selected.offsetHeight || 0;
    const outside = top < code.scrollTop || top + height > code.scrollTop + code.clientHeight;
    if (!onlyIfOutside || outside) code.scrollTop = Math.max(0,top-code.clientHeight/2+height/2);
    // On narrow screens the stacked file/docs panes clip the code viewport in a second scroll container.
    // Move only that container, retaining the map camera and the page behind the modal.
    const workspace = this.scrollContainer();
    if (workspace) {
      const row = selected.getBoundingClientRect(), visible = workspace.getBoundingClientRect();
      if (row.top < visible.top) workspace.scrollTop -= visible.top - row.top;
      else if (row.bottom > visible.bottom) workspace.scrollTop += row.bottom - visible.bottom;
    }
  }
  /** At most 200 rows mount; grammar tokens become plain spans and never source HTML. */
  render() {
    const code = this.find('code'); code.replaceChildren();
    for (const name of ['context','full','previous','next','goto-button','find-next','docs-selection']) if(this.find(name)) this.find(name).disabled = !this.data;
    this.historyControls(); this.find('range').textContent = '';
    if (!this.data) return;
    this.find('title').textContent = `${this.data.file}${this.data.line ? ':' + this.data.line : ''}`;
    if (this.find('goto')) this.find('goto').value = this.data.line || '';
    const result = sourceWindow(this.data.text,this.data.line,{mode:this.mode,page:this.page}); this.page = result.page;
    this.find('context').setAttribute('aria-pressed',this.mode === 'context'); this.find('full').setAttribute('aria-pressed',this.mode === 'full');
    this.find('previous').disabled = this.mode !== 'full' || this.page === 0;
    this.find('next').disabled = this.mode !== 'full' || this.page+1 >= result.pageCount;
    const validLine = Number.isInteger(this.data.line) && this.data.line > 0 && this.data.line <= result.totalLines;
    this.find('range').textContent = `Строки ${result.start}–${result.end} из ${result.totalLines}${validLine ? '' : ' · строка из индекса недоступна'}`;
    for (const line of result.lines) {
      const row = document.createElement('div'); row.className = `source-line${line.highlight ? ' source-line-selected' : ''}${this.query && line.text.includes(this.query) ? ' source-line-match' : ''}`;
      const number = document.createElement('span'); number.className = 'source-line-number'; number.textContent = line.number;
      const text = document.createElement('code'); appendSourceTokens(text,this.data,line); row.append(number,text); code.append(row);
    }
    code.scrollTop = 0; this.centerSelectedLine();
  }
}
