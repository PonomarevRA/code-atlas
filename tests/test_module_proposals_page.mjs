/** Generation fences remain authoritative while an old host context closes slowly. */
import assert from 'node:assert/strict';
import test from 'node:test';
import {ModuleProposalsPanel} from '../web/module-proposals-page.js';

/** Isolate request orchestration from rendering and use an explicitly delayed old close. */
function fixture() {
  let finishClose;
  const close = new Promise(resolve => { finishClose = resolve; });
  const panel = Object.create(ModuleProposalsPanel.prototype);
  const old = {projectId:'p', state:{contextId:'old'}};
  const element = {addEventListener(){}, removeEventListener(){}, removeAttribute(){}, setAttribute(){}};
  Object.assign(panel, {generation:0, disposed:false, reference:old, controller:null,
    root:element, form:element, buttons:[], positions:new Map(), pins:new Set(), frame:null,
    output:{...element, replaceChildren(){}, setAttribute(name,value){this[name]=value;}}, status:{textContent:''},
    scope:()=>({projectId:'p', body:{snapshotId:'s'}}),
    request:async path => path === '/api/projects' ? {projects:[{id:'p',snapshotId:'s'}]}
      : {projectId:'p',snapshotId:'s',stateRef:{contextId:'new'}},
    release:reference => reference === old ? close : Promise.resolve(),
    renderUpdate(){}, renderSearch(){},
  });
  return {panel, finishClose};
}

for (const action of ['reset', 'dispose', 'new-generation']) {
  test(`late old-context close cannot overwrite ${action}`, async () => {
    const {panel, finishClose} = fixture();
    const pending = panel.run('baseline');
    assert.equal(panel.output['aria-busy'], 'true');
    assert.equal(panel.root['aria-busy'], undefined);
    while (panel.reference?.state.contextId !== 'new') await new Promise(resolve => setImmediate(resolve));
    if (action === 'reset') panel.reset();
    if (action === 'dispose') panel.dispose();
    if (action === 'new-generation') { panel.generation++; panel.status.textContent='Новый запрос…'; }
    const expected = panel.status.textContent;
    finishClose(); await pending;
    assert.equal(panel.status.textContent, expected);
  });
}
