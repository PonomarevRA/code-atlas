/** Управляет отдельным Worker; новый запрос отменяет старый и не возвращает устаревшие результаты. */
export class RegexSearch {
  /** onResult получает только id совпадений, onStatus — текст ошибки или состояние поиска. */
  constructor(onResult, onStatus) {
    this.onResult = onResult;
    this.onStatus = onStatus;
    this.revision = 0;
  }
  /** Завершает Worker и таймеры; используется также при смене репозитория и фильтров. */
  cancel() {
    this.revision++;
    clearTimeout(this.debounce);
    clearTimeout(this.timeout);
    this.worker?.terminate();
    this.worker = null;
  }
  /** Выполняет шаблон спустя 150 мс; 500 мс ограничивают всё время жизни Worker. */
  search(nodes, pattern) {
    this.cancel();
    this.onResult([]);
    if (!pattern) { this.onStatus('Подсветка по имени. Граф и фильтры не изменяются.'); return; }
    const revision = this.revision;
    this.onStatus('Поиск по именам…');
    this.debounce = setTimeout(() => {
      if (revision !== this.revision) return;
      try {
        this.worker = new Worker(new URL('./regex-worker.js', import.meta.url), { type: 'module' });
        this.timeout = setTimeout(() => {
          if (revision !== this.revision) return;
          this.cancel();
          this.onStatus('Шаблон превысил лимит 500 мс. Упростите выражение.', true);
        }, 500);
        this.worker.onmessage = ({ data }) => {
          if (revision !== this.revision) return;
          this.cancel();
          if (data.error) this.onStatus(`Некорректное выражение: ${data.error}`, true);
          else { this.onStatus(''); this.onResult(data.ids); }
        };
        this.worker.onerror = () => {
          if (revision !== this.revision) return;
          this.cancel();
          this.onStatus('Не удалось запустить локальный поиск.', true);
        };
        this.worker.postMessage({ nodes: nodes.map(node => ({ id: node.id, label: node.label })), pattern });
      } catch (error) { this.cancel(); this.onStatus(`Поиск недоступен: ${error.message}`, true); }
    }, 150);
  }
}
