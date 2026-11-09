import {requestJson} from './bounded-request.js';
import {MapCleanupState,cleanupMapKey,cleanupMapURL} from './map-cleanup-state.js';
import {mapCleanupLabels} from './map-cleanup-labels.js';

/**
 * Builds host-native snapshot cleanup controls; the server owns plans, authorization and deletion.
 * @param {HTMLElement} root Existing technical storage section with its table/status/actions.
 * @param {object} options Optional host integration; no global preferences are changed.
 * @param {Function} options.fetcher Testable fetch transport, defaulting to the current browser.
 * @param {object} options.localeContext Explicit locale/enabled/fallback languages for owned labels and Intl.
 * @param {Function} options.onCatalog Receives an accepted catalog for the host's totals/path presentation.
 * @param {?Storage} options.receiptStorage Optional sessionStorage override; null tests blocked storage. Only one opaque plan ID is stored.
 */
export class MapCleanupController {
  constructor(root,{fetcher=(...args)=>fetch(...args),localeContext={locale:'ru',availableLocales:['ru','en'],fallbackLocale:'ru'},onCatalog=()=>{},receiptStorage=undefined}={}) {
    this.root=root;this.document=root.ownerDocument;this.fetcher=fetcher;this.labels=mapCleanupLabels(localeContext);
    this.transports=new Set();this.onCatalog=onCatalog;
    try{this.receiptStorage=receiptStorage===undefined?this.document.defaultView?.sessionStorage:receiptStorage;}catch{this.receiptStorage=null;}
    this.lastPlanId=this.readReceipt();this.state=new MapCleanupState();if(this.lastPlanId)this.state.phase='uncertain';this.rows=new Map();this.revision=0;this.disposed=false;
    this.table=root.querySelector('#storage-maps');this.status=root.querySelector('#storage-status');
    this.reload=root.querySelector('#reload-storage');this.controls=this.element('div',undefined,'map-cleanup-controls');
    this.controls.lang=this.labels.locale;this.summary=this.element('p');this.summary.setAttribute('role','status');
    this.notice=this.element('p');this.notice.setAttribute('role','status');this.notice.id='map-cleanup-status';
    const actions=this.element('div',undefined,'actions');
    this.previewButton=this.button('preview',()=>this.preview());this.previewButton.id='map-cleanup-preview';
    this.deleteButton=this.button('delete',()=>this.previewDelete());this.deleteButton.id='map-cleanup-delete';this.deleteButton.className='map-cleanup-danger';
    this.clearButton=this.button('clearSelection',()=>{if(!this.state.locked){this.state.selected.clear();this.state.discardPlan();this.sync();}});
    this.checkButton=this.button('check',()=>this.check());this.checkButton.id='map-cleanup-check';
    this.retryButton=this.button('retry',()=>this.execute());this.retryButton.id='map-cleanup-retry';
    actions.append(this.previewButton,this.deleteButton,this.clearButton,this.checkButton,this.retryButton);
    this.bulkHint=this.element('p',undefined,'muted');this.bulkHint.setAttribute('role','status');
    this.controls.append(this.summary,this.element('p',this.labels.text('capacity'),'muted'),actions,this.bulkHint,this.notice);
    root.insertBefore(this.controls,root.querySelector('.table-scroll'));
    this.pendingPhysical=this.element('p',undefined,'map-cleanup-pending');this.recovery=this.element('section',undefined,'map-cleanup-recovery');
    this.recovery.append(this.element('h3',this.labels.text('recovery')));this.recoveryList=this.element('div',undefined,'actions');this.recoveryStatus=this.element('p');this.recoveryStatus.setAttribute('role','status');
    this.recoveryRefresh=this.button('refreshRecovery',()=>this.loadRecovery());this.recovery.append(this.recoveryList,this.recoveryStatus,this.recoveryRefresh);
    this.receiptNotice=this.element('p');this.receiptNotice.setAttribute('role','status');this.receiptLink=this.element('a',this.labels.text('receipt'));this.receiptLink.hidden=true;this.receiptLink.target='_blank';this.receiptLink.rel='noopener noreferrer';
    this.controls.append(this.pendingPhysical,this.recovery,this.receiptNotice,this.receiptLink);
    this.buildDialog();
    if(this.lastPlanId){this.receiptLink.setAttribute('href',`/api/technical/map-cleanup/${encodeURIComponent(this.lastPlanId)}`);this.receiptLink.hidden=false;this.announce('uncertain');}
    this.onReload=()=>this.start();this.reload.addEventListener('click',this.onReload);this.sync();
  }

