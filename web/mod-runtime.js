/** Optional, explicitly activated mods decorate presentation; the host owns facts and navigation. */
import { requestJson } from './bounded-request.js';
let enabled = [], decorators = [], cleanups = [], revision = 0;
export const modErrors = [];
/** Keep hook input small, immutable and detached from snapshot/source objects. */
function facts(node) {
  return Object.freeze({id:node.definitionId || node.readTargetId || node.sourceOwnerId || node.id, visualId:node.id, label:node.label, kind:node.kind,
    file:node.file, line:node.line, comment:node.comment || node.condition || '',
    health:Object.freeze({...node.health && {score:node.health.score, documentationScore:node.health.documentationScore,level:node.health.level}}),
    complexity:Object.freeze({...node.complexity && {score:node.complexity.score,maxNestingDepth:node.complexity.maxNestingDepth}})});
}
/** Reloading the dedicated management page remains possible even if an executable mod breaks the map. */
export async function loadMods({fetcher, importer=url=>import(url), safe=false}={}) {
  const current=++revision;
  for(const cleanup of cleanups) {try{cleanup();}catch(error){modErrors.push(String(error));}}
  cleanups=[]; enabled=[]; decorators=[]; modErrors.length=0;
  if(safe) return [];
  const result=await requestJson('/api/mods',{}, {fetcher,timeoutMs:5000});
  if(current!==revision) return [];
  const pending=result.mods.filter(item=>item.enabled).sort((a,b)=>a.id.localeCompare(b.id));
  const active=[];
  while(pending.length) {
    const index=pending.findIndex(item=>(item.manifest.requires || []).every(id=>active.some(dependency=>dependency.id===id)));
    if(index<0) {modErrors.push('Unresolved mod dependencies');break;}
    active.push(...pending.splice(index,1));
  }
  for(const item of active) {
    try {
      if((item.manifest.requires || []).some(id=>!enabled.some(dependency=>dependency.id===id))) throw new Error('Dependency failed to activate');
      const staged=[], disposal=[];
      if(item.manifest.frontend) {
        const url=`/api/mods/${encodeURIComponent(item.id)}/assets/${item.fingerprint}/${item.manifest.frontend.split('/').map(encodeURIComponent).join('/')}`;
        const module=await importer(url);
        if(typeof module.activate!=='function') throw new Error('Mod must export activate(api)');
        const api=Object.freeze({apiVersion:1, modId:item.id,
          registerCardDecorator(callback) {if(typeof callback!=='function' || staged.length>=8) throw new Error('Invalid/too many card hooks'); staged.push({id:item.id,callback});},
          onDispose(callback) {if(typeof callback!=='function' || disposal.length>=8) throw new Error('Invalid/too many disposal hooks'); disposal.push(callback);}});
        await module.activate(api);
      }
      if(current!==revision) {for(const cleanup of disposal) cleanup(); return [];}
      enabled.push(item); decorators.push(...staged); cleanups.push(...disposal);
    } catch(error) {modErrors.push(`${item.id}: ${String(error).slice(0,300)}`);}
  }
  return enabled;
}
/** All Flow variants use the same appearance tokens without changing their measured geometry. */
export function applyModTheme(group) {
  for(const item of enabled) {
    const theme=item.manifest.theme;
    if(!theme) continue;
    const bodies=[...group.querySelectorAll('.flow-card-body'),...group.querySelectorAll('.topology-card-body'),...group.querySelectorAll('.mod-card-body')];
    for(const body of bodies) {
      if(theme.fill) body.setAttribute('data-mod-fill', 'true');
      if(theme.stroke) body.setAttribute('data-mod-stroke', 'true');
      for(const [token,attribute] of [['fill','fill'],['stroke','stroke'],['radius','rx'],['borderWidth','stroke-width']])
        if(theme[token]!=null) body.setAttribute(attribute,theme[token]);
    }
    for(const header of [...group.querySelectorAll('.flow-card-header'),...group.querySelectorAll('.topology-card-role-band'),...group.querySelectorAll('.mod-card-header')]) if(theme.headerFill) {
      header.setAttribute('fill',theme.headerFill); header.setAttribute('data-mod-fill', 'true');
    }
    for(const text of [...group.querySelectorAll('.flow-card-name'),...group.querySelectorAll('.topology-card-title')]) {
      if(theme.text) {
        text.setAttribute('fill',theme.text); text.setAttribute('data-mod-fill', 'true');
        for (const span of text.querySelectorAll('tspan')) {
          span.setAttribute('fill',theme.text); span.setAttribute('data-mod-fill', 'true');
        }
      }
      if(theme.fontSize) text.setAttribute('font-size',theme.fontSize);
      const rules=[];if(theme.text)rules.push(`fill:${theme.text}`);if(theme.fontSize)rules.push(`font-size:${theme.fontSize}px`);if(rules.length)text.setAttribute('style',rules.join(';'));
    }
  }
}
/** Delegate navigation to existing host actions; mods never invent node IDs or alter call edges. */
function actions(view,node,group,ready=()=>true) {
  const snapshot=facts(node);
  const perform=action=>{
    if(!ready()) throw new Error('Navigation actions are available only from event handlers');
    const selector=action==='expand'?'.path-card-expand, .node-action-inline':action==='enter'?'.node-action-enter':null;
    const control=selector && group.querySelector(selector);
    if(control?.dispatchEvent) {control.dispatchEvent(new MouseEvent('click',{bubbles:true}));return;}
    if(action==='source') (view.onPathSource || view.onInlineSource)?.(snapshot.id,view.getSourceNode?.(snapshot.id)?.line || node.definitionLine || node.line);
    else if(action==='select') view.onSelect?.(snapshot.id);
    else if(action==='isolate') view.modIsolate?.(snapshot.id);
    else if(action==='expand') view.onToggleBlock?.(node.id);
    else if(action==='enter') view.onEnterBlock?.(node.id);
  };
  return Object.freeze(Object.fromEntries(['source','select','isolate','expand','enter'].map(name=>[name,()=>perform(name)])));
}
/** Hook overlays are committed only after successful rendering; a failed hook leaves the host card intact. */
export function decorateModCard(view,group,node,bounds) {
  if(!enabled.length || !node || (node.transition && !node.compactRow)) return;
  applyModTheme(group);
  const snapshot=facts(node), navigation=actions(view,node,group);
  for(const decorator of decorators) {
    const overlay=document.createElementNS('http://www.w3.org/2000/svg','g');
    overlay.setAttribute('class','atlas-mod-overlay');overlay.setAttribute('data-mod-id',decorator.id);
    let ready=false;
    try {
      const result=decorator.callback(Object.freeze({node:snapshot,overlay,bounds:Object.freeze({...bounds}),actions:actions(view,node,group,()=>ready)}));
      if(result?.then) {Promise.resolve(result).catch(()=>{});throw new Error('Card decorators must be synchronous');}
      ready=true;group.append(overlay);
    }
    catch(error) {modErrors.push(`${decorator.id}: ${String(error).slice(0,300)}`); if(modErrors.length>20) modErrors.shift();}
  }
  const behavior=enabled.map(item=>item.manifest.behavior?.doubleClick).filter(Boolean).at(-1);
  if(behavior) group.addEventListener('dblclick',event=>{
    if(event.target.closest?.('[data-node-action], .path-card-source, .path-card-expand')) return;
    event.preventDefault();event.stopPropagation();navigation[behavior]?.();
  });
}
