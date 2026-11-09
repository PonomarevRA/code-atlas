/** Project/snapshot-bound architecture workflow. The browser renders facts; it never infers dependencies. */
import {requestJson} from './bounded-request.js';
import {DialogFocus} from './dialog-focus.js';
import {renderProofMap} from './architecture-proof.js';

export const EXAMPLE_RULES = {
  version:'atlas-architecture-v1', pythonRoot:'.',
  exclude:['outputs','evidence','sites','.atlas','resources/language-references','resources/reference-search'],
  layers:[{id:'language',paths:['atlas/flow_languages/*.py']},{id:'http',paths:['atlas/server.py','atlas/*_api.py']}],
  rules:[{id:'language-boundary',kind:'forbidden',layers:['language'],target:['http'],relations:['python-import']}],
};
const STATUS = {passed:'passed · Правило соблюдено',violated:'violated · Есть нарушение',unknown:'unknown · Недостаточно данных'};

/** Own one request generation and one source dialog; project changes invalidate all async delivery. */
export class ArchitecturePage {
  constructor(document, {request=requestJson, storage=globalThis.localStorage} = {}) {
    this.document=document; this.request=request; this.storage=storage;
    this.projects=[]; this.drafts=new Map(); this.project=null; this.run=null; this.proof=null; this.revision=0;
    this.$=id=>document.getElementById('architecture-'+id);
    this.focus=new DialogFocus(this.$('source'), {onClose:()=>this.closeSource(),initialFocus:()=>this.$('source-close')});
    this.$('source-close').addEventListener('click',()=>this.closeSource());
    this.$('source').addEventListener('cancel',event=>{event.preventDefault();this.closeSource();});
    this.$('project').addEventListener('change',()=>this.selectProject());
    this.$('run').addEventListener('click',()=>this.execute());
    this.$('reload').addEventListener('click',()=>this.loadProjects());
    this.$('more-rules').addEventListener('click',()=>this.loadCollection('rules',this.rulesCursor));
    this.$('more-findings').addEventListener('click',()=>this.loadCollection('findings',this.findingsCursor));
    this.$('more-edges').addEventListener('click',()=>this.openProof(this.proof.findingId,this.proof.nextOffset));
    this.$('rules').addEventListener('input',()=>{
      this.saveRules();
      if(this.run) this.$('status').textContent='Правила изменены. Показан предыдущий запуск; проверьте заново.';
    });
  }

  /** Literal text prevents names/rules/source from becoming executable markup. */
  node(tag,text) { const node=this.document.createElement(tag); if(text!=null) node.textContent=text; return node; }
  base() { return `/api/projects/${encodeURIComponent(this.project.id)}/architecture-checks`; }
  /** Session drafts survive unavailable browser storage;32 bounded project drafts are retained. */
  saveRules() {
    if(!this.project) return;
    const draft=this.$('rules').value;
    if(draft.length>65536) return;
    this.drafts.delete(this.project.id); this.drafts.set(this.project.id,draft);
    if(this.drafts.size>32) this.drafts.delete(this.drafts.keys().next().value);
    try {this.storage?.setItem('atlas-architecture:'+this.project.id,draft);} catch {}
  }

  /** Cancel the previous generation and restore controls even after failures. */
  async perform(action) {
    this.controller?.abort(); const controller=new AbortController(); this.controller=controller;
    const revision=++this.revision;
    const current=()=>revision===this.revision;
    this.busy(true); this.$('status').textContent='Выполняется…';
    const request=(url,options={})=>this.request(url,{...options,signal:controller.signal},{timeoutMs:60000});
    try { await action(request,current); }
    catch(error) { if(current() && error.name!=='AbortError') this.$('status').textContent=error instanceof SyntaxError ? 'Проверьте правила: некорректный JSON.' : error.message; }
    finally { if(current()) this.busy(false); }
  }

