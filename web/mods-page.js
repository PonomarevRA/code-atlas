/** Management stays independent from user scripts, allowing a broken mod to be disabled. */
import {requestJson} from './bounded-request.js';
import {createSemanticEngineSettings, isSemanticOnlyMod} from './semantic-engine-settings.js';
const $=id=>document.getElementById(id);
const element=(tag,text)=>{const node=document.createElement(tag);if(text!=null)node.textContent=text;return node;};
let busy=false;
/** Local application setting updates are explicit; errors keep previous working settings. */
async function action(task) {
  if(busy)return;busy=true;for(const button of document.querySelectorAll('button'))button.disabled=true;
  try{await task();$('mods-status').textContent='Готово. Открытые карты перечитают настройки модов.';}
  catch(error){$('mods-status').textContent=error.message;}
  finally{busy=false;for(const button of document.querySelectorAll('button'))if(button.dataset.invalid!=='true')button.disabled=false;}
}
const post=(path,body={},options={})=>requestJson(path,{...options,method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
const engineSettings = createSemanticEngineSettings({
  request: (url, options) => requestJson(url, options, {timeoutMs: 6000}),
  post: (url, body, options) => requestJson(url, {...options, method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}, {timeoutMs: 6000}),
  notify,
});
window.addEventListener('pagehide', () => engineSettings.dispose());
/** Existing map tabs preserve their semantic URLs while reloading changed presentation. */
function notify(){try{localStorage.setItem('atlas-mods-revision',String(Date.now()));}catch{ /* Reload remains available manually. */ }}
/** Render manifest prose as text and make activation version-specific. */
async function refresh(){
  const catalog=await requestJson('/api/mods');await engineSettings.load();$('mods-directory').textContent=catalog.directory;$('mods-list').replaceChildren();
  $('mods-auto').textContent = catalog.autoAnalysisEnabled ? 'Автоанализ по файлам: включён' : 'Автоанализ по файлам: выключен';
  $('mods-auto').dataset.enabled = String(catalog.autoAnalysisEnabled);
  for(const mod of catalog.mods){
    const card=element('article');card.append(element('h3',mod.manifest?.name || mod.id),element('p',`${mod.manifest?.version || '?'} · API ${mod.manifest?.apiVersion || '?'} · ${mod.status}`),element('p',mod.error || mod.manifest?.description || ''));
    if (mod.manifest?.requires?.length) card.append(element('p', `Сначала включите зависимости: ${mod.manifest.requires.join(', ')}`));
    if (isSemanticOnlyMod(mod)) card.append(element('p', 'Доступные локальные движки уточняют карту по запросу. Базовую карту перестраивать не нужно.'));
    else if (mod.manifest?.analysis) card.append(element('p', 'Правила анализа применятся после обновления карты. Сохранённые снимки сохраняют прежние факты.'));
    if (mod.autoAnalysisEligible) {
      const mode = {auto: 'Авто по файлам', on: 'Включён вручную', off: 'Выключен', changed: 'Изменена закреплённая версия'}[mod.analysisMode] || mod.analysisMode;
      const selection = isSemanticOnlyMod(mod) ? 'Выбор выполняется при работе с картой.' : 'Выбор выполняется при обновлении карты конкретного проекта.';
      card.append(element('p', `Режим анализа: ${mode}. ${selection}`));
    }
    const button=element('button',mod.enabled?'Выключить':mod.status==='changed'?'Включить новую версию':mod.autoAnalysisEligible?'Включить вручную':'Включить');
    button.disabled=!mod.manifest;if(!mod.manifest)button.dataset.invalid='true';
    button.addEventListener('click',()=>action(async()=>{await post(`/api/mods/${encodeURIComponent(mod.id)}/activation`,{fingerprint:mod.fingerprint,enabled:!mod.enabled});notify();await refresh();}));
    card.append(button);
    const wiki = mod.manifest?.wiki;
    if (wiki?.section && wiki.pages?.length) {
      const guide = element('a', 'Открыть wiki мода');
      const query = new URLSearchParams({page: wiki.section, mod: mod.id, modPage: wiki.pages[0].id});
      const repo = new URLSearchParams(location.search).get('repo');
      if (repo) query.set('repo', repo);
      guide.href = '/wiki.html?' + query;
      card.append(element('p'), guide);
    }
    if (mod.autoAnalysisEligible && mod.analysisMode !== 'auto') {
      const automatic = element('button', 'Авто по файлам');
      automatic.addEventListener('click', () => action(async () => {
        await post(`/api/mods/${encodeURIComponent(mod.id)}/activation`, {fingerprint: mod.fingerprint, enabled: null});
        notify(); await refresh();
      }));
      card.append(automatic);
    } else if (mod.autoAnalysisEligible) {
      const disable = element('button', 'Не применять автоматически');
      disable.addEventListener('click', () => action(async () => {
        await post(`/api/mods/${encodeURIComponent(mod.id)}/activation`, {fingerprint: mod.fingerprint, enabled: false});
        notify(); await refresh();
      }));
      card.append(disable);
    }
    if (mod.enabled) for (const workflow of mod.manifest.workflows || []) {
      const link=element('a',workflow.label);link.href='/research.html?'+new URLSearchParams({mod:mod.id,workflow:workflow.id});
      card.append(element('p'),link);
    }
    const settingsPanel = engineSettings.render(mod);
    if (settingsPanel) card.append(settingsPanel);
    $('mods-list').append(card);
  }
}
/** Display the actual selected runtime, including a failure instead of silently falling back. */
async function runtime(){const data=await requestJson('/api/integrations/graphify');$('runtime-info').textContent=`${data.mode==='external'?'Отдельный':'Встроенный'} · Graphify ${data.version || 'недоступен'} · ${data.adapterVersion}${data.error?' · '+data.error:''}`;$('runtime-path').value=data.python || '';}
$('mods-refresh').addEventListener('click',()=>action(refresh));
$('mods-open').addEventListener('click',()=>action(()=>post('/api/mods/open-directory')));
$('mods-disable').addEventListener('click',()=>action(async()=>{await post('/api/mods/disable-all');notify();await refresh();}));
$('mods-auto').addEventListener('click', () => action(async () => {
  await post('/api/mods/analysis-auto', {enabled: $('mods-auto').dataset.enabled !== 'true'});
  notify(); await refresh();
}));
$('runtime-select').addEventListener('click',()=>action(async()=>{await post('/api/integrations/graphify/runtime',{python:$('runtime-path').value.trim()});await runtime();}));
$('runtime-embedded').addEventListener('click',()=>action(async()=>{await post('/api/integrations/graphify/runtime',{python:null});await runtime();}));
await action(async()=>{await refresh();await runtime();});
