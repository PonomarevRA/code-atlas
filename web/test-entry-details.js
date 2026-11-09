import { el } from './dom.js';
import { appendDatasetTable } from './test-dataset-table.js';

const SCOPES = { declaration: 'метод', type: 'класс', parameter: 'параметр', file: 'файл', 'inherited-type': 'базовый класс' };

/** Compact annotation evidence stays distinct from executable method cards. */
export function annotationEvidenceCard(annotation, { onSource = () => {}, ownerId = null } = {}) {
  const card = el('article', undefined, 'test-annotation-card');
  const name = String(annotation.name || annotation.qualifiedName || 'аннотация').replace(/^@/, '');
  card.append(el('strong', `@${name.split('.').pop()}`));
  const scope = SCOPES[annotation.scope] || annotation.scope;
  if (scope) card.append(el('small', `${scope}${annotation.parameterName ? ` · ${annotation.parameterName}` : ''}`));
  if (annotation.text) card.append(el('code', annotation.text));
  card.title = annotation.text || annotation.qualifiedName || name;
  if (annotation.file && Number.isInteger(annotation.line) && (annotation.ownerNodeId || ownerId)) {
    const button = el('button', `Строка ${annotation.line}`, 'text-button');
    button.addEventListener('click', () => onSource(annotation.ownerNodeId || ownerId, annotation.line));
    card.append(button);
  }
  return card;
}

/** Raw provider IDs are navigation targets; synthetic test-entry IDs never reach the inspector. */
function appendReferences(root, facts, onNode) {
  for (const id of facts.nodeIds || []) {
    const button = el('button', 'Открыть связанный метод', 'text-button');
    button.setAttribute('aria-label', `Открыть связанный метод ${facts.label || id}`);
    button.addEventListener('click', () => onNode(id));
    root.append(button);
  }
}

/** Render bounded parameter-provider evidence; displayed rows never imply executed cases. */
function appendDataSources(section, entry, options) {
  const { onNode } = options;
  section.append(el('h3', 'Данные и варианты'));
  if (!entry.dataSources?.length && !entry.detailTruncated) section.append(el('p', 'Явный источник параметров не найден. Зависимости тела теста изучайте на схеме.', 'small muted'));
  for (const source of entry.dataSources || []) {
    const card = el('article', undefined, 'test-data-card');
    card.append(el('strong', source.label || source.kind || 'Источник данных'));
    if (source.expression) card.append(el('code', source.expression));
    if (Number.isInteger(source.declaredRowCount)) card.append(el('p', `Объявлено наборов: ${source.declaredRowCount}. Значения выражений не вычислялись.`, 'small'));
    if (source.status === 'unresolved' || source.status === 'unknown') card.append(el('p', 'Источник не сопоставлен однозначно.', 'small muted'));
    if (source.reason) card.append(el('p', String(source.reason), 'small muted'));
    if (source.bindingReason) card.append(el('p', String(source.bindingReason), 'small muted'));
    appendReferences(card, source, onNode);
    if (source.file) card.append(el('small', `${source.file}:${source.line || '?'}`));
    if (source.rows?.length || options.loadRows) appendDatasetTable(card, source, entry, options);
    section.append(card);
  }

}

/** Keep declared setup and inherited context separate from runtime ordering. */
function appendFixtures(section, entry, onNode) {
  section.append(el('h3', 'Подготовка и окружение'));
  if (!entry.fixtures?.length && !entry.detailTruncated) section.append(el('p', 'Подготовка не обнаружена в доступных объявлениях; это не доказывает её отсутствие.', 'small muted'));
  for (const fixture of entry.fixtures || []) {
    const card = el('article', undefined, 'test-data-card');
    card.append(el('strong', `${fixture.inherited ? 'Унаследовано · ' : ''}${fixture.label || 'Подготовка'}`));
    const phase = { beforeAll: 'до всех тестов', afterAll: 'после всех тестов', beforeEach: 'перед тестом', afterEach: 'после теста', 'before-all': 'до всех тестов', 'after-all': 'после всех тестов', 'before-each': 'перед тестом', 'after-each': 'после теста' }[fixture.phase] || fixture.phase;
    if (phase || fixture.scope) card.append(el('small', [phase, SCOPES[fixture.scope] || fixture.scope].filter(Boolean).join(' · ')));
    card.append(el('small', `${fixture.file || ''}:${fixture.line || '?'} · порядок и выполнение не установлены`));
    appendReferences(card, fixture, onNode);
    for (const resource of fixture.resources || []) card.append(el('code', typeof resource === 'string' ? resource : resource.path || resource.expression || resource.file || JSON.stringify(resource)));
    section.append(card);
  }

}

