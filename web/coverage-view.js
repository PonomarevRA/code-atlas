import { el } from './dom.js';
import { codeProjectId } from './filters.js';

const PAGE_SIZE = 50;
const LOOP_KINDS = new Set(['for', 'foreach', 'while', 'do']);
const STATUS_LABELS = { unknown: 'Неизвестно', partial: 'Частично', uncovered: 'Не покрыто', covered: 'Покрыто по отчёту' };

/** Только repository-relative пути; файловая безопасность дополнительно проверяется backend. */
export function coverageReportPaths(value) {
  const paths = String(value || '').split(/[,\n]/).map(path => path.trim()).filter(Boolean);
  const unique = [...new Set(paths)];
  if (unique.length > 8) throw new Error('Укажите не более 8 отчётов.');
  for (const path of unique) {
    if (path.length > 1024 || path.startsWith('/') || path.includes('\\') || /^[a-z][a-z\d+.-]*:/i.test(path)
        || path.split('/').includes('..') || /[\x00-\x1f]/.test(path)) {
      throw new Error('Отчёты должны быть путями внутри репозитория, например artifacts/coverage.xml.');
    }
  }
  return unique;
}

/** URL всегда локальный; raw ID является query data, а не произвольным адресом. */
export function coverageMapUrl(context, nodeId = null, target = null) {
  const query = new URLSearchParams();
  if (context.projectId) query.set('repo', context.projectId);
  if (nodeId) query.set('node', nodeId);
  if (nodeId && target) {
    query.set('view', 'architecture');
    query.set('level', 'symbols');
    query.set('project', target.projectId);
    if (target.file) query.set('file', target.file);
    if (target.page) query.set('page', target.page);
  }
  return `/?${query}`;
}

/** Строит адреса actual символов один раз, используя порядок и размер страниц существующей карты. */
export function coverageMapTargets(nodes) {
  const files = new Map();
  for (const node of nodes) {
    const projectId = codeProjectId(node);
    const file = node.file || null;
    const key = JSON.stringify([projectId, file]);
    if (!files.has(key)) files.set(key, { projectId, file, nodes: [] });
    files.get(key).nodes.push(node);
  }
  const targets = new Map();
  for (const group of files.values()) {
    group.nodes.sort((left, right) => String(left.label || left.id).localeCompare(String(right.label || right.id), 'ru')
      || String(left.id).localeCompare(String(right.id), 'en'));
    for (const [index, node] of group.nodes.entries()) {
      targets.set(node.id, { projectId: group.projectId, file: group.file, page: Math.floor(index / 240), label: node.label });
    }
  }
  return targets;
}

/** Фильтры меняют только captured response, не запускают новый анализ исходников. */
export function filterCoverageConditions(data, filters = {}) {
  const tests = new Map((data.tests || []).map(test => [test.id, test]));
  const query = String(filters.search || '').trim().toLocaleLowerCase();
  const result = [];
  for (const condition of data.conditions || []) {
    let kind = LOOP_KINDS.has(condition.kind) ? 'loop' : String(condition.kind).startsWith('switch') ? 'switch' : condition.kind;
    if (kind === 'conditional') kind = 'if';
    if (filters.kind && filters.kind !== 'all' && kind !== filters.kind) continue;
    if (filters.runtime && filters.runtime !== 'all' && condition.runtime?.status !== filters.runtime) continue;
    const linkedTests = [];
    for (const link of condition.testLinks || []) {
      const test = tests.get(link.testId);
      if (test) linkedTests.push(test);
    }
    if (filters.testKind && filters.testKind !== 'all') {
      const hasKind = condition.testKinds ? condition.testKinds[filters.testKind] > 0 : linkedTests.some(test => test.kind === filters.testKind);
      if (!hasKind) continue;
    }
    const text = [condition.methodLabel, condition.namespace, condition.file, condition.condition,
      ...linkedTests.map(test => test.name)].join(' ').toLocaleLowerCase();
    if (query && !text.includes(query)) continue;
    result.push(condition);
  }
  return result;
}

/** Бейдж отражает только статус отчётных счётчиков; отсутствие данных не означает uncovered. */
function statusBadge(status) {
  const known = Object.hasOwn(STATUS_LABELS, status) ? status : 'unknown';
  return el('span', STATUS_LABELS[known], `coverage-badge coverage-badge-${known}`);
}

