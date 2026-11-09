/** Provider language never chooses card layout; optional facts use a common text fallback. */
import assert from 'node:assert/strict';
import { sequencePresentation } from '../web/sequence-presentation.js';
const node = {kind:'operation',label:'value = read()',sourceExpression:'value = read()'};
assert.deepEqual(sequencePresentation({...node,language:'python'}), sequencePresentation({...node,language:'csharp'}));
const extension = sequencePresentation({...node,extensions:[{id:'rust.macro',version:1,label:'Макрос не раскрыт'}]});
assert.equal(extension.context, 'Макрос не раскрыт');
assert.equal(extension.contextIsCode, false);
console.log('Provider-independent card presentation PASS');
