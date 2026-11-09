import {requestJson} from './bounded-request.js';
import {AgentSetupPanel} from './agent-setup.js';
import {MapCleanupController} from './map-cleanup.js';
import {CheckoutSettings} from './checkout-settings.js';
const $ = id => document.getElementById(id);
/**
 * Reads local JSON with the shared bounded transport, retaining HTTP status/error codes.
 * @param {string} path Fixed host API path; no user-derived filesystem path is executed.
 * @param {?object} body A provided body selects POST; undefined selects GET.
 * @returns {Promise<object>} Decoded response; transport/HTTP errors are displayed by the owning action.
 */
async function request(path, body) {
  return requestJson(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
}
const cleanup = new MapCleanupController($('atlas-storage'), {
  localeContext:{locale:'ru',availableLocales:['ru','en'],fallbackLocale:'ru'},
  onCatalog:data=>{
    $('storage-path').textContent=data.mapsPath||data.dataMapsPath||data.storagePath;
    const total=Number.isFinite(data.totalMapBytes)?new Intl.NumberFormat('ru',{maximumFractionDigits:2}).format(data.totalMapBytes/1048576)+' MiB':'неизвестно';
    $('total-size').textContent=`Размер строк карт: ${total}`;
  },
});
$('open-storage').addEventListener('click',async()=>{
  try {await request('/api/technical/open-storage',{});}
  catch(error){$('storage-status').textContent=error.message;}
});
const checkoutSettings = new CheckoutSettings($('atlas-temporary-checkouts'), {
  localeContext:{locale:'ru',availableLocales:['ru','en'],fallbackLocale:'ru'},
});
window.addEventListener('pagehide',event=>{
  if(!event.persisted){cleanup.dispose();checkoutSettings.dispose();}
});
const query = new URLSearchParams(location.search);
const projectId = query.get('projectId') || query.get('repo') || null;
if (projectId) $('agent-guide-link').href = '/agent-setup.md?' + new URLSearchParams({projectId});
new AgentSetupPanel($('technical-agent-setup'), {getContext: () => ({projectId})});
await cleanup.start();
await checkoutSettings.start();
