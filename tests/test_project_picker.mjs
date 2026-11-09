/** Выбор проекта сохраняет живую карту и обычные модифицированные переходы браузера. */
import assert from 'node:assert/strict';
import {bindProjectPicker} from '../web/workspace-layout.js';
let handler, revealed = 0, focused = 0, scrolled = 0;
const link = {addEventListener: (_, fn) => {handler = fn;}, removeEventListener: (_, fn) => {if (handler === fn) handler = null;}};
const release = bindProjectPicker(link, () => revealed++, {focus: () => focused++, scrollIntoView: () => scrolled++});
for (const modifier of ['ctrlKey','metaKey','shiftKey','altKey']) handler({[modifier]:true, preventDefault: () => assert.fail('modified navigation intercepted')});
let prevented = false;
handler({button:0, preventDefault: () => {prevented = true;}});
assert.equal(prevented, true);
assert.deepEqual([revealed,focused,scrolled], [1,1,1]);
release(); assert.equal(handler,null);
console.log('Project picker preserves map, focus, modified links and disposal: PASS');
