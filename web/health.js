import { renderReadability } from './health-readability.js';
import { requestJson } from './bounded-request.js';
import { renderOrganization } from './health-organization.js';
import { HealthRefreshController } from './health-refresh.js';
const $ = id => document.getElementById(id);
const node = (tag, text, className) => { const element = document.createElement(tag); if (text != null) element.textContent = text; if (className) element.className = className; return element; };
const state = {project: null, baseline: null, plan: null, nodes: [], cursor: null, revision: 0, busy: false};
const score = value => value == null ? '?' : String(value);
const post = (suffix, body) => requestJson(`/api/projects/${encodeURIComponent(state.project)}/code-health${suffix}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}, {timeoutMs:60000});
const refresh = new HealthRefreshController($('health-refresh-status'), { onReady: scope => {
  if (state.project === scope.projectId && state.sourceMode === 'snapshot') return report(0);
} });
window.addEventListener('pagehide', () => refresh.stop());
/** Serialize UI commands and discard responses from a project the user has left. */
async function run(action) {
  if (state.busy || !state.project) return;
  state.busy = true; const revision = state.revision; $('health-status').className = ''; $('health-status').textContent = 'Читаю факты…';
  controls();
  try { const apply = await action(); if (revision === state.revision) { apply(); $('health-status').textContent = state.scopePartial ? 'Готово. Анализ неполный: ознакомьтесь с предупреждениями.' : 'Готово. Исходники и карта не изменены.'; } }
  catch (error) { if (revision === state.revision) { $('health-status').textContent = error.message; $('health-status').className = 'health-error'; } }
  finally { state.busy = false; controls(); }
}
/** Preserve baseline/plan actions until their required evidence exists. */
function controls() {
  for (const id of ['health-report','health-baseline','health-more']) $(id).disabled = state.busy || !state.project;
  for (const id of ['health-after','health-plan']) $(id).disabled = state.busy || !state.baseline;
}
/** Always show denominators and source dates beside averages. */
function summary(result) {
  const data = result.summary || {};
  $('health-summary').replaceChildren();
  for (const [label,value] of [['Среднее R · измеренные',score(data.readability?.meanScore)],['Покрытие R',`${data.readability?.assessedMethods ?? '?'} / ${data.readability?.totalMethods ?? '?'}`],['Среднее H',score(data.meanHealth)],['Организация O',score(data.organization?.score)],['Прозрачность T',score(data.transparency?.score)],['Измерено / методов',`${data.assessedCount ?? '?'} / ${data.methodCount ?? result.totalMethods ?? '?'}`],['Описание найдено / требуется',`${data.documentation?.documented ?? '?'} / ${data.documentation?.eligible ?? '?'}`],['Покрытие описаниями',data.documentation?.coveragePercent == null ? 'не установлено' : data.documentation.coveragePercent + '%'],['Неизвестные оценки',data.unknownCount ?? '?']]) {
    const card=node('article',label); card.append(node('strong',value)); $('health-summary').append(card);
  }
  $('health-evidence').textContent = `${result.sourceMode === 'working-tree' ? 'Текущие файлы' : 'Сохранённый снимок'} · карта: ${result.graphUpdatedAt || result.snapshotId || 'дата не установлена'} · измерение: ${result.measuredAt || 'дата не установлена'} · ${result.policyVersion || 'политика неизвестна'}${result.truncated ? ' · показана часть объявлений' : ''}${result.status && result.status !== 'complete' ? ' · анализ ' + result.status : ''}`;
  for (const warning of result.warnings || []) $('health-summary').append(node('p', `${warning.file}: ${warning.message}`, 'health-error'));
  renderOrganization($('health-organization'), data.organization);
}
/** Untrusted comments/findings use textContent, never HTML. */
function methods() {
  const filter=$('health-filter').value, table=node('table'), head=node('tr');
  for(const title of ['Метод','R / 100','H / 100','S / 100','D / 100','O / 100','T / 100','Вложенность','Замечания']) head.append(node('th',title));
  table.append(head);
  for(const item of state.nodes) {
    const health=item.health || {}, docs=item.documentationHealth || {};
    if(filter === 'review' && health.verdict !== 'REVIEW' && health.readability?.verdict !== 'REVIEW' || filter === 'unknown' && health.score != null || filter === 'undocumented' && docs.ownDescription !== false) continue;
    const row=node('tr'), name=node('td'), link=node('a',item.label || item.name || item.id);
    const parameters={repo:state.project,view:'architecture',level:'symbols',file:item.file || '',display:'flow'};
    if(item.rawNodeId || !item.id.startsWith('health-source:')) parameters.node=item.rawNodeId || item.id;
    link.href='/?' + new URLSearchParams(parameters);
    name.append(link,node('small',`${item.file || '?'}:${item.line || '?'} · ${health.status || 'не оценено'}`)); row.append(name);
    for(const [value,verdict] of [[health.readabilityScore,health.readability?.verdict],...[health.score,health.structuralScore,health.documentationScore,health.organizationScore,health.transparencyScore].map(value=>[value,health.verdict])]) { const cell=node('td'), badge=node('span',score(value),'health-score'); badge.dataset.level=value == null ? 'unknown' : value < 60 ? 'low' : value < 80 || verdict === 'REVIEW' ? 'warning' : 'good'; cell.append(badge); row.append(cell); }
    row.append(node('td',`${item.complexity?.maxNestingDepth ?? '?'} · средняя ${item.complexity?.averageNestingDepth ?? '?'}`));
    const cell=node('td'), findings=health.findings || [], details=node('details'); details.append(node('summary',`${findings.length} · ${health.verdict || 'UNKNOWN'}`));
    for(const finding of findings) details.append(node('p',finding.message));
    if(health.dependencies) details.append(node('p',`Зависимости: ${health.dependencies.callTargets} вызовов · ${health.dependencies.unknownTargets ?? '?'} неразрешённых · ${health.dependencies.writes ?? '?'} записей · ${health.dependencies.status}`));
    if(health.organization) details.append(node('p',`Организация: ${health.organization.file} · папка ${health.organization.folder} · ${health.organization.status}. Контекст выбранного участка; смысловая связность не измерена.`));
    renderReadability(details, health);
    details.append(node('p','Точность документации: смысловое ревью не выполнено.')); cell.append(details); row.append(cell); table.append(row);
  }
  $('health-methods').replaceChildren(table);
}
/** A displayed page keeps its own analysis scope; partial results never look complete. */
function show(result) { state.scopePartial=result.status === "partial" || result.status === "unavailable" || result.truncated || result.summary?.unknownCount > 0; state.nodes=result.nodes || []; state.sourceMode=result.sourceMode; state.cursor=result.nextCursor ?? null; $('health-more').hidden=state.cursor == null; summary(result); methods(); refresh.watch({projectId:state.project,snapshotId:result.snapshotId}); }
/** Explicit source files determine the measured scope, including newly added helpers. */
function fileQuery() { const files=$('health-files').value.split('\n').map(value=>value.trim()).filter(Boolean); if(!files.length) throw new Error('Укажите исходные файлы.'); return {sourceMode:'working-tree',files,locale:'ru'}; }
$('health-report').addEventListener('click', () => report(0));
$('health-more').addEventListener('click', () => report(state.cursor));
/** Report reads captured facts only; pagination never scans current project sources. */
function report(cursor) { return run(async () => { const result=await requestJson(`/api/projects/${encodeURIComponent(state.project)}/code-health/report?locale=ru&order=${$('health-order').value}&cursor=${cursor}`); return () => show(result); }); }
$('health-filter').addEventListener('change', methods);
$('health-basis').addEventListener('change',()=>{ $('health-draft').hidden=$('health-basis').value !== 'draft'; $('health-scenario').hidden=!$('health-draft').hidden; });
$('health-baseline').addEventListener('click',()=>run(async()=>{ const result=await post('',fileQuery()); return ()=>{ state.baseline=result; state.plan=null; $('health-forecast').replaceChildren(); $('health-comparison').replaceChildren(); show(result); $('health-method').replaceChildren(); for(const item of result.nodes) { const option=node('option',item.label); option.value=item.healthIdentity || item.id; $('health-method').append(option); } }; }));
/** Planning remains separate from actual code changes, even for point-valued estimates. */
$('health-plan').addEventListener('click',()=>run(async()=>{
  const body={baselineAssessmentId:state.baseline.assessmentId,intent:$('health-intent').value.trim(),locale:'ru'};
  if(!body.intent) throw new Error('Опишите цель изменений.');
  if($('health-basis').value === 'draft') body.drafts=[{file:$('draft-file').value.trim(),content:$('draft-content').value}];
  else body.projections=[{identity:$('health-method').value,reason:$('health-reason').value.trim(),metrics:{maxNestingDepth:{min:Number($('depth-min').value),max:Number($('depth-max').value)}},documentationScore:{min:Number($('docs-min').value),max:Number($('docs-max').value)}}];
  const result=await post('/plan',body);
  return ()=>{ state.plan=result; const host=$('health-forecast'); host.replaceChildren(node('h3','Прогноз · предполагаемое здоровье')); for(const item of result.projections || []) host.append(node('p',`${item.label || item.identity}: H ${item.scoreRange?.join('–') || '?'} · R ${score(item.readabilityScore)} · O ${score(item.organizationScore)} · T ${item.transparencyRange?.join('–') || '?'} · ${item.basis === 'draft-analysis' ? 'анализ черновика' : 'допущения'}`)); host.append(node('p','Диапазон не подтверждает поведение. Исходники не записаны.')); if(Object.values(result.baselineFreshness || {}).some(value=>value !== 'current')) host.append(node('p','Рабочие файлы изменились относительно исходной оценки: прогноз основан на сохранённом baseline.','health-error')); if(result.status === 'unavailable') host.append(node('p',result.reason)); };
}));
$('health-after').addEventListener('click',()=>run(async()=>{ const after=await post('',fileQuery()); const compared=await post('/compare',{beforeAssessmentId:state.baseline.assessmentId,afterAssessmentId:after.assessmentId,planId:state.plan?.planId || null,locale:'ru'}); return ()=>{ show(after); const host=$('health-comparison'); host.replaceChildren(node('h3',`После изменений · ${compared.verdict}`)); for(const change of compared.changes || []) host.append(node('p',`${after.nodes.find(item=>(item.healthIdentity || item.id) === (change.afterIdentity || change.identity))?.label || change.identity}: ${score(change.before)} → ${score(change.after)} · ${change.kind} · R ${score(change.readability?.before)} → ${score(change.readability?.after)}`)); for(const item of compared.planComparison || []) host.append(node('p',`Прогноз ${item.expected?.join('–') || '?'} · фактически ${score(item.actual)} · ${item.withinRange == null ? 'сравнение неизвестно' : item.withinRange ? 'в диапазоне' : 'вне диапазона'}`)); for(const item of compared.findings || []) host.append(node('p',item.message)); if(compared.reason) host.append(node('p',compared.reason)); host.append(node('p','Поведение не проверено этой оценкой.')); }; }));
$('health-project').addEventListener('change',()=>{ refresh.watch(null); state.project=$('health-project').value; state.revision++; state.baseline=null; state.plan=null; state.nodes=[]; state.cursor=null; $('health-method').replaceChildren(); $('health-summary').replaceChildren(); $('health-organization').replaceChildren(); $('health-evidence').textContent=''; $('health-forecast').replaceChildren(); $('health-comparison').replaceChildren(); $('health-more').hidden=true; methods(); controls(); report(0); });
try { const data=await requestJson('/api/projects'), params=new URLSearchParams(location.search); for(const project of data.projects || []) { const option=node('option',project.name); option.value=project.id; $('health-project').append(option); } const requested=params.get('repo'); if([...$('health-project').options].some(item=>item.value === requested)) $('health-project').value=requested; state.project=$('health-project').value; $('health-files').value=params.get('file') || ''; $('draft-file').value=params.get('file') || ''; controls(); await report(0); } catch(error) { $('health-status').textContent=error.message; }
