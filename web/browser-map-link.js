import { navigationSnapshot, writeRoute } from './navigation-history.js';

const PREFIX = '#atlas-map=', MAX_BYTES = 65536, MAX_ID = 12000;

/** Adjacent occurrence IDs share long caller prefixes; encode only their suffixes for browser handoff. */
function packIds(ids) {
  let previous = '';
  return ids.map(id => {
    let prefix = 0;
    while (prefix < previous.length && prefix < id.length && previous[prefix] === id[prefix]) prefix++;
    const packed = [prefix, id.slice(prefix)]; previous = id; return packed;
  });
}

/** Validate prefix compression before normalizing the restored navigation state. */
function unpackIds(rows, limit, allowEmpty = false) {
  if (!Array.isArray(rows) || rows.length > limit) throw new Error('Invalid navigation collection');
  let previous = '';
  return rows.map(row => {
    if (!Array.isArray(row) || row.length !== 2 || !Number.isInteger(row[0]) || row[0] < 0
      || row[0] > previous.length || typeof row[1] !== 'string') throw new Error('Invalid occurrence prefix');
    const id = previous.slice(0, row[0]) + row[1];
    if ((!allowEmpty && !id) || id.length > MAX_ID) throw new Error('Invalid occurrence ID');
    previous = id; return id;
  });
}

/** Compress repeated placement addresses while retaining exact visible geometry and camera alignment. */
function packPlacements(records) {
  return { ids: packIds(records.flatMap(record => [record.id, record.parentId, record.targetId || ''])),
    boxes: records.map(({ kind, x, y, width, height, footprints }) => [kind, x, y, width, height, footprints]) };
}

/** Geometry is subsequently validated by navigationSnapshot; malformed tuples cannot allocate unbounded arrays. */
function unpackPlacements(packed) {
  if (!packed || !Array.isArray(packed.boxes) || packed.boxes.length > 48) throw new Error('Invalid placement collection');
  const ids = unpackIds(packed.ids, 144, true);
  if (ids.length !== packed.boxes.length * 3) throw new Error('Invalid placement addresses');
  return packed.boxes.map((box, index) => {
    if (!Array.isArray(box) || box.length !== 6) throw new Error('Invalid placement tuple');
    const [kind, x, y, width, height, footprints] = box;
    return { id: ids[index * 3], parentId: ids[index * 3 + 1], targetId: ids[index * 3 + 2] || null,
      kind, x, y, width, height, footprints };
  });
}

/** Carry filters, camera and disclosures in a bounded fragment; source text and API data are never included. */
export function browserMapURL(href, value) {
  const snapshot = navigationSnapshot(value);
  const path = packIds(snapshot.pathExpanded), inline = packIds(snapshot.inlineExpanded);
  const methodPlacements = packPlacements(snapshot.methodPlacements), inlineMethodPlacements = packPlacements(snapshot.inlineMethodPlacements);
  snapshot.pathExpanded = []; snapshot.inlineExpanded = [];
  // Geometry addresses are packed separately; transferring them keeps the destination camera on the same cards.
  snapshot.methodPlacements = []; snapshot.inlineMethodPlacements = [];
  const bytes = new TextEncoder().encode(JSON.stringify({ version: 1, snapshot, path, inline, methodPlacements, inlineMethodPlacements }));
  if (bytes.length > MAX_BYTES) throw new Error('Слишком много раскрытых деталей для ссылки. Сверните часть методов и повторите.');
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  const url = new URL(writeRoute(snapshot.route, href));
  url.hash = PREFIX + btoa(binary); return url.href;
}

/** Malformed or foreign-version fragments fall back to the ordinary map URL without executing content. */
export function readBrowserMapURL(href) {
  const url = new URL(href);
  if (!url.hash.startsWith(PREFIX)) return null;
  try {
    const encoded = url.hash.slice(PREFIX.length);
    if (encoded.length > MAX_BYTES * 1.4) return null;
    const binary = atob(encoded);
    const payload = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Uint8Array.from(binary, char => char.charCodeAt(0))));
    if (payload.version !== 1 || !payload.snapshot) return null;
    const snapshot = navigationSnapshot({ ...payload.snapshot,
      pathExpanded: unpackIds(payload.path, 600), inlineExpanded: unpackIds(payload.inline, 320),
      methodPlacements: unpackPlacements(payload.methodPlacements), inlineMethodPlacements: unpackPlacements(payload.inlineMethodPlacements) });
    url.hash = ''; return { snapshot, href: url.href };
  } catch { return null; }
}

/** Prepare the current map on actual link activation; normal new-tab links also open the OS browser in desktop mode. */
export function bindBrowserMapLink(link, getSnapshot, browser, onError) {
  link.href = browser.location.href;
  const prepare = event => {
    try { link.href = browserMapURL(browser.location.href, getSnapshot()); }
    catch (error) { event.preventDefault(); onError(error.message); }
  };
  link.addEventListener('click', prepare);
  link.addEventListener('auxclick', prepare);
}