/** Group annotation cards by their captured scope and source owner. */
function appendAnnotations(section, entry, onSource) {
  const annotations = el('details');
  annotations.append(el('summary', `Аннотации и атрибуты · ${entry.annotations?.length ?? entry.annotationCount ?? 'неизвестно'}`));
  const cards = el('div', undefined, 'test-annotation-list');
  for (const annotation of entry.annotations || []) cards.append(annotationEvidenceCard(annotation, { onSource, ownerId: entry.handlerNodeIds?.[0] }));
  annotations.append(cards); section.append(annotations);
}

/** Keep framework labels available without pushing the test inputs below a long metadata list. */
function appendMetadata(section, entry) {
  if (!entry.metadata?.length) return;
  const details = el('details');
  details.append(el('summary', `Метаданные теста · ${entry.metadata.length}`));
  for (const item of entry.metadata) {
    const value = item.values?.join(' · ') || item.value || item.expression || item.text || 'см. аннотацию ниже';
    details.append(el('p', `${item.name || item.label || 'Метаданные'}: ${value}`, 'small'));
  }
  section.append(details);
}

/** Show declared inputs and setup without evaluating providers or promising runner execution. */
export function appendTestEntryDetails(root, entry, { onNode = () => {}, onSource = () => {}, loadRows, isCurrent } = {}) {
  const section = el('section', undefined, 'test-entry-details');
  if (entry.containerNodeId) {
    const overview = el('button', 'Все тесты класса', 'text-button');
    overview.addEventListener('click', () => onNode(entry.containerNodeId)); section.append(overview);
  }
  section.append(el('h3', 'Что проверяет тест'), el('p', entry.label));
  section.append(el('p', `${entry.framework || 'Тестовый фреймворк'} · ${entry.declaredStatus === 'disabled' ? 'отключён в исходнике' : entry.declaredStatus === 'enabled' ? 'объявлен в исходнике' : 'конфигурация не определена'}`, 'small'));
  if (entry.disabledReason) section.append(el('p', entry.disabledReason, 'small muted'));
  section.append(el('p', 'Результат запуска неизвестен. Показаны объявления, выражения и связи исходников.', 'small muted'));
  if (entry.detailTruncated) section.append(el('p', 'Часть подробных фактов не вошла в ответ. Отсутствующие сведения не означают отсутствие данных или подготовки.', 'small muted'));
  if (entry.detailWarning) section.append(el('p', entry.detailWarning, 'small muted'));
  if (entry.tags?.length) section.append(el('p', `Метки: ${entry.tags.join(' · ')}`, 'small'));
  const testNodeId = entry.handlerNodeIds?.[0];
  if (testNodeId) {
    const source = el('button', 'Исходник теста', 'text-button');
    source.addEventListener('click', () => onSource(testNodeId, entry.line)); section.append(source);
  }
  const contextRoot = el('section', undefined, 'test-parameter-context');
  /** API pages may retain complete parameters when the graph-node preview was bounded. */
  function renderContext(parameters) {
    contextRoot.replaceChildren();
    const context = (parameters || []).filter(parameter => parameter.role && parameter.role !== 'indexed');
    if (!context.length) return;
    contextRoot.append(el('h3', 'Контекст и преобразование аргументов'));
    for (const parameter of context) contextRoot.append(el('p', `${parameter.name}${parameter.type ? ` · ${parameter.type}` : ''} · ${parameter.role === 'context' ? 'контекст расширения; не ячейка набора данных' : parameter.role === 'aggregator' ? 'агрегатор аргументов; привязка требует проверки' : 'роль не установлена'}`, 'small'));
  }
  appendDataSources(section, entry, { onNode, onSource, loadRows, isCurrent, onParameters: renderContext });
  renderContext(entry.parameters);
  section.append(contextRoot);
  appendFixtures(section, entry, onNode);
  appendMetadata(section, entry);
  appendAnnotations(section, entry, onSource);
  section.append(el('h3', 'Действия и проверки'), el('p', 'Откройте схему от теста: вызовы подготовки, запросов и assertions показаны в порядке исходника. Утверждения кода не являются фактическим результатом запуска.', 'small'));
  root.append(section);
  return section;
}
