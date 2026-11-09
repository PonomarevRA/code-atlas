/** Контракт локальной истории: контекст карты не меняется, состояние возвращается копией. */
import assert from 'node:assert/strict';
import { ReaderHistory } from '../web/source-reader-history.js';

/** Создаёт независимое состояние файла с пользовательскими настройками просмотра. */
function viewState(nodeId, overrides = {}) {
  return {
    context: { projectId: 'project', snapshotId: 'snapshot', nodeId, label: `${nodeId}.cs`, line: 12 },
    mode: 'full', page: 2, query: 'Calculate', matchIndex: 3,
    scrollTop: 187, scrollLeft: 41,
    ...overrides,
  };
}

const history = new ReaderHistory();
history.reset('project:snapshot');
assert.equal(history.canBack, false);
assert.equal(history.canForward, false);
assert.equal(history.move(-1), null);
assert.equal(history.move(1), null);

const first = viewState('a');
const savedFirst = structuredClone(first);
history.push(first);
first.context.nodeId = 'mutated';
first.query = 'mutated';
history.push(viewState('b', { mode: 'context', page: 0, scrollTop: 23 }));
assert.equal(history.canBack, true);
assert.equal(history.canForward, false);
const restoredFirst = history.move(-1);
assert.deepEqual(restoredFirst, savedFirst, 'push must capture state instead of retaining caller-owned objects');
assert.equal(history.canBack, false);
assert.equal(history.canForward, true);
restoredFirst.context.nodeId = 'mutated-return';
restoredFirst.query = 'mutated-return';
const restoredSecond = history.move(1);
assert.equal(restoredSecond.context.nodeId, 'b');
assert.equal(restoredSecond.mode, 'context');
assert.equal(restoredSecond.scrollTop, 23);
assert.deepEqual(history.move(-1), savedFirst, 'move must return an independent copy');

// Back → новый файл удаляет только прежнюю ветку Forward.
history.push(viewState('c'));
assert.equal(history.canForward, false);
assert.equal(history.move(1), null);
assert.equal(history.move(-1).context.nodeId, 'a');
assert.equal(history.move(1).context.nodeId, 'c');

// Повторное открытие того же узла/строки обновляет настройки без лишнего шага Back.
const changed = viewState('c', { query: 'different', scrollTop: 501, scrollLeft: 90 });
changed.context.label = 'New display label';
history.push(changed);
assert.equal(history.move(-1).context.nodeId, 'a');
assert.deepEqual(history.move(1), changed);
const updated = viewState('c', { page: 7, query: 'updated', matchIndex: 4, scrollTop: 701 });
history.update(updated);
updated.context.label = 'mutated update';
assert.equal(history.move(-1).context.nodeId, 'a');
const afterUpdate = history.move(1);
assert.equal(afterUpdate.page, 7);
assert.equal(afterUpdate.query, 'updated');
assert.equal(afterUpdate.matchIndex, 4);
assert.equal(afterUpdate.scrollTop, 701);
assert.equal(afterUpdate.context.label, 'c.cs');

// Другой адрес строки — самостоятельный переход, даже в том же raw-узле.
const otherLine = viewState('c');
otherLine.context.line = 99;
history.push(otherLine);
assert.equal(history.move(-1).context.line, 12);
assert.equal(history.move(1).context.line, 99);
history.reset('project:snapshot');
assert.equal(history.canBack, true, 'reset of the same scope must preserve local history');
history.reset('project:new-snapshot');
assert.equal(history.canBack, false);
assert.equal(history.canForward, false);
assert.equal(history.move(-1), null);
history.push(viewState('new'));
history.reset('other-project:new-snapshot');
assert.equal(history.move(-1), null);
assert.equal(history.canBack, false);

// Повторный push после Back подтверждает текущую ветку, даже при том же адресе.
history.push(viewState('a'));
history.push(viewState('b'));
assert.equal(history.move(-1).context.nodeId, 'a');
history.push(viewState('a', { scrollTop: 800 }));
assert.equal(history.canForward, false);
assert.equal(history.move(1), null);
history.reset('bounded-history');

// Ограниченный журнал удерживает последние двадцать записей без скрытого роста.
for (let index = 0; index < 25; index++) history.push(viewState(`node-${index}`));
let oldest;
for (let index = 0; index < 19; index++) oldest = history.move(-1);
assert.equal(oldest.context.nodeId, 'node-5');
assert.equal(history.canBack, false);
assert.equal(history.move(-1), null);
for (let index = 0; index < 19; index++) assert.ok(history.move(1));
assert.equal(history.canForward, false);
assert.equal(history.move(1), null);

console.log('Source reader history contract PASS');
