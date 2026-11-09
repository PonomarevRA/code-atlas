import {el} from './dom.js';
import {analysisDiagnosticsState,analysisIssueCard} from './analysis-diagnostics.js';
import {requestJson} from './bounded-request.js';

export const OVERVIEW_LOCALE = Object.freeze({locale:'ru', availableLocales:['ru'], fallback:'ru',contentOwner:'Atlas host chrome; captured project text is preserved without translation'});
const TASKS = [
  {id:'understand', title:'Понять проект', description:'Начните с сохранённой документации и основных областей.'},
  {id:'entrypoints', title:'Найти обработчик или запуск', description:'Откройте существующий каталог подтверждённых точек входа.'},
  {id:'impact', title:'Оценить влияние изменения', description:'Выберите файл или символ, затем изучите его входящие и исходящие связи. Это статический контекст, а не доказательство всех последствий.'},
];
const STATES = {
  ready:'Сохранённый снимок доступен.', refreshing_with_snapshot:'Карта обновляется. Показан последний доступный снимок.',
  build_failed_with_snapshot:'Обновление не удалось. Последний доступный снимок сохранён.',
  build_failed_no_snapshot:'Построение завершилось ошибкой. Доступного снимка нет.',
  building_no_snapshot:'Карта строится. Готового снимка пока нет.', missing_snapshot:'Сохранённый снимок недоступен.',
  not_built:'Проект добавлен. Карта ещё не построена.',
};
const ROLES = {source:'Исходный код',tests:'Тесты',examples:'Примеры',vendor:'Зависимости',generated:'Созданные файлы',unknown:'Назначение не определено'};

/**
 * Проверяет принадлежность обзора выбранному проекту и снимку, не интерпретируя текст как код.
 * @param {object} data Публичный ответ overview API.
 * @param {object} scope Выбранные projectId и nullable snapshotId; null не разрешает смешивать проекты.
 * @returns {object} Исходный DTO после проверки. Некорректный или чужой ответ вызывает Error.
 */
export function validateOverview(data, scope) {
  if (data?.projectId !== scope.projectId || (data.snapshotId||null)!==(scope.snapshotId||null)) {
    const error=new Error('Обзор не соответствует выбранному проекту или снимку. Обновите его.');error.status=409;throw error;
  }
  if (data.apiVersion !== 1 || !Object.hasOwn(STATES,data.state) || !Array.isArray(data.areas) || !Array.isArray(data.suggestedStarts)
      || typeof data.snapshot?.available !== 'boolean' || (data.snapshot.available&&!data.snapshotId)) throw new Error('Сервер вернул неполный обзор. Повторите чтение.');
  return data;
}

/**
 * Составляет подпись текущей области из реально выбранного scope, без вывода runtime-состояния из имён.
 * @param {object} context Проект, snapshotId, mode, file/projectLabel и выбранная подпись узла.
 * @returns {string} Краткий видимый путь; отсутствие снимка обозначено явно.
 */
export function projectScopeLabel({project,snapshotId,mode,file,projectLabel,nodeLabel} = {}) {
  if (!project) return 'Проект не выбран';
  const parts=[project.name || project.id,mode==='system'?'Архитектура':mode==='groups'?'Группы':'Код'];
  if (projectLabel) parts.push(projectLabel);
  if (file) parts.push(file);
  if (nodeLabel) parts.push(nodeLabel);
  parts.push(snapshotId ? 'снимок '+snapshotId.slice(0,8) : 'нет доступного снимка');
  return parts.join(' › ');
}

/**
 * Создаёт компактный обзор с первичными задачами и закрытыми native details для доказательств/проблем.
 * @param {HTMLElement} root Единственная панель обзора с native details для подробностей; данные выводятся буквальным текстом.
 * @param {object} options request — same-origin транспорт; onAction получает разрешённое действие и текущий DTO;
 * onState получает проверенный DTO, а onLayout сообщает об изменении высоты. Запросы не выполняют rebuild.
 * @returns {object} sync/refresh/setOpen/isOpen/dispose; dispose отменяет чтение и запрещает поздний render.
 */