/** Контекст выполнения тестов остаётся unknown независимо от атрибутов и количества theory examples. */
function testDescription(test) {
  const type = test.kind === 'gherkin' ? 'Gherkin' : 'Unit';
  const framework = test.framework === 'unknown' ? 'фреймворк не сопоставлен' : test.framework || test.subtype || 'формат не указан';
  let description = `${type} · ${framework} · выполнение неизвестно`;
  if (test.declaredStatus === 'disabled') description += ' · отключён в исходнике';
  if (test.declaredStatus === 'unknown') description += ' · статус объявления неизвестен';
  if (test.exampleRows) description += ` · ${test.exampleRows}${test.exampleRowsTruncated ? '+' : ''} строк данных, запуск не подтверждён`;
  return description;
}

/** Время показывается до миллисекунд: целые наносекунды превышают точность чисел JavaScript. */
function timestampDescription(nanoseconds) {
  const milliseconds = Number(nanoseconds) / 1e6;
  const date = Number.isFinite(milliseconds) && milliseconds > 0 ? new Date(milliseconds) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toISOString() : 'не указано';
}

/** Hash относится только к файлу отчёта, а не к версии исходников проекта. */
function reportDescription(report) {
  const timestamp = timestampDescription(report.mtimeNs);
  return `Отчёт: ${report.file} · ${report.status} · SHA-256: ${report.sourceHash || 'не указан'} · время файла (до миллисекунд): ${timestamp}`;
}

/** Время отчёта не доказывает совпадение версии исходников; enum остаётся только в metadata. */
function freshnessDescription(value) {
  return {
    stale: 'Исходник новее отчёта; данные могут быть устаревшими.',
    'timestamp-compatible-not-source-hash-proof': 'Время запуска отчёта совместимо с исходником, но совпадение версии по hash не доказано.',
    'mtime-compatible-not-source-hash-proof': 'Время файла отчёта совместимо с исходником, но совпадение версии по hash не доказано.',
    unknown: 'Совпадение версии отчёта и исходника не подтверждено.',
  }[value] || 'Актуальность отчёта не подтверждена.';
}

/** Страница условий: безопасный текст, локальная фильтрация, отдельные доказательства источника/отчёта/тестов. */
export class CoverageView {
  constructor(elements, { onSelect = () => {}, onPage = () => {}, onRuntimeFilter = () => {}, onSource = async () => null } = {}) {
    this.elements = elements;
    this.onSelect = onSelect;
    this.onPage = onPage;
    this.onSource = onSource;
    this.onRuntimeFilter = onRuntimeFilter;
    this.data = null;
    this.filters = {};
    this.page = 0;
    this.testPage = 0;
    this.sourceRevision = 0;
    this.paginationControls = new WeakMap();
  }

  /** Новый анализ полностью заменяет dataset; связанные raw IDs должны существовать в закреплённой карте. */
  setData(data, context, validNodeIds, { conditionId = null, page = 0, mapTargets = new Map() } = {}) {
    this.data = data;
    this.context = context;
    this.validNodeIds = validNodeIds;
    this.mapTargets = mapTargets;
    this.sourceFacts = new Map((data.sourceFacts || []).map(fact => [fact.file, fact]));
    this.page = Number.isSafeInteger(Number(page)) && Number(page) >= 0 ? Number(page) : 0;
    this.testPage = 0;
    this.selectedId = null;
    this.sourceRevision += 1;
    this.renderSummary();
    this.render();
    if (conditionId && data.conditions.some(condition => condition.id === conditionId)) this.select(conditionId, false);
    else this.elements.detail.replaceChildren(el('h2', 'Детали условия'), el('p', 'Выберите условие в списке.'));
  }

