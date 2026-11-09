import { el } from './dom.js';
import { CoverageView, coverageMapUrl, coverageMapTargets, coverageReportPaths } from './coverage-view.js';

const ELEMENT_IDS = ['back', 'project', 'scope', 'clear-scope', 'reports', 'analyze', 'status', 'provenance',
  'results', 'summary', 'warnings', 'filters', 'search', 'kind', 'runtime', 'test-kind', 'count',
  'conditions', 'pagination', 'detail', 'tests', 'tests-count'];

/** Только вызываемые символы карты подходят для области метода; файлы и атрибуты исключены. */
function callable(node) {
  if (!node || ['attribute', 'attribute_scope', 'file', 'project', 'note'].includes(node.kind)) return false;
  return node._callable === true || ['method', 'constructor', 'local-function'].includes(node.kind)
    || (node.kind === 'symbol' && /\([^)]*\)/.test(String(node.label || '')));
}

/** Отдельная страница чтения: первоначальный/явный анализ, отмена старых запросов и локальные фильтры. */
export class CoveragePage {
  constructor(elements, options = {}) {
    this.elements = elements;
    this.fetcher = options.fetcher || ((...args) => fetch(...args));
    this.history = options.history || globalThis.history;
    this.url = new URL(options.href || globalThis.location.href);
    this.projects = [];
    this.nodes = new Map();
    this.mapTargets = new Map();
    this.context = { projectId: this.url.searchParams.get('repo'), snapshotId: this.url.searchParams.get('snapshot'), nodeId: this.url.searchParams.get('node') };
    this.revision = 0;
    this.filters = {
      search: (this.url.searchParams.get('q') || '').slice(0, 500),
      kind: ['if', 'switch', 'loop'].includes(this.url.searchParams.get('kind')) ? this.url.searchParams.get('kind') : 'all',
      runtime: ['unknown', 'partial', 'uncovered', 'covered'].includes(this.url.searchParams.get('runtime')) ? this.url.searchParams.get('runtime') : 'all',
      testKind: ['unit', 'gherkin'].includes(this.url.searchParams.get('tests')) ? this.url.searchParams.get('tests') : 'all',
    };
    this.view = new CoverageView({
      summary: elements.summary, warnings: elements.warnings, conditions: elements.conditions,
      count: elements.count, pagination: elements.pagination, detail: elements.detail,
      tests: elements.tests, testsCount: elements['tests-count'],
    }, {
      onSelect: id => this.replaceUrl({ condition: id }),
      onPage: page => this.replaceUrl({ page }),
      onRuntimeFilter: status => {
        elements.runtime.value = status;
        this.filter();
      },
      onSource: (nodeId, line) => this.source(nodeId, line),
    });
    elements.search.value = this.filters.search;
    elements.kind.value = this.filters.kind;
    elements.runtime.value = this.filters.runtime;
    elements['test-kind'].value = this.filters.testKind;
    elements.reports.value = this.url.searchParams.getAll('report').join('\n');
    elements.filters.addEventListener('submit', event => event.preventDefault());
    elements.analyze.addEventListener('click', () => this.analyze());
    elements.project.addEventListener('change', () => {
      this.context = { projectId: elements.project.value, snapshotId: null, nodeId: null };
      this.replaceUrl({ repo: this.context.projectId, snapshot: null, node: null, condition: null, page: null });
      this.loadProject(false);
    });
    elements['clear-scope'].addEventListener('click', () => {
      this.context.nodeId = null;
      this.replaceUrl({ node: null, condition: null, page: null });
      this.analyze();
    });
    elements.reports.addEventListener('input', () => {
      this.invalidate();
      elements.status.textContent = 'Пути отчётов изменены. Нажмите «Анализировать».';
      elements.analyze.textContent = 'Анализировать';
      elements.analyze.disabled = false;
    });
    for (const name of ['search', 'kind', 'runtime', 'test-kind']) {
      elements[name].addEventListener(name === 'search' ? 'input' : 'change', () => this.filter());
    }
    this.view.setFilters(this.filters);
    this.updateScope();
  }

  /** Меняет адрес текущей страницы; фильтры и выбор условия не добавляют записи в историю браузера. */
  replaceUrl(values) {
    for (const [key, value] of Object.entries(values)) {
      if (value == null || value === '' || value === 'all' || value === 0) this.url.searchParams.delete(key);
      else this.url.searchParams.set(key, String(value));
    }
    this.history?.replaceState(this.history.state, '', this.url.pathname + this.url.search + this.url.hash);
  }

