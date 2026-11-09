const kinds = new Set(['code','data']);
const actions = new Set(['clear-snapshots','forget-registration','delete-map']);
const statuses = new Set(['ready','purging','completed','partial','failed','expired']);

/**
 * Namespaces a server map ID by its layer; no path is accepted as deletion authority.
 * @param {object} map A code/data catalog or plan item with string id.
 * @returns {string} Collision-free selection key, or an empty key for an invalid identity.
 */
export function cleanupMapKey(map) {
  return kinds.has(map?.kind) && typeof map.id === 'string' && map.id ? JSON.stringify([map.kind,map.id]) : '';
}

/**
 * Restricts catalog navigation to known local map pages without loading external resources.
 * @param {unknown} value Server-provided relative URL; paths, schemes and credentials remain untrusted.
 * @returns {?string} Normalized local map URL, or null for an unsupported/unsafe destination.
 */
export function cleanupMapURL(value) {
  if(typeof value!=='string'||value.length>4096||!value.startsWith('/')||value.startsWith('//')||/[\\\u0000-\u0020]/.test(value))return null;
  try {
    const url=new URL(value,'http://atlas.invalid');
    if(url.origin!=='http://atlas.invalid'||!['/','/index.html','/data.html'].includes(url.pathname))return null;
    return url.pathname+url.search+url.hash;
  }catch{return null;}
}

/**
 * Holds selection and one server-owned preview independently of DOM and transport.
 * Catalog refresh preserves surviving choices; no operation automatically selects maps.
 */
export class MapCleanupState {
  constructor() { this.rows = new Map(); this.selected = new Set(); this.plan = null; this.confirmed = false; this.phase = 'idle'; }

  /**
   * Reconciles catalog data without mutating its objects or inventing availability for older servers.
   * @param {object} catalog Existing storage response with maps and codeMaps arrays.
   */
  setCatalog(catalog) {
    const next = new Map();
    for (const [kind,items] of [['data',catalog.maps || []],['code',catalog.codeMaps || []]]) {
      for (const item of items) {
        const row = {...item,kind}; const key = cleanupMapKey(row);
        if (key) next.set(key,row);
      }
    }
    this.rows = next;
    for (const key of this.selected) if (!this.selectable(key)) this.selected.delete(key);
  }

  /**
   * Recognizes rows eligible for at least one explicit bulk action; preview remains authoritative.
   * @param {string} key Current namespaced catalog identity.
   * @returns {boolean} Whether a clear/delete capability is explicitly advertised.
   */
  selectable(key) {const row=this.rows.get(key);return row?.cleanup?.available===true||row?.cleanup?.deleteAvailable===true;}

  /**
   * Changes a single explicit choice, invalidating an unexecuted preview.
   * @param {string} key Canonical cleanupMapKey of a current selectable row.
   * @param {boolean} checked User-requested checkbox state.
   * @returns {boolean} Whether the selection changed; busy/unresolved operations cannot change scope.
   */
  choose(key, checked) {
    if (this.locked || !this.selectable(key)) return false;
    if (checked && !this.selected.has(key) && this.selected.size >= 50) return false;
    const changed = checked ? !this.selected.has(key) : this.selected.has(key);
    if (checked) this.selected.add(key); else this.selected.delete(key);
    if (changed) { this.plan = null; this.confirmed = false; }
    return changed;
  }

  get locked() { return ['previewing','executing','checking','uncertain','purging','retry'].includes(this.phase); }

  /**
   * Projects the entire explicit selection and per-action eligibility without silently dropping targets.
   * @returns {object} maps/count/known bytes/unknown-size count; canClear/canDelete require every selected row.
   */
  selection() {
    const maps = []; let bytes = 0, unknown = 0,clearCount=0,deleteCount=0;
    for (const key of this.selected) {
      const row = this.rows.get(key); if (!row) continue;
      if(row.cleanup?.available===true)clearCount++;
      if(row.cleanup?.deleteAvailable===true)deleteCount++;
      maps.push({kind:row.kind,id:row.id});
      if (Number.isFinite(row.cleanup?.logicalBytes) && row.cleanup.logicalBytes >= 0) bytes += row.cleanup.logicalBytes;
      else unknown++;
    }
    return {maps,count:maps.length,bytes,unknown,canClear:maps.length>0&&clearCount===maps.length,canDelete:maps.length>0&&deleteCount===maps.length};
  }

  /**
   * Validates a plan against the exact requested identity/action before showing or executing it.
   * Registration-removing acknowledgements never optimistically remove a possibly re-registered current row.
   * @param {object} plan API response; same-plan result reads must retain its identity and item scope.
   * @param {Array} expected Explicit selection captured for preview or the current plan's maps.
   * @param {string} expectedAction Requested action; legacy receipts without action are snapshot cleanup only.
   * @throws {Error} Malformed, substituted or unexpected-scope plans are rejected before state mutation.
   */
  accept(plan, expected, expectedAction='clear-snapshots') {
    const action=plan?.action ?? 'clear-snapshots';
    const validAction=actions.has(action) && action===expectedAction
      && (!this.plan || action===this.plan.action)
      && !(action==='forget-registration' && plan.mapsCleared===true)
      && !(action==='clear-snapshots' && plan.registrationRemoved===true)
      && (action!=='delete-map'||(plan.mapsCleared===true)===(plan.registrationRemoved===true));
    const keys = Array.isArray(plan?.maps) ? plan.maps.map(cleanupMapKey) : [];
    const wanted = new Set(expected.map(cleanupMapKey));
    const validIdentity = plan?.schemaVersion === 'atlas-map-cleanup-v1'
      && typeof plan.planId === 'string' && plan.planId.length>0 && plan.planId.length<=128;
    const validScope = keys.length===wanted.size && keys.length<=50 && new Set(keys).size===keys.length
      && keys.every(key=>key && wanted.has(key));
    const validState = statuses.has(plan?.status) && Number.isFinite(Date.parse(plan?.expiresAt))
      && Number.isFinite(plan?.totals?.sizeBytes) && plan.totals.sizeBytes>=0;
    const samePlan = !this.plan || plan?.planId===this.plan.planId;
    if (!validIdentity || !validScope || !validState || !samePlan || !validAction) throw new Error('INVALID_CLEANUP_PLAN');
    this.plan = {...plan,action};
    // An old completed receipt may outlive a new registration with the same ID.
    // Only a fresh catalog may remove its current row and associated selection.
    if (action==='forget-registration'||plan.registrationRemoved===true) return;
    if (plan.mapsCleared === true) {
      for (let index=0;index<keys.length;index++) {
        const key=keys[index],cleared=plan.maps[index];
        this.selected.delete(key);
        const row = this.rows.get(key);
        const sameSnapshot=row?.snapshotId && cleared.snapshotId ? row.snapshotId===cleared.snapshotId : row?.builtAt===cleared.builtAt;
        if (row && sameSnapshot) this.rows.set(key,{...row,status:'not-built',builtAt:null,sizeBytes:null,freshness:{status:'not-built'},cleanup:{...row.cleanup,available:false,logicalBytes:0,analysisVersion:null,versionStatus:'unknown'}});
      }
    }
  }

  /**
   * Drops an expired/stale/unconfirmed preview, retaining the user's still-valid choices.
   */
  discardPlan() { this.plan = null; this.confirmed = false; this.phase = 'idle'; }
}