  /** Сводка перечисляет только наблюдаемые данные и не вычисляет общий процент покрытия. */
  renderSummary() {
    const counts = this.data.summary || {};
    const summary = this.elements.summary;
    summary.replaceChildren();
    for (const [key, label] of [['conditions', 'условий'], ['methods', 'методов'], ['tests', 'объявлений тестов в инвентаре'], ['reports', 'отчётов']]) {
      const fallback = this.data[key]?.length || 0;
      summary.append(el('span', `${counts[key] ?? fallback} ${label}`, 'coverage-stat'));
    }
    const statuses = { covered: 0, partial: 0, uncovered: 0, unknown: 0 };
    for (const condition of this.data.conditions || []) {
      const status = Object.hasOwn(statuses, condition.runtime?.status) ? condition.runtime.status : 'unknown';
      statuses[status] += 1;
    }
    for (const status of ['unknown', 'partial', 'uncovered', 'covered']) {
      const count = counts.conditionsByStatus?.[status] ?? statuses[status];
      const button = el('button', `${count} · ${STATUS_LABELS[status]}`, 'coverage-stat');
      button.type = 'button';
      button.title = 'Отфильтровать условия по статусу счётчиков отчёта';
      button.addEventListener('click', () => this.onRuntimeFilter(status));
      summary.append(button);
    }
    if (counts.conditions > (this.data.conditions || []).length) {
      summary.append(el('span', `Возвращено ${(this.data.conditions || []).length} из ${counts.conditions} найденных условий`, 'coverage-stat'));
    }
    if (this.data.scope?.mode === 'node-chain') summary.append(el('p', 'Условия относятся к выбранной цепочке; инвентарь тестов — к просканированным файлам репозитория. Связи с условиями показаны отдельно.', 'coverage-muted'));
    const warnings = [...(this.data.warnings || [])];
    if (this.data.status !== 'ready' || Object.values(this.data.truncation || {}).some(Boolean)) {
      warnings.unshift('Анализ ограничен: найденные элементы не являются полным инвентарём репозитория.');
    }
    if (!(this.data.reports || []).some(report => report.status === 'parsed' || report.status === 'partial')) {
      warnings.unshift('Нет принятого отчёта выполнения. Покрытие условий неизвестно.');
    }
    this.elements.warnings.replaceChildren(...warnings.slice(0, 12).map(warning => el('p', String(warning))));
    if (warnings.length > 12) this.elements.warnings.append(el('p', `Показано 12 из ${warnings.length} предупреждений.`, 'coverage-muted'));
    const bounds = el('details');
    bounds.append(el('summary', 'Границы анализа и отчёты'));
    const omissions = Object.entries(this.data.truncation || {}).filter(([, limited]) => limited).map(([name]) => name);
    bounds.append(el('p', omissions.length ? `Ограничена выборка: ${omissions.join(', ')}` : 'Сканер не сообщил усечения выборки.', 'coverage-muted'));
    for (const [name, value] of Object.entries(this.data.limits || {})) bounds.append(el('p', `${name}: ${value}`, 'coverage-muted'));
    for (const report of this.data.reports || []) {
      bounds.append(el('p', reportDescription(report), 'coverage-muted'));
      for (const warning of report.warnings || []) bounds.append(el('p', String(warning), 'coverage-muted'));
    }
    this.elements.warnings.append(bounds);
  }

  /** Переключение фильтра сбрасывает обе страницы; текстовые данные не уходят в сеть. */
  setFilters(filters, { resetPage = true } = {}) {
    this.filters = filters;
    if (resetPage) {
      this.page = 0;
      this.testPage = 0;
    }
    if (this.data) this.render();
  }

  /** Ограничивает DOM 50 условиями, сохраняя группы файлов/методов и общий счётчик выборки. */
  render() {
    const conditions = filterCoverageConditions(this.data, this.filters);
    const pages = Math.max(1, Math.ceil(conditions.length / PAGE_SIZE));
    this.page = Math.min(this.page, pages - 1);
    this.elements.count.textContent = `${conditions.length} из ${(this.data.conditions || []).length} условий в результате · страница ${this.page + 1} из ${pages}`;
    const root = this.elements.conditions;
    root.replaceChildren();
    this.conditionButtons = new Map();
    const files = new Map();
    const methods = new Map();
    for (const condition of conditions.slice(this.page * PAGE_SIZE, (this.page + 1) * PAGE_SIZE)) {
      const filename = condition.file || 'Файл не сопоставлен';
      let file = files.get(filename);
      if (!file) {
        file = el('section', undefined, 'coverage-file');
        file.append(el('h2', filename));
        files.set(filename, file);
        root.append(file);
      }
      const key = `${filename}\u0000${condition.methodId || condition.ownerNodeId || condition.methodLabel}`;
      let method = methods.get(key);
      if (!method) {
        method = el('div', undefined, 'coverage-method');
        method.append(el('h3', condition.methodLabel || 'Метод не сопоставлен'));
        methods.set(key, method);
        file.append(method);
      }
      const button = el('button', undefined, 'coverage-condition');
      button.type = 'button';
      button.setAttribute('aria-pressed', String(this.selectedId === condition.id));
      const title = el('span', undefined, 'coverage-condition-title');
      title.append(el('span', `${condition.kind} · строка ${condition.line || '—'}`), statusBadge(condition.runtime?.status));
      button.append(title, el('span', condition.condition || 'Выражение не извлечено', 'coverage-condition-expression'));
      const caption = condition.sourceStatus === 'parsed' ? 'Источник разобран' : 'Источник разобран частично или не сопоставлен';
      button.append(el('span', caption, 'coverage-condition-location'));
      if (condition.testLinksTruncated) button.append(el('span', `Показано ${(condition.testLinks || []).length} из ${condition.testLinksTotal} статических связей с тестами`, 'coverage-condition-location'));
      button.addEventListener('click', () => this.select(condition.id));
      this.conditionButtons.set(condition.id, button);
      method.append(button);
    }
    if (!conditions.length) root.append(el('p', 'Условий с выбранными фильтрами нет.'));
    this.elements.pagination.replaceChildren();
    if (pages > 1) this.pagination(this.elements.pagination, this.page, pages, next => { this.page = next; this.render(); this.onPage(next); });
    this.renderInventory();
  }