  /** Проверенные пути сохраняются при перезагрузке; черновой ввод не меняет применённые пути в URL. */
  replaceReports(paths) {
    this.url.searchParams.delete('report');
    for (const path of paths) this.url.searchParams.append('report', path);
    this.replaceUrl({});
  }

  /** Отбрасывает старые ответы, даже если транспорт не обработал AbortSignal. */
  invalidate() {
    this.revision += 1;
    this.controller?.abort();
    this.elements.results.hidden = true;
    this.view.sourceRevision += 1;
    return this.revision;
  }

  /** Один controller отменяет карту и анализ вместе, сохраняя согласованность снимка. */
  requestStart(message) {
    const revision = this.invalidate();
    this.controller = new AbortController();
    this.elements.analyze.disabled = true;
    this.elements.status.textContent = message;
    return { revision, signal: this.controller.signal };
  }

  /** Читает JSON один раз; вызывающий код отображает ошибку обычным текстом. */
  async json(path, options = {}) {
    const response = await this.fetcher(path, options);
    const data = await response.json();
    if (!response.ok) {
      const message = data.error || (typeof data.detail === 'string' ? data.detail : `HTTP ${response.status}`);
      throw new Error(message);
    }
    return data;
  }

  /** Перечисляет локальные проекты; только валидная ссылка repo+snapshot запускает первый анализ. */
  async start() {
    const request = this.requestStart('Загружаем проекты…');
    try {
      const data = await this.json('/api/projects', { signal: request.signal });
      if (request.revision !== this.revision) return;
      this.projects = (data.projects || []).filter(project => project.snapshotId);
      this.elements.project.replaceChildren();
      const placeholder = el('option', 'Выберите проект');
      placeholder.value = '';
      this.elements.project.append(placeholder);
      for (const project of this.projects) {
        const label = project.name || project.path?.split('/').filter(Boolean).at(-1) || project.id;
        const option = el('option', label);
        option.value = project.id;
        this.elements.project.append(option);
      }
      if (!this.projects.length) throw new Error('Нет опубликованных проектов. Добавьте репозиторий на карте Code Atlas.');
      const requested = this.context.projectId;
      if (requested && !this.projects.some(project => project.id === requested)) {
        this.elements.project.value = '';
        throw new Error('Проект из ссылки не найден. Выберите опубликованный проект.');
      }
      this.context.projectId ||= this.projects[0].id;
      this.elements.project.value = this.context.projectId;
      const auto = Boolean(requested && this.context.snapshotId);
      await this.loadProject(auto);
    } catch (error) {
      if (request.revision === this.revision) this.elements.status.textContent = error.message;
    } finally {
      if (request.revision === this.revision) this.elements.analyze.disabled = false;
    }
  }

  /** Читает существующий graph API; устаревший снимок требует явного обновления анализа. */
  async graph(request, expectedSnapshot = null) {
    const graph = await this.json(`/api/projects/${encodeURIComponent(this.context.projectId)}/graph`, { signal: request.signal });
    if (request.revision !== this.revision) return null;
    const snapshot = graph.metadata?.snapshotId;
    if (!snapshot) throw new Error('У карты нет опубликованного snapshotId.');
    this.nodes = new Map((graph.nodes || []).map(node => [node.id, node]));
    this.mapTargets = coverageMapTargets(this.nodes.values());
    if (expectedSnapshot && expectedSnapshot !== snapshot) throw new Error('Снимок из ссылки устарел. Нажмите «Анализировать», чтобы использовать текущую карту.');
    this.context.snapshotId = snapshot;
    this.replaceUrl({ repo: this.context.projectId, snapshot, node: this.context.nodeId });
    this.updateScope();
    return graph;
  }

  /** Смена проекта загружает контекст; первоначальная валидная ссылка также запускает анализ. */
  async loadProject(auto) {
    const expectedSnapshot = auto ? this.context.snapshotId : null;
    const request = this.requestStart('Загружаем снимок карты…');
    try {
      if (!await this.graph(request, expectedSnapshot)) return;
      if (auto) await this.analysis(request, coverageReportPaths(this.elements.reports.value));
      else this.elements.status.textContent = 'Проект выбран. Нажмите «Анализировать».';
    } catch (error) {
      if (request.revision === this.revision) this.elements.status.textContent = error.message;
    } finally {
      if (request.revision === this.revision) this.elements.analyze.disabled = false;
    }
  }