  busy(value) {
    for(const button of this.document.querySelectorAll('.architecture-page button')) button.disabled=value;
    this.$('rules').disabled=value;
    this.$('run').disabled=value || !this.project?.snapshotId;
    this.$('reload').disabled=value;
  }

  /** A new project/snapshot cannot inherit a result or a source-dialog response from the old one. */
  selectProject() {
    this.saveRules(); this.controller?.abort(); this.revision++; this.closeSource();
    this.project=this.projects.find(item=>item.id===this.$('project').value) || null;
    this.run=null; this.proof=null;
    for(const name of ['results','findings','edges','map']) this.$(name).replaceChildren();
    for(const name of ['more-rules','more-findings','more-edges']) this.$(name).hidden=true;
    this.$('context').textContent=''; this.$('summary').textContent='Запустите проверку, чтобы получить результат.';
    this.$('proof-status').textContent='Выберите нарушение.';
    this.$('snapshot').textContent=this.project?.snapshotId ? `Снимок: ${this.project.snapshotId}` : 'У проекта ещё нет готовой карты.';
    let saved=this.drafts.get(this.project?.id);
    if(saved===undefined) {try {saved=this.storage?.getItem('atlas-architecture:'+this.project?.id);} catch {}}
    this.$('rules').value=saved ?? JSON.stringify(EXAMPLE_RULES,null,2);
    this.$('status').textContent=this.project ? 'Готово к проверке.' : 'Добавьте проект на странице карты.';
    this.busy(false);
  }

  async loadProjects() {
    await this.perform(async(request,current)=>{
      const data=await request('/api/projects'); if(!current()) return;
      const previous=this.project?.id || new URLSearchParams(globalThis.location?.search || '').get('repo');
      this.projects=data.projects || []; this.$('project').replaceChildren();
      for(const project of this.projects) {const option=this.node('option',project.name);option.value=project.id;this.$('project').append(option);}
      if(this.projects.some(item=>item.id===previous)) this.$('project').value=previous;
      this.selectProject();
    });
  }

