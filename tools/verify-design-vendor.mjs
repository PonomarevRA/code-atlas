import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
const base = new URL('../web/vendor/ag-design/',import.meta.url);
const manifest = JSON.parse(await readFile(new URL('manifest.json',base)));
for (const [name,digest] of Object.entries(manifest.files)) {
  assert.equal(createHash('sha256').update(await readFile(new URL(name,base))).digest('hex'),digest,name);
}
console.log(`ag-design ${manifest.version}: ${Object.keys(manifest.files).length} assets verified`);