  /** Обычные кнопки обеспечивают keyboard navigation без собственного перехвата клавиш. */
  pagination(root, page, pages, change) {
    const previous = el('button', '← Предыдущая');
    const next = el('button', 'Следующая →');
    previous.type = next.type = 'button';
    previous.disabled = page === 0;
    next.disabled = page >= pages - 1;
    previous.addEventListener('click', () => {
      change(page - 1);
      const current = this.paginationControls.get(root);
      (current.previous.disabled ? current.next : current.previous).focus?.({ preventScroll: true });
    });
    next.addEventListener('click', () => {
      change(page + 1);
      const current = this.paginationControls.get(root);
      (current.next.disabled ? current.previous : current.next).focus?.({ preventScroll: true });
    });
    this.paginationControls.set(root, { previous, next });
    root.append(previous, el('span', `${page + 1} / ${pages}`), next);
  }

  /** Инвентарь исходников не преобразует disabled/theory/scenario declarations в выполненные тесты. */
  renderInventory() {
    const query = String(this.filters.search || '').toLocaleLowerCase().trim();
    const tests = (this.data.tests || []).filter(test => (!this.filters.testKind || this.filters.testKind === 'all' || test.kind === this.filters.testKind)
      && (!query || [test.name, test.namespace, test.file].join(' ').toLocaleLowerCase().includes(query)));
    const pages = Math.max(1, Math.ceil(tests.length / PAGE_SIZE));
    this.testPage = Math.min(this.testPage, pages - 1);
    this.elements.tests.replaceChildren();
    for (const test of tests.slice(this.testPage * PAGE_SIZE, (this.testPage + 1) * PAGE_SIZE)) this.elements.tests.append(this.testCard(test));
    if (!tests.length) this.elements.tests.append(el('p', 'Тестов с выбранными фильтрами нет.'));
    this.elements.testsCount.textContent = `${tests.length} из ${(this.data.tests || []).length} объявлений · страница ${this.testPage + 1} из ${pages}`;
    if (pages > 1) this.pagination(this.elements.tests, this.testPage, pages, next => { this.testPage = next; this.renderInventory(); });
  }

  /** Ссылку на карту получает только raw node из текущего graph dataset. */
  testCard(test) {
    const card = el('div', undefined, 'coverage-test');
    card.append(el('strong', test.name || test.id), el('p', testDescription(test), 'coverage-muted'));
    card.append(el('p', `${test.file || 'Файл не указан'}:${test.line || '—'}`, 'coverage-muted'));
    for (const reason of test.skipReasons || []) card.append(el('p', `Причина отключения: ${reason}`));
    if (test.tags?.length) card.append(el('p', `Теги: ${test.tags.join(', ')}`, 'coverage-muted'));
    if (test.nodeId && this.validNodeIds.has(test.nodeId)) {
      const link = el('a', 'Тест на карте');
      link.textContent = 'Тест на текущей карте';
      link.title = 'Откроется текущая опубликованная карта. Символ мог измениться после анализа.';
      link.href = coverageMapUrl(this.context, test.nodeId, this.mapTargets.get(test.nodeId));
      card.append(link);
    }
    if (test.staticTargetsTruncated) card.append(el('p', `Показано ${(test.staticTargetIds || []).length} из ${test.staticTargetsTotal} статически достижимых целей. Выполнение не подтверждено.`, 'coverage-muted'));
    if (test.steps?.length) {
      const steps = el('details');
      steps.append(el('summary', 'Шаги сценария · не подтверждение выполнения'));
      for (const step of test.steps.slice(0, 12)) {
        steps.append(el('p', `${step.text} · ${step.file || test.file}:${step.line || '—'}`));
        const status = { 'exact-literal': 'Найдена буквальная привязка шага', ambiguous: 'Несколько возможных привязок', unresolved: 'Привязка не сопоставлена' }[step.bindingStatus];
        if (status) steps.append(el('p', status, 'coverage-muted'));
      }
      if (test.steps.length > 12) steps.append(el('p', `Показано 12 из ${test.steps.length} сохранённых шагов.`, 'coverage-muted'));
      card.append(steps);
    }
    return card;
  }

