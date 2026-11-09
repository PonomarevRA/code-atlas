import { branchLabels, renderControlFlow, renderDocumentation } from './path-annotations.js';
import { el } from './dom.js';
import { requestJson } from './bounded-request.js';
import { PATH_NODE_LIMIT } from './execution-path-limits.js';
/** Различает исходный код, сохранённый факт и предположительную цель диспетчеризации. */
function confidenceLabel(value) {
  return { source: 'из исходника', graph: 'факт графа', candidate: 'возможная цель', unresolved: 'не сопоставлено' }[value] || value || 'не установлено';
}
/** Строит ограниченное дерево: повторный путь отличается от цикла текущих предков. */
export function buildExecutionTree(data, { maxRows = 600 } = {}) {
  const steps = new Map((data.steps || []).map(step => [step.nodeId, step]));
  const outgoing = new Map();
  for (const edge of data.edges || []) {
    if (!steps.has(edge.source) || !steps.has(edge.target)) continue;
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
    outgoing.get(edge.source).push(edge);
  }
  for (const edges of outgoing.values()) edges.sort((a, b) => (a.sourceLine ?? Infinity) - (b.sourceLine ?? Infinity) || String(a.target).localeCompare(String(b.target)));
  const visited = new Set();
  let rows = 0;
  let limited = false;
  /** Обходит каждый метод один раз, сохраняя отдельные ссылки остальных направленных путей. */
  function visit(id, ancestors, via = null) {
    if (rows >= maxRows) { limited = true; return null; }
    rows++;
    const type = ancestors.has(id) ? 'cycle' : visited.has(id) ? 'reference' : 'step';
    const item = { type, step: steps.get(id), via, children: [] };
    if (type !== 'step') return item;
    visited.add(id);
    const nextAncestors = new Set(ancestors);
    nextAncestors.add(id);
    for (const edge of outgoing.get(id) || []) {
      const child = visit(edge.target, nextAncestors, edge);
      if (child) item.children.push(child);
    }
    return item;
  }
  const roots = [];
  if (steps.has(data.rootNodeId)) roots.push(visit(data.rootNodeId, new Set()));
  for (const id of steps.keys()) if (!visited.has(id)) {
    const root = visit(id, new Set());
    if (root) roots.push(root);
  }
  return { roots: roots.filter(Boolean), rows, limited };
}
/** Отображает статические возможные пути; запросы привязаны к выбранному символу и снимку. */
export class ExecutionPathViewer {
  /** Принимает внедряемый fetch для тестирования отмены и callback исходника по raw nodeId. */
  constructor(root, getContext, options = {}) {
    const { fetcher = (...args) => fetch(...args), onSource = () => {} } = options;
    this.root = root;
    this.getContext = getContext;
    this.fetcher = fetcher;
    this.onSource = onSource;
    this.getCallers = options.getCallers || (() => []);
    this.getEntries = options.getEntries || (() => []);
    this.onResult = options.onResult || (() => {});
    this.onError = options.onError || (() => {});
    this.entry = null;
    this.latestData = null;
    this.latestEntries = [];
    this.revision = 0;
    this.opened = false;
    this.find = name => root.querySelector(`[data-path="${name}"]`);
    this.find('close').addEventListener('click', () => this.close());
    this.find('retry').addEventListener('click', () => this.open());
    this.find('build').addEventListener('click', () => this.open());
    this.find('reset').addEventListener('click', () => {
      this.find('depth').value = 30;
      this.find('nodes').value = PATH_NODE_LIMIT;
      this.open();
    });
    const entry = this.find('entry');
    if (entry) entry.addEventListener('change', () => {
      const id = entry.value;
      this.entry = (this.latestEntries || []).find(item => item.id === id) || null;
      if (this.latestData) this.renderBoard(this.latestData);
    });
  }
  /** Ключ не зависит от фильтров: анализ всегда охватывает полный сохранённый граф. */
  contextKey() {
    const { projectId, snapshotId, nodeId } = this.getContext();
    return JSON.stringify([projectId, snapshotId, nodeId]);
  }
  /** Отменяет устаревший анализ при смене репозитория, снимка или выбранного узла. */
  sync() { if (this.opened && this.key !== this.contextKey()) this.close(); }
  /** Скрывает панель и аннулирует даже ответ fetch, который проигнорировал abort. */
  close() {
    this.controller?.abort();
    this.revision++;
    this.opened = false;
    this.root.hidden = true;
  }
  /** Запрашивает ограниченный серверный анализ; не изменяет карту и её фильтры. */
  async open() {
    const context = this.getContext();
    if (!context.projectId || !context.snapshotId || !context.nodeId) return;
    this.controller?.abort();
    this.controller = new AbortController();
    const revision = ++this.revision;
    this.key = this.contextKey();
    this.opened = true;
    this.root.hidden = Boolean(this.canvasOnly);
    this.find('title').textContent = `Путь выполнения · ${context.label || context.nodeId}`;
    this.find('status').textContent = 'Анализируем возможные вызовы, логи, trace/span и комментарии в текущих исходниках…';
    this.find('tree').replaceChildren();
    const chain = this.find('chain');
    if (chain) chain.replaceChildren();
    this.find('retry').hidden = true;
    this.find('build').disabled = true;
    const maxDepth = Math.max(1, Math.min(30, Math.trunc(Number(this.find('depth').value)) || 30));
    const maxNodes = Math.max(1, Math.min(PATH_NODE_LIMIT, Math.trunc(Number(this.find('nodes').value)) || PATH_NODE_LIMIT));
    this.find('depth').value = maxDepth;
    this.find('nodes').value = maxNodes;
    try {
      const data = await requestJson(`/api/projects/${encodeURIComponent(context.projectId)}/execution-path`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ nodeId: context.nodeId, snapshotId: context.snapshotId, maxDepth, maxNodes }), signal: this.controller.signal }, { fetcher: (...args) => this.fetcher(...args) });
      if (revision !== this.revision || !this.opened || this.key !== this.contextKey()) return;
      if (data.snapshotId !== context.snapshotId || data.rootNodeId !== context.nodeId) throw new Error('Ответ относится к другому символу или снимку. Повторите анализ после обновления карты.');
      this.render(data);
      this.onResult?.(data);
    } catch (error) {
      if (error.name === 'AbortError' || revision !== this.revision || !this.opened || this.key !== this.contextKey()) return;
      this.find('status').textContent = error.message;
      this.onError?.(error.message);
      this.find('retry').hidden = false;
      this.find('build').disabled = false;
    }
  }
  /** Выносит сообщения логов и span из глубоких ветвей без утверждения, что они обязательно выполнятся. */
  observableSummary(data) {
    const events = [];
    const methods = new Map((data.steps || []).map(step => [step.nodeId, step]));
    const callerConditions = new Map();
    for (const edge of data.edges || []) {
      if (!edge.branchContextKnown || !edge.branchIds?.length) continue;
      const caller = methods.get(edge.source);
      const labels = branchLabels(caller?.controlFlow, edge.branchIds);
      if (!callerConditions.has(edge.target)) callerConditions.set(edge.target, []);
      callerConditions.get(edge.target).push(`${caller?.label || edge.source}: ${labels.join(' → ')}`);
    }
    let logs = 0;
    let spans = 0;
    for (const step of data.steps || []) for (const event of step.events || []) {
      if (event.kind !== 'log' && event.kind !== 'span') continue;
      if (event.kind === 'log') logs++; else spans++;
      events.push({ step, event });
    }
    const details = el('details', undefined, 'path-observables');
    details.open = events.length > 0;
    details.append(el('summary', `Логи (${logs}) / Trace/span (${spans})`));
    details.append(el('p', 'Шаблоны, найденные в возможных шагах пути. Их фактическое появление зависит от ветвлений и вызовов.', 'small muted'));
    const list = el('div', undefined, 'path-observable-list');
    for (const { step, event } of events.slice(0, 100)) {
      const row = el('article', undefined, 'path-event');
      row.append(el('strong', `${event.kind === 'log' ? 'Лог' : 'Trace/span'} · ${step.label}`), el('pre', String(event.text || '')), el('small', `${step.file || 'Без исходника'}:${event.line || '?'}${event.conditional ? ' · условная ветвь' : ''}`));
      const conditions = branchLabels(step.controlFlow, event.branchIds);
      if (conditions.length) row.append(el('p', `Условия исходника: ${conditions.join(' → ')}`, 'path-event-conditions'));
      const incoming = callerConditions.get(step.nodeId) || [];
      for (const condition of incoming.slice(0, 10)) row.append(el('p', `Один из возможных вызовов метода при: ${condition}`, 'path-event-conditions'));
      if (incoming.length > 10) row.append(el('p', `Ещё ${incoming.length - 10} условных вызовов — в дереве пути.`, 'small muted'));
      if (step.file) {
        const source = el('button', 'Метод в исходнике');
        source.addEventListener('click', () => this.onSource(step.nodeId));
        row.append(source);
      }
      list.append(row);
    }
    details.append(list);
    if (events.length > 100) details.append(el('p', `Показаны 100 из ${events.length} событий; остальные доступны в шагах методов.`, 'small muted'));
    return details;
  }
  /** Создаёт дерево с ленивым содержимым методов, чтобы большие ответы не блокировали интерфейс. */
  render(data) {
    this.find('build').disabled = false;
    this.latestData = data;
    this.renderChain(data);
    this.renderBoard(data);
    const tree = buildExecutionTree(data, { maxRows: 1200 });
    const flags = Object.entries(data.truncation || {}).filter(([, value]) => value).map(([name]) => ({ nodes: 'число методов', depth: 'глубина', files: 'число файлов', bytes: 'объём исходников', events: 'события метода' }[name] || name));
    const summary = `${data.returnedNodes ?? data.steps?.length ?? 0} из ${data.totalReachableNodes ?? data.steps?.length ?? 0} достижимых символов · ${data.cycles?.length || 0} циклических связей. Статические возможные пути, не реальная трасса выполнения.${flags.length ? ` Пределы: ${flags.join(', ')}.` : ''}${tree.limited ? ' Дерево ограничено 1200 строками.' : ''}`;
    const caveats = el('details', undefined, 'path-caveats');
    caveats.append(el('summary', 'Ограничения анализа'), el('p', data.warning || 'Анализ текущей рабочей копии по статическим свидетельствам.'), el('p', 'Полный снимок: фильтры карты не ограничивают анализ. Порядок событий — расположение в исходнике, а не фактическая последовательность выполнения.'));
    if (flags.length) caveats.append(el('p', 'Увеличьте доступные пределы и перестройте путь. Ограничения файлов, объёма исходников и событий задаются сервером.'));
    this.find('status').replaceChildren(el('p', summary), caveats, this.observableSummary(data));
    const container = this.find('tree');
    container.replaceChildren();
    const elements = new Map();
    /** Рисует одну ветвь; повторные методы представлены ссылками, циклы явно помечены. */
    const renderItem = item => {
      const step = item.step;
      if (item.type !== 'step') {
        const button = el('button', `${item.type === 'cycle' ? '↻ Цикл' : '↗ Повторный путь'}: ${step.label}${item.via ? ` · ${confidenceLabel(item.via.confidence)}` : ''}`, 'path-reference');
        button.addEventListener('click', () => {
          const target = elements.get(step.nodeId);
          for (let parent = target; parent && parent !== container; parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true;
          target?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });
        return button;
      }
      const details = el('details', undefined, 'path-step');
      const summary = el('summary', `${step.label} · ${step.kind || 'символ'}${item.via ? ` · ${item.via.relation} / ${confidenceLabel(item.via.confidence)}${item.via.sourceLine ? ` · строка ${item.via.sourceLine} (${item.via.lineKind === 'working-tree' ? 'исходник' : 'индекс'})` : ''}` : ''}`);
      details.append(summary);
      elements.set(step.nodeId, details);
      const body = el('div', undefined, 'path-step-body');
      details.append(body);
      let populated = false;
      /** События добавляются только после раскрытия конкретного метода. */
      const populate = () => {
        if (populated || !details.open) return;
        populated = true;
        const location = step.file ? `${step.file}:${step.line || '?'}${step.endLine ? `–${step.endLine}` : ''}` : 'Исходник не сопоставлен';
        body.append(el('p', `${location} · ${step.sourceStatus || 'unresolved'}`, 'path-location'));
        if (step.file) {
          const source = el('button', step.kind === 'method' ? 'Исходник метода' : 'Исходник символа');
          source.addEventListener('click', () => this.onSource(step.nodeId));
          body.append(source);
        }
        if (step.signature) body.append(el('pre', String(step.signature), 'node-signature'));
        body.append(renderDocumentation(step.documentation), renderControlFlow(step.controlFlow));
        if (item.via?.branchIds?.length) {
          const caller = (data.steps || []).find(candidate => candidate.nodeId === item.via.source);
          const conditions = branchLabels(caller?.controlFlow, item.via.branchIds);
          body.append(el('p', `Условия вызова из ${caller?.label || item.via.source}: ${conditions.join(' → ')}`, 'path-event-conditions'));
        }
        for (const text of step.unresolved || []) body.append(el('p', String(text), 'small muted'));
        if (!step.events?.length) body.append(el('p', 'В этом шаге события из исходника не выделены.', 'small muted'));
        for (const event of (step.events || []).slice(0, 100)) {
          const eventRow = el('div', undefined, 'path-event');
          eventRow.append(el('strong', `${({ call: 'Вызов', log: 'Лог', span: 'Trace/span', comment: 'Комментарий' })[event.kind] || event.kind} · строка ${event.line || '?'} · ${confidenceLabel(event.confidence)}${event.conditional ? ' · условная ветвь' : ''}${event.awaited ? ' · await' : ''}${event.parallel ? ' · параллельная конструкция' : ''}`), el('pre', String(event.text || event.callee || '')));
          const conditions = branchLabels(step.controlFlow, event.branchIds);
          if (conditions.length) eventRow.append(el('p', `Условия исходника: ${conditions.join(' → ')}`, 'path-event-conditions'));
          if (event.kind === 'call' && !(event.targetNodeIds || []).length) eventRow.append(el('small', 'Цель вызова не сопоставлена с символом графа.'));
          body.append(eventRow);
        }
        if ((step.events?.length || 0) > 100) body.append(el('p', 'Показаны первые 100 событий метода.', 'small muted'));
      };
      details.addEventListener('toggle', populate);
      const children = el('div', undefined, 'path-children');
      for (const child of item.children) children.append(renderItem(child));
      details.append(children);
      if (step.nodeId === data.rootNodeId) { details.open = true; populate(); }
      return details;
    };
    for (const root of tree.roots) container.append(renderItem(root));
    if (!tree.roots.length) container.append(el('p', 'Возможные шаги для этого символа не найдены.'));
    this.root.scrollIntoView?.({ behavior: 'instant', block: 'start' });
  }
  /** Показывает цепочку «кто вызывает → выбранный → куда идёт» без нового анализатора. */
  renderChain(data) {
    const chain = this.find('chain');
    if (!chain) return;
    const callers = (this.getCallers(data.rootNodeId) || []).slice(0, 8).map(node => ({
      id: node.id, label: node.label || node.id, file: node.file,
    }));
    const root = (data.steps || []).find(step => step.nodeId === data.rootNodeId);
    const seen = new Set();
    const callees = [];
    for (const edge of data.edges || []) {
      if (edge.source !== data.rootNodeId || seen.has(edge.target)) continue;
      seen.add(edge.target);
      const step = (data.steps || []).find(item => item.nodeId === edge.target);
      if (step) callees.push({ id: step.nodeId, label: step.label, file: step.file });
    }
    chain.replaceChildren(
      this.chainColumn('Кто вызывает', callers),
      this.chainColumn('Выбранный', root ? [{ id: root.nodeId, label: root.label, file: root.file }] : []),
      this.chainColumn('Куда идёт', callees.slice(0, 8)),
    );
  }
  /** Колонка цепочки: шаг без файла не запрашивает исходник. */
  chainColumn(title, steps) {
    const column = el('div', undefined, 'path-chain-column');
    column.append(el('strong', title));
    if (!steps.length) column.append(el('p', 'Нет шага в загруженной карте', 'small muted'));
    for (const step of steps) {
      const button = el('button', step.label || step.id, 'path-chain-step');
      button.type = 'button';
      button.disabled = !step.file;
      if (step.file) button.addEventListener('click', () => this.onSource(step.id));
      column.append(button);
    }
    return column;
  }
  /** Собирает схему слева направо: вход, затем шаги и развилки if/switch. */
  renderBoard(data) {
    const board = this.find('board');
    if (!board) return;
    const steps = new Map((data.steps || []).map(step => [step.nodeId, step]));
    this.latestEntries = this.getEntries(data.rootNodeId) || [];
    if (this.entry && !this.latestEntries.some(item => item.id === this.entry.id)) this.entry = null;
    if (!this.entry && this.latestEntries.length === 1) this.entry = this.latestEntries[0];
    this.fillEntrySelect();
    const columns = [];
    if (this.entry) columns.push({ title: 'Точка входа', cards: [this.entryCard(this.entry)] });
    const byDepth = new Map();
    const visit = (item, depth) => {
      if (!item || item.type !== 'step') return;
      if (!byDepth.has(depth)) byDepth.set(depth, []);
      byDepth.get(depth).push(item);
      for (const child of item.children || []) visit(child, depth + 1);
    };
    for (const root of buildExecutionTree(data, { maxRows: 800 }).roots) visit(root, 0);
    for (const depth of [...byDepth.keys()].sort((left, right) => left - right)) {
      columns.push({
        title: depth === 0 ? 'Начало' : `Шаг ${depth}`,
        cards: byDepth.get(depth).map(item => this.methodCard(item.step, item.via, steps)),
      });
    }
    board.replaceChildren();
    if (!columns.length) board.append(el('p', 'Шаги пути не найдены.', 'small muted'));
    for (const column of columns) {
      const lane = el('div', undefined, 'path-column');
      lane.append(el('strong', column.title));
      for (const card of column.cards) lane.append(card);
      board.append(lane);
    }
    const flags = Object.entries(data.truncation || {}).filter(([, value]) => value);
    if (flags.length) board.append(el('p', 'Показана не вся достижимая схема: сработал предел анализа. Увеличьте глубину и число символов и перестройте путь.', 'small muted'));
  }
  /** Заполняет выбор входа, не запуская новый анализ. */
  fillEntrySelect() {
    const select = this.find('entry');
    if (!select) return;
    const blank = el('option', 'Без точки входа');
    blank.value = '';
    select.replaceChildren(blank);
    for (const entry of this.latestEntries) {
      const option = el('option', entry.label || entry.route || entry.id);
      option.value = entry.id;
      select.append(option);
    }
    select.value = this.entry?.id || '';
  }
  /** Карточка HTTP/очереди, с которой начинается просмотр пути. */
  entryCard(entry) {
    const card = el('article', undefined, 'unity-node unity-entry');
    const title = entry.kind === 'http' ? `${entry.httpMethod || 'HTTP'} вход` : 'Точка входа';
    card.append(el('header', title));
    card.append(el('p', entry.label || entry.route || entry.id, 'unity-route'));
    if (entry.route) card.append(el('code', entry.route));
    return card;
  }
  /** Карточка метода: порты if/switch и кнопка исходника, если файл известен. */
  methodCard(step, via, steps) {
    const card = el('article', undefined, 'unity-node');
    card.append(el('header', step.label || step.nodeId));
    card.append(el('p', step.file ? `${step.file}:${step.line || '?'}` : 'Исходник не сопоставлен', 'unity-meta'));
    const parent = steps.get(via?.source);
    const labels = branchLabels(parent?.controlFlow, via?.branchIds || []);
    if (labels.length) card.append(el('p', labels.join(' · '), 'unity-meta'));
    else if (parent?.controlFlow?.branches?.length) card.append(el('p', 'Ветвь вызова не сопоставлена с условием или циклом.', 'unity-meta'));
    const branches = step.controlFlow?.branches || [];
    if (!branches.length) card.append(el('p', 'В этом методе условия и циклы не выделены.', 'unity-meta'));
    for (const branch of branches) {
      const block = el('div', undefined, 'unity-branch');
      const kind = branch.kind === 'conditional' ? '?:' : String(branch.kind || '').includes('switch') ? 'switch' : branch.kind;
      block.append(el('strong', `${kind} · строка ${branch.line || '?'}`));
      block.append(el('code', String(branch.header || branch.condition || branch.selector || 'условие не извлечено')));
      for (const arm of branch.arms || []) {
        const port = el('div', undefined, 'unity-port');
        const name = arm.kind === 'true' ? 'истина' : arm.kind === 'false' ? 'иначе' : (arm.label || arm.kind || 'ветвь');
        port.append(el('span', name));
        if (arm.expression) port.append(el('code', arm.expression));
        block.append(port);
      }
      card.append(block);
    }
    if (step.file) {
      const source = el('button', 'Исходник');
      source.type = 'button';
      source.addEventListener('click', () => this.onSource(step.nodeId));
      card.append(source);
    }
    return card;
  }
}