  /**
   * Creates literal DOM text; neither server strings nor names are parsed as markup.
   * @param {string} tag Fixed HTML tag chosen by the host.
   * @param {?string} text Optional literal text; undefined leaves an empty element.
   * @param {?string} className Optional host-owned CSS class, never server-provided.
   * @returns {HTMLElement} Detached element owned by this document.
   */
  element(tag,text,className) {
    const item=this.document.createElement(tag);if(text!==undefined)item.textContent=text;if(className)item.className=className;return item;
  }

  /**
   * Creates one labelled native action; asynchronous handlers own pending/error states.
   * @param {string} key Stable message key for the visible button label.
   * @param {Function} callback Host action handler; no server-returned command is executed.
   * @returns {HTMLButtonElement} Detached button with type=button.
   */
  button(key,callback) {const item=this.element('button',this.labels.text(key));item.type='button';item.addEventListener('click',callback);return item;}

  /**
   * Mounts one native modal with safe default focus and explicit consequences, not a custom focus trap.
   */
  buildDialog() {
    this.dialog=this.element('dialog',undefined,'map-cleanup-dialog');this.dialog.id='map-cleanup-dialog';this.dialog.lang=this.labels.locale;
    this.dialog.setAttribute('aria-labelledby','map-cleanup-title');this.dialog.setAttribute('aria-describedby','map-cleanup-consequences');
    this.dialogTitle=this.element('h2',this.labels.text('confirmTitle'));this.dialogTitle.id='map-cleanup-title';
    this.consequences=this.element('p',this.labels.text('consequence'));this.consequences.id='map-cleanup-consequences';
    this.preserved=this.element('p');this.estimates=this.element('p',undefined,'muted');
    this.planList=this.element('ul',undefined,'map-cleanup-plan-list');this.warningList=this.element('ul');this.warningDetails=this.element('details');this.warningDetails.append(this.element('summary',this.labels.text('warnings')),this.warningList);this.planTotal=this.element('p');this.expiry=this.element('p',undefined,'muted');
    this.dialogStatus=this.element('p');this.dialogStatus.setAttribute('role','status');this.dialogStatus.id='map-cleanup-dialog-status';
    const actions=this.element('div',undefined,'actions');this.cancel=this.button('cancel',()=>this.closeDialog());this.cancel.id='map-cleanup-cancel';this.cancel.autofocus=true;
    this.confirm=this.button('confirm',()=>this.execute());this.confirm.id='map-cleanup-confirm';this.confirm.className='map-cleanup-danger';
    actions.append(this.cancel,this.confirm);
    const body=this.element('div',undefined,'map-cleanup-dialog-body');this.dialogBody=body;body.tabIndex=0;body.setAttribute('aria-label',this.labels.text('details'));
    body.append(this.consequences,this.preserved,this.planList,this.planTotal,this.estimates,this.warningDetails,this.expiry);
    this.dialog.append(this.dialogTitle,body,this.dialogStatus,actions);
    this.dialog.addEventListener('cancel',event=>{event.preventDefault();this.closeDialog();});
    this.dialog.addEventListener('close',()=>this.returnFocus());this.root.append(this.dialog);
  }

  /**
   * Returns focus only to a surviving trigger after modal dismissal, never on a background status update.
   */
  returnFocus() {
    if(this.disposed)return;
    for(const target of [this.focusOrigin,this.checkButton,this.retryButton,this.reload,this.previewButton,this.deleteButton]) {
      if(target?.isConnected&&!target.disabled&&!target.hidden){target.focus({preventScroll:true});return;}
    }
  }

  /**
   * Closes confirmation without cancelling an already submitted server operation.
   */
  closeDialog() {
    if(!this.state.confirmed){this.forgetReceipt(this.state.plan?.planId);this.state.discardPlan();}
    this.dialog.close();this.sync();
  }