  /** Доказательства сокращённых путей сохраняют кандидатов и не превращаются в прямой вызов. */
  renderTestLinks(detail, condition) {
    detail.append(el('h3', 'Связанные тесты · статические кандидаты'));
    const links = condition.testLinks || [];
    if (condition.testLinksTruncated) detail.append(el('p', `Показано ${links.length} из ${condition.testLinksTotal} статических связей. Остальные доказательства не включены в ответ.`, 'coverage-muted'));
    const tests = new Map((this.data.tests || []).map(test => [test.id, test]));
    for (const link of links) {
      const test = tests.get(link.testId);
      detail.append(test ? this.testCard(test) : el('p', 'Тест связи не включён в текущий инвентарь.', 'coverage-muted'));
      const evidence = link.confidence === 'source' ? 'Связь по исходнику' : 'Возможная связь';
      detail.append(el('p', `${evidence} · ${link.sourceKind || 'источник не указан'}. Выполнение теста не подтверждено.`, 'coverage-muted'));
      if (link.bindingEvidence) {
        const binding = link.bindingEvidence;
        detail.append(el('p', `Статическая привязка шага: ${binding.pattern || 'шаблон не указан'} · ${binding.file}:${binding.line || '—'}`, 'coverage-muted'));
      }
      const relations = link.relations || [];
      detail.append(el('p', `Связи статического пути: ${relations.slice(0, 6).join(' → ') || 'не указаны'}${relations.length > 6 ? ` · ещё ${relations.length - 6}` : ''}`, 'coverage-muted'));
      if (link.pathTruncated) detail.append(el('p', `Путь из ${link.pathLength} узлов сокращён: середина не включена. Конечные точки не означают прямой вызов.`, 'coverage-muted'));
      const path = link.pathTruncated ? [...(link.pathPrefix || []), ...(link.pathSuffix || [])] : link.path || [];
      const nodes = el('ul');
      for (const id of path) {
        const item = el('li');
        const target = this.mapTargets.get(id);
        const label = target?.label || id;
        if (this.validNodeIds.has(id)) {
          const anchor = el('a', `${label} · текущая карта`);
          anchor.href = coverageMapUrl(this.context, id, target);
          item.append(anchor);
        } else item.append(el('span', `${label} · узел отсутствует в этой карте`));
        nodes.append(item);
      }
      if (path.length) detail.append(nodes);
    }
    if (!links.length) detail.append(el('p', 'Связанных тестов не найдено в этой выборке; отсутствие связи не доказывает отсутствие тестов.', 'coverage-muted'));
  }