  /** Явный анализ читает текущую карту и пути отчётов; тесты репозитория не запускаются. */
  async analyze() {
    let paths;
    try { paths = coverageReportPaths(this.elements.reports.value); }
    catch (error) { this.elements.status.textContent = error.message; return; }
    if (!this.projects.some(project => project.id === this.context.projectId)) {
      this.elements.status.textContent = 'Выберите опубликованный проект.';
      return;
    }
    const request = this.requestStart('Анализируем текущий снимок…');
    try {
      if (!await this.graph(request)) return;
      await this.analysis(request, paths);
    } catch (error) {
      if (request.revision === this.revision) this.elements.status.textContent = error.message;
    } finally {
      if (request.revision === this.revision) this.elements.analyze.disabled = false;
    }
  }

  /** Область привязана к raw методу; результат отчёта не выводится из статических кандидатов тестов. */
  async analysis(request, reportPaths) {
    const context = { ...this.context };
    if (context.nodeId && !callable(this.nodes.get(context.nodeId))) {
      throw new Error('Цепочка доступна для метода текущей карты. Нажмите «Весь репозиторий», чтобы изменить область.');
    }
    this.replaceReports(reportPaths);
    this.elements.status.textContent = 'Читаем условия, тесты и локальные отчёты…';
    const body = { snapshotId: context.snapshotId, reportPaths };
    if (context.nodeId) body.nodeId = context.nodeId;
    const data = await this.json(`/api/projects/${encodeURIComponent(context.projectId)}/test-coverage`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: request.signal,
    });
    if (request.revision !== this.revision) return;
    if (data.snapshotId !== context.snapshotId || (data.projectId && data.projectId !== context.projectId)
        || (data.scope?.nodeId || null) !== (context.nodeId || null)) throw new Error('Ответ относится к другой области или снимку. Повторите анализ.');
    this.view.setData(data, context, new Set(this.nodes.keys()), {
      conditionId: this.url.searchParams.get('condition'), page: this.url.searchParams.get('page'),
      mapTargets: this.mapTargets,
    });
    this.view.setFilters(this.filters, { resetPage: false });
    this.replaceUrl({ condition: this.view.selectedId, page: this.view.page });
    this.elements.results.hidden = false;
    this.elements.status.textContent = data.status === 'ready' ? 'Анализ завершён.' : 'Анализ завершён с ограничениями. Проверьте предупреждения.';
    this.elements.provenance.textContent = `Снимок: ${data.snapshotId} · ${data.analyzerVersion} · источник: ${data.sourceKind}. Анализ не запускает тесты.`;
    this.elements.analyze.textContent = 'Повторить анализ';
  }

  /** Фильтры перерисовывают готовый ответ и заменяют query-параметры без повторного анализа. */
  filter() {
    this.filters = { search: this.elements.search.value.slice(0, 500), kind: this.elements.kind.value,
      runtime: this.elements.runtime.value, testKind: this.elements['test-kind'].value };
    this.view.setFilters(this.filters);
    this.replaceUrl({ q: this.filters.search, kind: this.filters.kind, runtime: this.filters.runtime, tests: this.filters.testKind, page: null });
  }

  /** Запрашивает исходник только существующего raw метода и проверяет контекст ответного снимка. */
  async source(nodeId) {
    if (!callable(this.nodes.get(nodeId))) throw new Error('Исходник доступен только для метода текущей карты.');
    const revision = this.revision;
    const context = { ...this.context };
    const result = await this.json(`/api/projects/${encodeURIComponent(context.projectId)}/source`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nodeId, snapshotId: context.snapshotId }),
    });
    if (revision !== this.revision || result.snapshotId !== context.snapshotId) throw new Error('Контекст изменился. Откройте исходник заново.');
    return result;
  }

  /** Явный сброс области и ссылки на карту сохраняют текущий репозиторий и raw ID метода. */
  updateScope() {
    const node = this.nodes.get(this.context.nodeId);
    this.elements.scope.textContent = this.context.nodeId ? `Цепочка метода: ${node?.label || this.context.nodeId}` : 'Весь репозиторий';
    this.elements['clear-scope'].hidden = !this.context.nodeId;
    this.elements.back.href = coverageMapUrl(this.context, this.context.nodeId, this.mapTargets.get(this.context.nodeId));
    this.elements.back.textContent = '← Текущая карта кода';
    this.elements.back.title = 'Откроется текущая опубликованная карта. Символ мог измениться после анализа.';
  }
}

// DOM и транспорт внедряются для проверки отдельной страницы; состояние основной карты не импортируется.
if (globalThis.document?.getElementById?.('coverage-root')) {
  const elements = {};
  for (const id of ELEMENT_IDS) elements[id] = document.getElementById(`coverage-${id}`);
  new CoveragePage(elements).start();
}