  /**
   * Sends one bounded same-origin request; finite response/body wait does not imply server rollback.
   * @param {string} path Fixed API path, with encoded server-issued plan IDs only.
   * @param {object} options Native fetch options; no automatic mutation retry.
   * @returns {Promise<object>} Parsed API result; HTTP/timeout/network errors retain their original codes.
   */
  async request(path,options={}) {
    const controller=new AbortController();this.transports.add(controller);
    const abort=()=>controller.abort();options.signal?.addEventListener('abort',abort,{once:true});
    if(options.signal?.aborted)controller.abort();
    try {return await requestJson(path,{...options,signal:controller.signal},{fetcher:this.fetcher,timeoutMs:120000,activity:{ru:'Хранилище карт…',en:'Map storage…'}});}
    finally {this.transports.delete(controller);options.signal?.removeEventListener('abort',abort);}
  }

  /**
   * Refreshes the catalog while retaining keyed rows, choices and focus; ignored aborts cannot overwrite newer data.
   * @param {boolean} allowLocked Internal post-commit refresh may read current rows while own purge awaits retry.
   * @returns {Promise<void>} Errors leave the previous catalog in place and announce a retryable read failure.
   */
  async load(allowLocked=false) {
    if(this.disposed||this.state.locked&&!allowLocked)return;
    this.loadController?.abort();this.loadController=new AbortController();const revision=++this.revision;
    this.reload.disabled=true;this.status.textContent=this.labels.text('loading');
    try {
      const data=await this.request('/api/technical/storage',{signal:this.loadController.signal});
      if(this.disposed||revision!==this.revision)return;
      if(!Array.isArray(data.maps)||!Array.isArray(data.codeMaps))throw new Error(this.labels.text('error'));
      this.state.setCatalog(data);this.renderRows();this.onCatalog(data);this.status.textContent='';
      this.pendingPhysical.textContent=data.cleanupStorage?.hasPendingPhysicalData?this.labels.text('pendingPhysical'):'';
    } catch(error) {if(!this.disposed&&revision===this.revision&&error.name!=='AbortError')this.status.textContent=this.labels.text(this.state.plan?.registrationRemoved===true?'forgetCatalogError':'error');}
    finally {if(!this.disposed&&revision===this.revision){this.reload.disabled=false;this.sync();}}
  }