  /** Детали не привязывают счётчики строки к отдельному true/false/case objective. */
  select(id, notify = true) {
    const condition = this.data.conditions.find(item => item.id === id);
    if (!condition) return;
    this.selectedId = id;
    this.sourceRevision += 1;
    this.render();
    const detail = this.elements.detail;
    detail.replaceChildren(el('h2', `${condition.kind} · ${condition.methodLabel || 'Метод'}`));
    detail.append(el('p', `${condition.file || 'Файл не сопоставлен'}:${condition.line || '—'}`, 'coverage-muted'),
      el('pre', condition.condition || 'Выражение не извлечено'));
    if (condition.sourceStatus !== 'parsed') detail.append(el('p', 'Источник разобран частично или владелец не сопоставлен. Проверьте исходник и ограничения анализа.', 'coverage-muted'));
    const actions = el('div', undefined, 'coverage-detail-actions');
    if (condition.ownerNodeId && this.validNodeIds.has(condition.ownerNodeId)) {
      const map = el('a', 'Метод на текущей карте');
      map.title = 'Откроется текущая опубликованная карта. Символ мог измениться после анализа.';
      map.href = coverageMapUrl(this.context, condition.ownerNodeId, this.mapTargets.get(condition.ownerNodeId));
      const source = el('button', 'Исходник метода');
      source.type = 'button';
      source.addEventListener('click', () => this.openSource(condition, source));
      actions.append(map, source);
    } else actions.append(el('p', 'Объявление не связано однозначно с raw узлом карты.', 'coverage-muted'));
    detail.append(actions, el('h3', 'Цели проверки'));
    const objectives = el('ul');
    for (const objective of condition.objectives || []) objectives.append(el('li', `${objective.label || objective.kind} · выполнение неизвестно${objective.implicit ? ' · неявная ветвь' : ''}`));
    if (!(condition.objectives || []).length) objectives.append(el('li', 'Цели проверки не извлечены. Проверьте исходник.'));
    detail.append(objectives, el('h3', 'Данные выполнения'), statusBadge(condition.runtime?.status));
    const runtime = condition.runtime || {};
    detail.append(el('p', `Попадания в строку: ${runtime.lineHits ?? 'неизвестно'} · ветви строки: ${runtime.branchesCovered ?? '—'} / ${runtime.branchesValid ?? '—'}`));
    detail.append(el('p', 'Счётчики строки не доказывают выполнение каждого true/false, case или пути внутри цикла.', 'coverage-muted'));
    if (runtime.reason) detail.append(el('p', runtime.reason, 'coverage-muted'));
    detail.append(el('p', freshnessDescription(runtime.freshness), 'coverage-muted'));
    for (const file of runtime.reportPaths || []) {
      const report = (this.data.reports || []).find(item => item.file === file);
      detail.append(el('p', report ? reportDescription(report) : `Отчёт: ${file} · метаданные не включены в результат`, 'coverage-muted'));
    }
    const metadata = el('details');
    metadata.append(el('summary', 'Метаданные источника и отчёта'));
    metadata.append(el('p', `freshness: ${runtime.freshness || 'unknown'}`, 'coverage-muted'));
    const sourceFact = this.sourceFacts.get(condition.file);
    if (sourceFact) {
      metadata.append(el('p', `Текущий исходник ${sourceFact.file} · SHA-256: ${sourceFact.sourceHash || 'не указан'}`, 'coverage-muted'));
      metadata.append(el('p', `Время исходника (до миллисекунд): ${timestampDescription(sourceFact.mtimeNs)} · согласованное чтение: ${sourceFact.stableRead === true ? 'да' : sourceFact.stableRead === false ? 'нет' : 'неизвестно'}`, 'coverage-muted'));
    }
    detail.append(metadata);
    this.renderTestLinks(detail, condition);
    this.sourceContainer = el('div');
    detail.append(this.sourceContainer);
    if (notify) {
      this.revealSelection(id);
      this.onSelect(id);
    }
  }

  /** При пользовательском выборе на узком экране детали находятся под списком, поэтому показываем их и переносим keyboard focus. */
  revealSelection(id) {
    if (globalThis.matchMedia?.('(max-width: 900px)')?.matches) {
      const detail = this.elements.detail;
      detail.setAttribute('tabindex', '-1');
      detail.focus?.({ preventScroll: true });
      detail.scrollIntoView?.({ block: 'start', inline: 'nearest', behavior: 'auto' });
      return;
    }
    this.conditionButtons.get(id)?.focus?.({ preventScroll: true });
  }

  /** Асинхронный source response не может заполнить детали другого выбранного условия. */
  async openSource(condition, button) {
    const revision = ++this.sourceRevision;
    button.disabled = true;
    this.sourceContainer.replaceChildren(el('p', 'Загружаем исходник…', 'coverage-muted'));
    try {
      const source = await this.onSource(condition.ownerNodeId, condition.line);
      if (revision !== this.sourceRevision || this.selectedId !== condition.id) return;
      this.sourceContainer.replaceChildren(el('h3', 'Текущий исходник'), el('p', source.warning || 'Рабочая копия; строки могли измениться.', 'coverage-muted'));
      const lines = String(source.text || '').replace(/\r\n?/g, '\n').split('\n');
      const start = Math.max(0, (condition.line || source.line || 1) - 6);
      const end = Math.min(lines.length, start + 24);
      const snippet = lines.slice(start, end).map((line, index) => `${start + index + 1}  ${line}`).join('\n');
      this.sourceContainer.append(el('pre', snippet));
    } catch (error) {
      if (revision === this.sourceRevision) this.sourceContainer.replaceChildren(el('p', error.message));
    } finally { button.disabled = false; }
  }
}