  /** Preserve the chosen rules and identifiers; refresh projects explicitly after a409. */
  async execute() {
    this.closeSource(); this.saveRules();
    await this.perform(async(request,current)=>{
      const rules=JSON.parse(this.$('rules').value);
      const result=await request(this.base(),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({snapshotId:this.project.snapshotId,rules})});
      if(!current()) return;
      this.run=result; this.proof=null;
      for(const name of ['results','findings','edges','map']) this.$(name).replaceChildren();
      this.$('more-edges').hidden=true; this.$('proof-status').textContent='Выберите нарушение.';
      this.$('summary').textContent=`${STATUS[result.status]} · passed ${result.counts.passed}, violated ${result.counts.violated}, unknown ${result.counts.unknown}`;
      this.$('context').textContent=`Снимок ${result.snapshotId} · исходники ${result.sourceFingerprint} · правила ${result.rulesFingerprint}`;
      this.renderCollection(result,'rules');
      this.findingsCursor=0; this.$('more-findings').hidden=false;
      const findings=await request(`${this.base()}/${result.runId}?collection=findings&limit=20`);
      if(!current()) return;
      this.renderCollection(findings,'findings'); this.$('status').textContent='Проверка завершена. Результат — сохранённое наблюдение исходников.';
    });
  }

  async loadCollection(collection,offset) {
    await this.perform(async(request,current)=>{
      const result=await request(`${this.base()}/${this.run.runId}?collection=${collection}&offset=${offset}&limit=20`);
      if(current()) {this.renderCollection(result,collection);this.$('status').textContent='Страница загружена.';}
    });
  }

  /** Paging keeps the exact run; delivery and analysis truncation stay visible independently. */
  renderCollection(page,collection) {
    const host=this.$(collection==='rules'?'results':'findings');
    for(const item of page.items) {
      const row=this.node('li');
      if(collection==='rules') {
        row.dataset.status=item.status; row.append(this.node('strong',`${item.ruleId}: ${STATUS[item.status]}`));
        row.append(this.node('p',`Файлов: ${item.selectedFiles} · нарушений: ${item.findingCount} · полнота: ${item.coverage}`));
        for(const reason of item.reasons) row.append(this.node('p',reason));
        if(item.reasonsTruncated) row.append(this.node('p','Список причин сокращён. Сузьте область для подробного объяснения.'));
      } else {
        const button=this.node('button',`${item.ruleId} · ${item.kind==='cycles'?'Цикл':'Запрещённая связь'} · связей ${item.edgeCount}`);
        button.type='button'; button.addEventListener('click',()=>this.openProof(item.id,0)); row.append(button);
      }
      host.append(row);
    }
    if(!page.total && !page.offset) host.append(this.node('li',collection==='findings'?'Подтверждённых нарушений нет. Проверьте статусы unknown выше.':'Нет правил.'));
    const button=this.$(collection==='rules'?'more-rules':'more-findings'); button.hidden=page.nextOffset===null;
    if(collection==='rules') this.rulesCursor=page.nextOffset; else this.findingsCursor=page.nextOffset;
    if(page.findingsTruncated || page.deliveryTruncated) host.append(this.node('li',page.findingsTruncated?'Список нарушений ограничен. Сузьте область и повторите проверку.':'Страница сокращена по объёму; продолжите кнопкой ниже.'));
  }

  async openProof(findingId,offset) {
    await this.perform(async(request,current)=>{
      const proof=await request(`${this.base()}/${this.run.runId}/findings/${findingId}?offset=${offset}&limit=20`);
      if(!current()) return;
      this.proof=proof; this.$('edges').replaceChildren(); renderProofMap(this.$('map'),proof.items);
      this.$('proof-status').textContent=`${proof.kind==='cycles'?'Цикл':'Запрещённая связь'}: связи ${proof.offset+1}–${proof.offset+proof.items.length} из ${proof.total}. ${proof.offset || proof.nextOffset!==null?'Показана часть пути.':'Показан полный путь.'}`;
      for(let index=0;index<proof.items.length;index++) {
        const edge=proof.items[index], row=this.node('li');
        row.append(this.node('p',`${edge.source} → ${edge.target} · ${edge.relation} · ${edge.confidence}`));
        const button=this.node('button',`Открыть ${edge.file}:${edge.line || 1}`); button.type='button';
        button.addEventListener('click',()=>this.openSource(index,button)); row.append(button); this.$('edges').append(row);
      }
      this.$('more-edges').hidden=proof.nextOffset===null; this.$('status').textContent='Доказательство загружено. Выберите исходник связи.';
      this.$('proof-heading')?.focus?.();
    });
  }

  async openSource(index,opener) {
    const proof=this.proof;
    await this.perform(async(request,current)=>{
      const source=await request(`${this.base()}/${this.run.runId}/findings/${proof.findingId}/source`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({edgeIndex:proof.offset+index})});
      if(!current()) return;
      renderProofMap(this.$('map'),proof.items,index);
      for(const [position,button] of [...this.$('edges').querySelectorAll('button')].entries()) button.setAttribute('aria-pressed',String(position===index));
      this.$('source-context').textContent=`${source.file}:${source.line} · строки с ${source.startLine} · снимок ${source.snapshotId} · SHA256 ${source.sourceByteHash}${source.truncated?' · фрагмент':''}`;
      this.$('source-text').textContent=source.text;
      this.$('source').showModal(); this.focus.open(); if(opener) this.focus.previous=opener; this.$('status').textContent='Исходник совпадает с доказательством.';
    });
  }
  closeSource() { if(this.$('source').open) this.$('source').close(); this.focus.close(); }
  dispose() { this.saveRules();this.controller?.abort();this.revision++;this.closeSource(); }
}

if(globalThis.document?.getElementById('architecture-project')) {
  const page=new ArchitecturePage(document); page.loadProjects();
  window.addEventListener('pagehide',()=>page.dispose(),{once:true});
}