  /**
   * Updates cells in existing rows; replacement is limited to identities actually added or removed by the catalog.
   */
  renderRows() {
    for(const [key,item] of this.rows)if(!this.state.rows.has(key)){item.row.remove();this.rows.delete(key);}
    for(const [key,map] of this.state.rows) {
      let item=this.rows.get(key);
      if(!item) {
        const row=this.element('tr');row.lang=this.labels.locale;row.setAttribute('data-map-key',key);const cells=Array.from({length:8},()=>this.element('td'));
        const label=this.element('label',undefined,'map-cleanup-choice');const checkbox=this.element('input');checkbox.type='checkbox';
        label.append(checkbox,this.element('span',this.labels.text('selectShort')));cells[0].append(label);
        const link=this.element('a');cells[1].append(link);const folder=this.element('button',this.labels.text('folder'));folder.type='button';
        const forget=this.button('forget',()=>this.previewForget(key));forget.className='map-cleanup-forget';
        const rowActions=this.element('div',undefined,'map-cleanup-row-actions');rowActions.append(folder,forget);cells[7].append(rowActions);
        checkbox.addEventListener('change',()=>{this.state.choose(key,checkbox.checked);this.sync();});
        folder.addEventListener('click',()=>this.openFolder(key));row.append(...cells);this.table.append(row);
        const reason=this.element('small',undefined,'map-cleanup-reason');cells[0].append(reason);
        item={row,cells,checkbox,link,folder,forget,reason};this.rows.set(key,item);
      }
      item.checkbox.setAttribute('aria-label',`${this.labels.text('select')}: ${map.title} · ${this.labels.text(map.kind)}`);
      item.link.textContent=map.title;const destination=cleanupMapURL(map.url);
      if(destination)item.link.setAttribute('href',destination);else item.link.removeAttribute('href');
      const freshness={current:'current',stale:'staleRow',unavailable:'missing','not-built':'notBuilt','not-checked':'unchecked'};
      const status=this.labels.text(freshness[map.freshness?.status]||'unchecked');
      const version=map.cleanup?.versionStatus==='known'&&map.cleanup.analysisVersion?String(map.cleanup.analysisVersion):this.labels.text('versionUnknown');
      const buildKey={pending:'buildingRow',running:'buildingRow',building:'buildingRow',indexing:'buildingRow',failed:'failedRow'}[map.status];
      const values=[this.labels.text(map.kind),this.labels.date(map.builtAt),version,status+(buildKey?' · '+this.labels.text(buildKey):''),this.labels.bytes(map.sizeBytes)];
      for(let i=0;i<values.length;i++)item.cells[i+2]?.replaceChildren(this.document.createTextNode(values[i]));
      // Size stays in its own column; folder action is the final eighth cell.
      item.folder.hidden=map.kind!=='data'||!map.sizeBytes;
      const reasonKey={'no-clearable-snapshots':'noSnapshots','unsafe-storage':'unsafeStorage','missing-snapshot':'missingSnapshot','unrecognized-snapshot-file':'unrecognizedSnapshot','unrecognized-snapshot-directory':'unrecognizedSnapshot'}[map.cleanup?.reason]||'unavailable';
      item.checkbox.title=map.cleanup?.available?'':this.labels.text(reasonKey);
      item.reason.textContent=map.cleanup?.reason==='missing-snapshot'?this.labels.text('missingSnapshot'):item.checkbox.title;
      item.forget.hidden=map.cleanup?.forgetAvailable!==true;
      item.forget.setAttribute('aria-label',`${this.labels.text('forget')}: ${map.title} · ${this.labels.text(map.kind)}`);
      for(let i=0;i<item.cells.length;i++)item.cells[i].setAttribute('data-label',this.labels.text(['select','title','layer','date','version','rowStatus','rowSize','actions'][i]));
      item.row.setAttribute('data-cleanup-available',String(Boolean(map.cleanup?.available)));
    }
    if(!this.state.rows.size){
      this.emptyRow ||= this.element('tr');const cell=this.element('td',this.labels.text('empty'));cell.colSpan=8;this.emptyRow.replaceChildren(cell);this.table.append(this.emptyRow);
    }else this.emptyRow?.remove();
    this.sync();
  }