export function createProjectOverview(root,{request=requestJson,onAction=()=>{},onState=()=>{},onLayout=()=>{}}={}) {
  const details=root.querySelector('[data-overview-details]'),summary=details.querySelector('summary');
  const body=root.querySelector('[data-overview-body]'),notice=root.querySelector('[data-overview-status]');
  const tasks=root.querySelector('[data-overview-tasks]'),compactPurpose=root.querySelector('[data-overview-purpose]');
  const issues=root.querySelector('[data-overview-issues]'),issueList=root.querySelector('[data-overview-issue-list]');
  let scope=null,key='',data=null,error='',loading=false,controller=null,generation=0,disposed=false,signature='',stale=false;
  let panelOpen=false;
  details.addEventListener('toggle',onLayout);issues.addEventListener('toggle',onLayout);

  /** Сообщает только заранее известное действие и текущий immutable-scope DTO; источник не управляет командами. */
  function action(id,payload){if(!disposed&&data)onAction(id,payload,data);}

  /** Создаёт обычную кнопку с видимой подписью и точным обработчиком, сохраняя фокус при обновлении. */
  function button(text,id,callback){const node=el('button',text);node.type='button';node.dataset.overviewAction=id;node.addEventListener('click',callback);return node;}

  /** Показывает ограниченный список путей доказательств; Markdown/HTML и внешние ссылки не выполняются. */
  function evidence(items){
    const list=el('ul',undefined,'overview-evidence');
    for(const source of (Array.isArray(items)?items:[]).slice(0,6))if(typeof source.path==='string')list.append(el('li',source.path));
    return list;
  }

  /**
   * Обновляет компактное назначение/действия и раскрываемые доказательства; сохраняет фокус и отдельный статус.
   * Старые данные удерживаются только при чтении того же project/snapshot; чужое содержимое очищается в sync.
   */
  function render(){
    if(disposed)return;
    root.hidden=!scope||!panelOpen;
    if(!scope){onLayout();return;}
    const active=body.ownerDocument.activeElement,focusKey=root.contains(active)?active.dataset.overviewAction:null;
    summary.textContent='Области и источники';
    const diagnostics=analysisDiagnosticsState(data?.diagnostics);
    notice.textContent=loading?(data?'Обновляем обзор…':'Читаем обзор проекта…'):error;
    notice.hidden=!notice.textContent;
    notice.classList.toggle('error-text',Boolean(error));
    body.setAttribute('aria-busy',String(loading));body.replaceChildren();tasks.replaceChildren();issueList.replaceChildren();
    issues.hidden=true;
    compactPurpose.textContent=data?.purpose?.text|| (loading&&!data?'Читаем назначение проекта…':'Назначение не было сохранено. Области и источники доступны по запросу.');
    if(error)tasks.append(stale?button('Перечитать выбранную карту','reload',()=>onAction('reload',null,{projectId:scope.projectId,snapshotId:scope.snapshotId})):button('Повторить чтение обзора','retry',()=>refresh()));
    if(!data){onLayout();return;}
    for(const task of TASKS){
      const start=button(task.title,task.id,()=>action(task.id));start.disabled=!data.snapshot.available;start.title=task.description;tasks.append(start);
    }
    const failures=Array.isArray(data.diagnostics?.issues)?data.diagnostics.issues:[];
    if(diagnostics.partial){
      issues.hidden=false;
      const count=diagnostics.errorCount??failures.length;
      issues.querySelector('summary').textContent='Проблемы ('+count+')';
      for(const issue of failures.slice(0,24))issueList.append(analysisIssueCard(issue,{projectId:data.projectId,onNode:nodeId=>action('issue',{nodeId})}));
      if(data.diagnostics?.truncated||failures.length>24)issueList.append(el('p','Список ограничен; показаны '+Math.min(failures.length,24)+'.','small muted'));
    }
    const purpose=el('section');purpose.append(el('h3','Что делает проект'));
    purpose.append(el('p',data.purpose?.text || 'Назначение не описано в сохранённых источниках. Название папки не считается описанием проекта.'));
    purpose.append(evidence(data.purpose?.provenance));body.append(purpose);
    const areas=el('section');areas.append(el('h3','Основные области'));
    if(!data.areas.length)areas.append(el('p','Области не вошли в этот снимок. Это не означает, что в проекте нет кода.','muted'));
    const list=el('ul',undefined,'overview-areas');
    for(const area of data.areas.slice(0,24)){
      const item=el('li'),canOpen=data.snapshot.available&&Array.isArray(area.nodeIds)&&area.nodeIds.length>0;
      const title=canOpen?button(area.title||area.path||area.id,'area:'+area.id,()=>action('area',area)):el('strong',area.title||area.path||area.id);
      item.append(title,el('span',ROLES[area.role]||ROLES.unknown,'overview-role'));
      if(area.path)item.append(el('code',area.path));
      if(area.reason)item.append(el('p',area.reason));
      if(Number.isFinite(area.nodeCount))item.append(el('small',new Intl.NumberFormat(OVERVIEW_LOCALE.locale).format(area.nodeCount)+' узлов в снимке'));
      item.append(evidence(area.provenance));list.append(item);
    }
    areas.append(list);body.append(areas);
    if(data.suggestedStarts.length){
      const starts=el('section');starts.append(el('h3','Подтверждённые ориентиры'));
      for(const start of data.suggestedStarts.slice(0,12)){
        const item=el('div',undefined,'overview-reference');
        const entry=button(start.title||'Открыть ориентир','start:'+start.id,()=>action('suggestion',start));
        entry.disabled=!data.snapshot.available;item.append(entry,evidence(start.provenance));starts.append(item);
      }
      body.append(starts);
    }
    const limits=el('p','Обзор основан на сохранённых файлах. Статические связи не доказывают выполнение кода или актуальность рабочей папки.','small muted');body.append(limits);
    for(const limitation of (data.limitations||[]).slice(0,6))if(typeof limitation==='string')body.append(el('p',limitation,'small muted'));
    const help=el('a','Как пользоваться Atlas ↗');help.href='/wiki.html?page=Explore-Code';help.target='_blank';help.rel='noopener';body.append(help,el('small','Руководство Atlas; это не документация выбранного проекта.'));
    if(focusKey)for(const node of root.querySelectorAll('[data-overview-action]'))if(node.dataset.overviewAction===focusKey)node.focus({preventScroll:true});
    onLayout();
  }

  /**
   * Читает обзор с конечным timeout через host-транспорт; каждое поколение имеет собственный AbortSignal.
   * @returns {Promise<void>} Ошибка отражается локально; поздний/отменённый ответ не меняет новый scope.
   */
  async function refresh(){
    if(!scope||disposed)return;
    controller?.abort();controller=new AbortController();const own=++generation,target={...scope};loading=true;error='';stale=false;render();
    const suffix=target.snapshotId?'?snapshotId='+encodeURIComponent(target.snapshotId):'';
    try{
      const response=await request('/api/projects/'+encodeURIComponent(target.projectId)+'/overview'+suffix,{signal:controller.signal});
      if(disposed||own!==generation)return;
      data=validateOverview(response,target);
      onState(data);
    }catch(failure){if(disposed||own!==generation||controller.signal.aborted)return;stale=failure.status===409;error=stale?'Снимок обзора изменился. Откройте проект заново.':'Не удалось прочитать обзор. Карта и её текущий контекст сохранены.';}
    finally{if(!disposed&&own===generation){loading=false;render();}}
  }

  return {
    /**
     * Новый ключ очищает только чужой обзор; повторный sync того же scope не запускает запрос заново.
     * @param {object} context project — выбранная регистрация либо null; snapshotId — принятая ревизия либо null.
     * @returns {void} Запускает отменяемое чтение без ожидания и без изменения карты.
     */
    sync({project,snapshotId}){
      const next=project?JSON.stringify([project.id,snapshotId||null,project.status,project.error]):'';
      if(next===signature)return;signature=next;
      const nextKey=project?JSON.stringify([project.id,snapshotId||null]):'';
      controller?.abort();generation++;
      if(nextKey!==key){data=null;error='';stale=false;setOpen(false);details.open=false;issues.open=false;}
      key=nextKey;scope=project?{projectId:project.id,snapshotId:snapshotId||null,name:project.name||project.id}:null;
      if(scope)void refresh();else render();
    },
    refresh,
    isOpen:()=>panelOpen,
    setOpen,
    dispose(){disposed=true;generation++;controller?.abort();details.removeEventListener('toggle',onLayout);issues.removeEventListener('toggle',onLayout);},
  };

  /** Переключает обзор независимо от вложенных секций, не меняя карту или камеру. */
  function setOpen(open){
    if(panelOpen===open)return;
    panelOpen=open;
    root.hidden=!scope||!open;
    onLayout();
  }
}
