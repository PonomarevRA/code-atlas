import { el } from './dom.js';

/** Membership uses captured ownership only; file and identifier prefixes are not owners. */
export function testsForContainer(nodeId, entries = [], containerFacts = null) {
  const tests = new Map();
  for (const entry of [...(containerFacts?.tests || []), ...entries]) {
    if (entry.kind !== 'test' || entry.containerNodeId !== nodeId) continue;
    tests.set(entry.handlerNodeIds?.[0] || entry.id, entry);
  }
  return [...tests.values()].sort((left, right) => (left.line ?? 1e9) - (right.line ?? 1e9));
}

/** Show declared tests as the starting point of a class, without a fictitious class execution. */
export function renderTestClassOverview(root, node, entries, { onTest = () => {}, onPath = () => {}, onSource = () => {} } = {}) {
  const tests = testsForContainer(node.id, entries, node.testContainerFacts);
  if (!tests.length) return false;
  const section = el('section', undefined, 'test-class-overview');
  section.append(el('h2', node.label || 'Тестовый класс'), el('h3', `Тесты класса · ${tests.length}`));
  section.append(el('p', 'Начните с теста: изучите именованные входы и подготовку, затем откройте схему действий. Число объявлений не равно числу запусков.', 'small muted'));
  if (node.testContainerFacts?.declaredTestCount > tests.length) section.append(el('p', `Список ограничен: ${tests.length} из ${node.testContainerFacts.declaredTestCount} объявлений.`, 'small muted'));
  for (const test of tests) {
    const card = el('article', undefined, 'test-data-card');
    const open = el('button', test.label || 'Тест', 'text-button');
    open.addEventListener('click', () => onTest(test)); card.append(open);
    const sources = test.dataSources || [];
    const names = sources.map(source => source.label || source.kind).filter(Boolean);
    card.append(el('small', names.length ? names.join(' · ') : 'Без объявленного источника параметров'));
    const countsKnown = sources.length === 1 && Number.isInteger(sources[0].declaredRowCount);
    if (countsKnown) card.append(el('small', `Объявленных строк: ${sources[0].declaredRowCount}`));
    else if (sources.length) card.append(el('small', 'Количество вариантов не определено'));
    if (test.declaredStatus === 'disabled') card.append(el('small', 'Отключён в исходнике'));
    const id = test.handlerNodeIds?.[0];
    if (id) {
      const path = el('button', 'Схема от теста', 'text-button');
      path.addEventListener('click', () => onPath(id, test)); card.append(path);
      const source = el('button', 'Исходник теста', 'text-button');
      source.addEventListener('click', () => onSource(id, test.line)); card.append(source);
    }
    section.append(card);
  }
  root.replaceChildren(section);
  return true;
}