  /**
   * Opens only the explicitly clicked data-map folder through the existing host API.
   * @param {string} key Current namespaced data-map identity; unknown/code IDs are ignored.
   * @returns {Promise<void>} Folder/API failures stay visible without clearing selection.
   */
  async openFolder(key) {
    const map=this.state.rows.get(key);if(!map||map.kind!=='data')return;
    try {await this.request('/api/technical/open-storage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mapId:map.id})});}
    catch {if(!this.disposed)this.status.textContent=this.labels.text('error');}
  }

  /**
   * Synchronizes button/selection state without replacing rows, names or the current focus target.
   */
  sync() {
    const selection=this.state.selection(),t=this.labels.text;
    const selectedSize=selection.unknown===selection.count&&selection.count ? t('unknown') : this.labels.bytes(selection.bytes)+(selection.unknown?' + '+selection.unknown+' '+t('unknown'):'');
    this.summary.textContent=`${t('selected')}: ${selection.count}/50 · ${t('bytes')}: ${selectedSize}`;
    this.previewButton.disabled=this.state.locked||!selection.canClear;this.deleteButton.disabled=this.state.locked||!selection.canDelete;
    const hints=[];
    if(selection.count&&!selection.canClear)hints.push(t('clearUnavailable'));
    if(selection.count&&!selection.canDelete)hints.push(t('deleteUnavailable'));
    this.bulkHint.textContent=hints.join(' ');this.clearButton.disabled=this.state.locked||!selection.count;
    this.reload.disabled=this.state.locked||Boolean(this.loadController&&!this.loadController.signal.aborted&&this.status.textContent===t('loading'));
    this.checkButton.hidden=!['uncertain','purging','retry'].includes(this.state.phase);this.checkButton.disabled=['executing','checking'].includes(this.state.phase);
    this.retryButton.hidden=this.state.phase!=='retry';this.retryButton.disabled=this.state.phase!=='retry';this.retryButton.textContent=this.operationText('retry');
    for(const [key,item] of this.rows){item.checkbox.checked=this.state.selected.has(key);item.checkbox.disabled=this.state.locked||!this.state.selectable(key);item.forget.disabled=this.state.locked||this.state.rows.get(key)?.cleanup?.forgetAvailable!==true;}
    for(const button of this.recoveryList.children)button.disabled=this.state.locked;
    this.confirm.disabled=this.state.phase!=='ready';this.cancel.textContent=t(this.state.confirmed?'close':'cancel');
  }

  /**
   * Opens a current exact-selection preview; selection is locked until its response or explicit dismissal.
   */
  async preview() {
    const selection=this.state.selection();if(!selection.canClear)return;
    return this.previewAction(selection.maps,'clear-snapshots',this.previewButton);
  }

  /**
   * Reviews the whole explicit selection for snapshot purge plus registration removal.
   * @returns {Promise<void>} A distinct Cancel-default confirmation; missing capabilities never narrow the selection.
   */
  async previewDelete() {
    const selection=this.state.selection();if(!selection.canDelete)return;
    return this.previewAction(selection.maps,'delete-map',this.deleteButton);
  }

  /**
   * Reviews only the explicitly clicked registration, independently of snapshot-selection checkboxes.
   * @param {string} key Current row identity; availability is a server hint and preview remains authoritative.
   * @returns {Promise<void>} Opens metadata-only confirmation; no files or registrations change on preview.
   */
  async previewForget(key) {
    const row=this.state.rows.get(key);if(row?.cleanup?.forgetAvailable!==true)return;
    return this.previewAction([{kind:row.kind,id:row.id}],'forget-registration',this.rows.get(key)?.forget);
  }

  /**
   * Binds one preview to its explicit action and exact targets before opening the native confirmation.
   * @param {Array} maps Explicit current identities; never inferred from paths or an automatic selection.
   * @param {string} action One supported cleanup action, retained in every subsequent receipt check.
   * @param {HTMLElement} trigger Surviving focus-return target after dismissal.
   * @returns {Promise<void>} Failed or substituted previews preserve the existing catalog and selection.
   */
  async previewAction(maps,action,trigger) {
    if(this.disposed||this.state.locked)return;
    this.focusOrigin=trigger;this.pendingAction=action;this.status.textContent='';
    this.loadController?.abort();this.revision++;this.state.discardPlan();this.state.phase='previewing';this.notice.textContent=this.operationText('previewing');this.sync();
    const revision=this.revision;
    try {
      const plan=await this.request('/api/technical/map-cleanup/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({maps,action})});
      if(this.disposed||revision!==this.revision)return;
      if(plan.status!=='ready'||plan.mapsCleared===true||plan.registrationRemoved===true)throw new Error('INVALID_CLEANUP_PLAN');
      this.state.accept(plan,maps,action);this.state.phase='ready';this.notice.textContent='';this.showPlan();
    } catch(error) {if(!this.disposed&&revision===this.revision){this.state.discardPlan();this.notice.textContent=this.errorText(error);}}
    finally {if(!this.disposed)this.sync();}
  }

  /**
   * Resolves action-specific host text; server strings never become labels or commands.
   * @param {string} key Shared message key with optional metadata-only or combined-delete counterparts.
   * @returns {string} Plain localized text for the current receipt or pending preview action.
   */
  operationText(key) {
    const action=this.state.plan?.action ?? this.pendingAction;
    const forgetKeys={confirmTitle:'forgetTitle',confirm:'forget',consequence:'forgetConsequence',preserved:'forgetPreserved',details:'forgetDetails',previewing:'forgetPreviewing',executing:'forgetExecuting',completed:'forgetCompleted',failed:'forgetFailed',readyAgain:'forgetReadyAgain',retry:'forgetRetry',stale:'forgetStale'};
    const deleteKeys={confirmTitle:'deleteTitle',confirm:'delete',consequence:'deleteConsequence',preserved:'deletePreserved',details:'deleteDetails',previewing:'deletePreviewing',executing:'deleteExecuting',completed:'deleteCompleted',partial:'deletePartial',purging:'deletePurging',failed:'deleteFailed',readyAgain:'deleteReadyAgain',retry:'deleteRetry',stale:'deleteStale'};
    let overrides={};
    if(action==='forget-registration')overrides=forgetKeys;
    else if(action==='delete-map')overrides=deleteKeys;
    return this.labels.text(overrides[key]||key);
  }

  /**
   * Shows only server-confirmed target names and sizes with Cancel as the initial keyboard action.
   */
  showPlan() {
    const plan=this.state.plan,t=this.labels.text,forget=plan.action==='forget-registration';this.planList.replaceChildren();
    this.dialogBody.setAttribute('aria-label',this.operationText('details'));this.dialogTitle.textContent=this.operationText('confirmTitle');this.confirm.textContent=this.operationText('confirm');
    this.consequences.textContent=this.operationText('consequence');this.preserved.textContent=this.operationText('preserved');
    this.estimates.textContent=forget?'':t('estimates');this.estimates.hidden=forget;
    for(const map of plan.maps)this.planList.append(this.element('li',`${map.title} · ${t(map.kind)}`+(forget?'':` · ${t('snapshots')}: ${map.snapshotCount ?? t('unknown')} · ${this.labels.bytes(map.logicalBytes ?? map.sizeBytes)}`)));
    this.planTotal.textContent=`${t('selected')}: ${plan.maps.length}`+(forget?'':` · ${t('bytes')}: ${this.labels.bytes(plan.totals.sizeBytes)}`);
    this.warningList.replaceChildren();
    for(const warning of (plan.warnings||[]).slice(0,50))this.warningList.append(this.element('li',String(warning).slice(0,1000)));
    this.warningDetails.hidden=!this.warningList.children.length;
    this.expiry.textContent=`${t('expire')}: ${this.labels.date(plan.expiresAt)}`;this.dialogStatus.textContent='';
    this.dialog.showModal();this.cancel.focus();
  }

  /**
   * Maps known failure states to host-owned recovery guidance; server text is never executed.
   * @param {Error} error HTTP status/code or local invalid-plan error from the bounded request.
   * @returns {string} Localized recovery text; unrecognized details use a generic safe message.
   */
  errorText(error) {
    if(error.code==='CLEANUP_MAP_MISSING')return this.labels.text('missingMap');
    if(error.code==='CLEANUP_EMPTY')return this.labels.text('emptySelection');
    if(error.code==='CLEANUP_LIMIT')return this.labels.text('limit');
    if(error.code==='CLEANUP_STALE')return this.operationText('stale');
    if(error.code==='CLEANUP_EXPIRED'||error.status===410||error.status===404)return this.labels.text('expired');
    if(error.code==='CLEANUP_BUSY')return this.labels.text('busy');
    if(error.code==='CLEANUP_UNSAFE'&&['unrecognized-snapshot-file','unrecognized-snapshot-directory'].includes(error.reason))return this.labels.text('unrecognizedSnapshot');
    if(error.code==='CLEANUP_UNSAFE'||error.code==='CLEANUP_CONFLICT')return this.labels.text('unsafe');
    return this.labels.text(error.message==='INVALID_CLEANUP_PLAN'?'invalid':'error');
  }

  /**
   * Executes only the current explicitly confirmed plan; duplicate clicks and unknown acknowledgements never create a new plan.
   * @returns {Promise<void>} Server outcome is displayed; a lost reply permits only a same-plan result read first.
   */
  async execute() {
    if(this.disposed||!this.state.plan||!['ready','retry'].includes(this.state.phase))return;
    if(this.state.plan.status==='ready'&&Date.parse(this.state.plan.expiresAt)<=Date.now()){this.expire(this.labels.text('expired'));return;}
    const plan=this.state.plan;this.rememberReceipt(plan.planId);this.loadController?.abort();this.revision++;this.status.textContent='';this.state.confirmed=true;this.state.phase='executing';this.announce('executing');this.sync();
    try {
      const result=await this.request(`/api/technical/map-cleanup/${encodeURIComponent(plan.planId)}/execute`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})});
      if(!this.disposed)this.receive(result,plan);
    } catch(error) {
      if(this.disposed)return;
      if(error.code==='CLEANUP_STALE'||error.status===410||error.status===404||error.code==='CLEANUP_UNSAFE'||error.code==='CLEANUP_CONFLICT')this.expire(this.errorText(error));
      else {this.state.phase='uncertain';this.announce(error.code==='CLEANUP_BUSY'?'busy':'uncertain');if(this.dialog.open)this.dialog.close();}
    } finally {if(!this.disposed)this.sync();}
  }

