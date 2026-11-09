import { requestJson } from './bounded-request.js';

/** Flat filtered snapshot file list; every open action uses a raw ID, never a typed filesystem path. */
export class SourceFileBrowser {
  /** Mount one paginated index; onOpen receives only captured project/snapshot/raw-node locations. */
  constructor(root, onOpen, fetcher) {
    this.root = root; this.onOpen = onOpen; this.fetcher = fetcher; this.revision = 0;
    this.find = name => root.querySelector(`[data-source="${name}"]`);
    this.find('file-query').addEventListener('input', () => { this.close(); this.timer = setTimeout(() => this.load(), 180); });
    this.find('file-previous')?.addEventListener('click', () => this.load(Math.max(0,(this.offset || 0)-100)));
    this.find('file-more').addEventListener('click', () => this.load(this.nextOffset || 0));
  }
  /** A new project or snapshot clears incompatible paths; switching nodes keeps the current list. */
  setContext(context) {
    const scope = JSON.stringify([context.projectId, context.snapshotId]);
    if (scope !== this.scope) { this.close(); this.loaded = false; this.scope = scope; this.context = context; this.find('file-query').value = ''; this.find('files').replaceChildren(); }
    else this.context = context;
  }
  /** Invalidate responses immediately, including during the next query debounce. */
  close() { clearTimeout(this.timer); this.controller?.abort(); this.revision++; }
  /** Catalog reads do not move the map or start indexing; stale responses are discarded. */
  async load(offset = 0) {
    clearTimeout(this.timer); this.controller?.abort(); this.controller = new AbortController(); const revision = ++this.revision;
    const context = {...this.context}; if (!context.projectId || !context.snapshotId) return;
    const status = this.find('file-status'); status.textContent = 'Читаю файлы карты…'; this.find('file-more').disabled = true;
    try {
      const data = await requestJson(`/api/projects/${encodeURIComponent(context.projectId)}/source-files`, {method:'POST',headers:{'Content-Type':'application/json'},signal:this.controller.signal,
        body:JSON.stringify({snapshotId:context.snapshotId,query:this.find('file-query').value,offset,limit:100})}, {fetcher:this.fetcher});
      if (revision !== this.revision) return;
      if (data.snapshotId !== context.snapshotId) throw new Error('Карта обновилась. Откройте просмотр заново.');
      const host = this.find('files'); host.replaceChildren();
      for (const file of data.files) {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = file.file; button.title = file.file;
        button.addEventListener('click', () => this.onOpen({...context,nodeId:file.nodeId,label:file.file,line:file.line})); host.append(button);
      }
      this.loaded = true; this.offset = offset;
      if (this.find('file-previous')) this.find('file-previous').hidden = offset === 0;
      this.nextOffset = data.nextOffset; this.find('file-more').hidden = this.nextOffset == null;
      this.find('file-more').disabled = false; status.textContent = `${offset + data.files.length} / ${data.total} файлов в карте${data.truncated ? ' · есть следующая страница' : ''}. Это индекс, не список всех файлов диска.`;
    } catch (error) { if (revision === this.revision && error.name !== 'AbortError') status.textContent = error.message; }
  }
}