  /**
   * Reads only the current plan after a lost acknowledgement or pending purge, preserving its identity across retries.
   */
  async check() {
    if(this.disposed||['executing','checking'].includes(this.state.phase))return;
    if(!this.state.plan){if(this.lastPlanId)return this.recover(this.lastPlanId);return;}
    const plan=this.state.plan;this.state.phase='checking';this.announce('purging');this.sync();
    try {const result=await this.request(`/api/technical/map-cleanup/${encodeURIComponent(plan.planId)}`);if(!this.disposed)this.receive(result,plan);}
    catch(error){if(!this.disposed){if(error.status===404||error.status===410||error.code==='CLEANUP_STALE')this.expire(this.errorText(error));else {this.state.phase='uncertain';this.announce('uncertain');}}}
    finally {if(!this.disposed)this.sync();}
  }

  /**
   * Commits a validated same-action outcome; registration-removing results reconcile through a fresh catalog.
   * Old receipts cannot remove a new registration with the same ID, even before that read completes.
   * @param {object} result Server-issued state; mapsCleared/registrationRemoved acknowledge their bound action.
   * @param {object} previous Exact plan captured before execute/read, used for identity and scope validation.
   * @throws {Error} A substituted or malformed plan is rejected before acknowledging success.
   */
  receive(result,previous) {
    this.state.accept(result,previous.maps,previous.action ?? 'clear-snapshots');
    if(result.mapsCleared===true&&result.registrationRemoved!==true)this.renderRows();
    if(result.mapsCleared===true||result.registrationRemoved===true){if(this.dialog.open)this.dialog.close();void this.load(true);}
    if(result.status==='expired'){this.expire(this.labels.text('expired'));return;}
    if(result.status==='ready'){
      this.state.phase=this.state.confirmed?'retry':'ready';
      if(this.state.confirmed)this.announce('readyAgain');else this.showPlan();
      return;
    }
    if(result.status==='purging'){this.state.phase='purging';this.announce('purging');void this.loadRecovery();return;}
    this.state.phase=result.canRetry?'retry':'idle';this.announce(result.status);
    if(!result.canRetry)this.forgetReceipt(result.planId);
    this.renderRows();
    void this.loadRecovery();
  }

  /**
   * Invalidates execution consent after stale/expired scope; a new preview is always required.
   * @param {string} message Host-owned localized reason shown after closing the modal.
   */
  expire(message) {this.forgetReceipt(this.state.plan?.planId);this.state.discardPlan();if(this.dialog.open)this.dialog.close();this.notice.textContent=message;this.sync();}

  /**
   * Updates in-flow and modal polite status together without moving focus.
   * @param {string} key Stable message key; a finite pending-purge count adds current plan progress.
   */
  announce(key) {
    const pending=this.state.plan?.pendingPurgeCount;
    const removed=this.state.plan?.mapsCleared===true?this.state.plan.removedBytes:null;
    const text=this.operationText(key)+(Number.isFinite(pending)&&pending>0?' · '+this.labels.text('pending')+': '+pending:'')
      +(Number.isFinite(removed)&&removed>=0?' · '+this.labels.text('removedBytes')+': '+this.labels.bytes(removed):'');
    this.notice.textContent=text;this.dialogStatus.textContent=text;
  }

  /**
   * Reads at most one bounded opaque plan ID; malformed/private values are ignored without executing anything.
   */
  readReceipt() {
    try{const id=this.receiptStorage?.getItem('atlas-map-cleanup-active-plan');return typeof id==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(id)?id:null;}
    catch{return null;}
  }

  /**
   * Retains only the confirmed operation ID before sending; no map titles, paths or settings are stored.
   * @param {string} planId Server-issued plan ID; failure keeps an explicit same-origin result link.
   */
  rememberReceipt(planId) {
    this.lastPlanId=planId;this.receiptLink.setAttribute('href',`/api/technical/map-cleanup/${encodeURIComponent(planId)}`);this.receiptLink.hidden=false;
    try{if(!this.receiptStorage)throw new Error('STORAGE_UNAVAILABLE');this.receiptStorage.setItem('atlas-map-cleanup-active-plan',planId);this.receiptNotice.textContent='';}
    catch{this.receiptNotice.textContent=this.labels.text('receiptUnavailable');}
  }

  /**
   * Clears only this known terminal/cancelled record, leaving an unrelated active receipt untouched.
   * @param {?string} planId Completed, expired or cancelled plan ID; absent/mismatched IDs are ignored.
   */
  forgetReceipt(planId) {
    if(!planId||this.lastPlanId!==planId)return;this.lastPlanId=null;
    try{if(this.readReceipt()===planId)this.receiptStorage.removeItem('atlas-map-cleanup-active-plan');}catch{/* The visible result remains authoritative. */}
    this.receiptNotice.textContent='';
  }

  /**
   * Starts catalog and durable pending-operation discovery; page reload never executes a cleanup.
   * @returns {Promise<void>} Each read has independent visible failure recovery.
   */
  async start() {await this.load(true);if(!this.disposed)await this.loadRecovery();}

  /**
   * Reads bounded server journals so a lost plan ID survives page reload/server restart without client-stored private data.
   */
  async loadRecovery() {
    if(this.disposed||this.recoveryLoading)return;this.recoveryLoading=true;this.recoveryRefresh.disabled=true;
    try {
      const data=await this.request('/api/technical/map-cleanup');if(this.disposed)return;
      if(data.schemaVersion!=='atlas-map-cleanup-v1'||!Array.isArray(data.operations))throw new Error('INVALID_CLEANUP_PLAN');
      this.recoveryList.replaceChildren();
      for(const operation of data.operations.slice(0,32)) {
        if(typeof operation.planId!=='string'||!operation.planId||operation.planId.length>128)continue;
        const button=this.button('recover',()=>this.recover(operation.planId));
        const names=(operation.maps||[]).slice(0,2).map(map=>String(map.title)).join(', ');
        button.textContent=`${this.labels.text('recover')}: ${names} · ${this.labels.date(operation.expiresAt)}`;
        this.recoveryList.append(button);
      }
      if(this.lastPlanId&&!data.operations.some(operation=>operation.planId===this.lastPlanId)){
        const lastId=this.lastPlanId;this.recoveryList.append(this.button('checkLast',()=>this.recover(lastId)));
      }
      this.recoveryStatus.textContent=data.truncated?this.labels.text('pendingPhysical'):'';
      this.recovery.hidden=!this.recoveryList.children.length&&!data.truncated;
    }catch{if(!this.disposed){this.recovery.hidden=false;this.recoveryStatus.textContent=this.labels.text('recoveryError');}}
    finally{this.recoveryLoading=false;if(!this.disposed){this.recoveryRefresh.disabled=false;this.sync();}}
  }

  /**
   * Opens one durable record for explicit review; a ready uncommitted plan still requires native confirmation.
   * @param {string} planId Server-listed opaque ID; only the encoded fixed result route is requested.
   * @returns {Promise<void>} No execute request is issued during recovery.
   */
  async recover(planId) {
    if(this.disposed||this.state.locked&&(this.state.plan||planId!==this.lastPlanId))return;this.focusOrigin=this.document.activeElement;this.state.discardPlan();this.state.phase='checking';this.sync();
    try {
      const result=await this.request(`/api/technical/map-cleanup/${encodeURIComponent(planId)}`);
      if(this.disposed)return;if(result.planId!==planId)throw new Error('INVALID_CLEANUP_PLAN');
      this.state.confirmed=result.mapsCleared===true||result.registrationRemoved===true;
      this.receive(result,{maps:result.maps,action:result.action ?? 'clear-snapshots'});
    }catch(error){if(!this.disposed){
      if(error.status===404||error.status===410||error.code==='CLEANUP_STALE'){this.forgetReceipt(planId);this.expire(this.errorText(error));}
      else{this.state.phase='uncertain';this.announce('uncertain');}
    }}
    finally{if(!this.disposed)this.sync();}
  }

  /**
   * Releases pending catalog work; an already submitted mutation is never represented as cancelled server-side.
   */
  dispose() {this.disposed=true;this.revision++;this.loadController?.abort();for(const controller of this.transports)controller.abort();if(this.dialog.open)this.dialog.close();this.dialog.remove();this.controls.remove();this.reload.removeEventListener('click',this.onReload);}
}
