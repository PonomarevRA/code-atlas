/** Packages the author-maintained wiki for the static reader; --check detects drift without writes. */
import {lstat, readFile, readdir, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {execFileSync} from 'node:child_process';

const root = fileURLToPath(new URL('../', import.meta.url));
const source = path.join(root, 'docs/wiki');
const reviewedAt = '2026-10-28';
const preferred = ['Home', 'Architecture', 'Walkthrough-Map', 'Chunked-Analysis', 'Walkthrough-Agents', 'Walkthrough-Tests', 'Development-Guide', 'Code-Patterns', 'Pattern-Selection', 'Modular-Code', 'MCP-Compatible-Updates', 'Mods-and-Wiki'];
for (const directory of [path.join(root, 'docs'), source, path.join(root, 'web')]) {
  const status = await lstat(directory);
  if (!status.isDirectory() || status.isSymbolicLink()) throw new Error('Wiki source must be an ordinary project directory');
}
// Both readers use one projection. Run it only after source/output boundary checks.
const target = path.join(root, 'web/project-wiki.json');
try {
  const status = await lstat(target);
  if (!status.isFile() || status.isSymbolicLink()) throw new Error('Wiki bundle target must be an ordinary file');
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
}
const patternArguments = ['-m', 'atlas.code_patterns.wiki'];
if (process.argv.includes('--check')) patternArguments.push('--check');
execFileSync(process.env.ATLAS_WIKI_PYTHON || 'python3', patternArguments, {cwd: root, stdio: 'inherit'});
const names = (await readdir(source)).filter(name => /^[A-Za-z][A-Za-z0-9-]*\.md$/.test(name));
names.sort((a, b) => {
  const position = name => preferred.includes(name.slice(0, -3)) ? preferred.indexOf(name.slice(0, -3)) : preferred.length;
  return position(a) - position(b) || a.localeCompare(b, 'en');
});
const pages = [];
for (const name of names) {
  const filename = path.join(source, name);
  const status = await lstat(filename);
  if (!status.isFile() || status.isSymbolicLink() || status.size > 100000) throw new Error(`Invalid wiki source: ${name}`);
  const text = await readFile(filename, 'utf8');
  const title = text.match(/^#\s+(.+)$/m)?.[1];
  if (!title || Buffer.byteLength(text) > 100000) throw new Error(`Invalid wiki page: ${name}`);
  const pageReviewedAt = text.match(/^Сверено по исходникам: (\d{4}-\d{2}-\d{2})\.$/m)?.[1];
  pages.push({id: name.slice(0, -3), title, source: `docs/wiki/${name}`, text,
    ...(pageReviewedAt ? {reviewedAt: pageReviewedAt} : {})});
}
// A package refresh does not claim that every legacy page was reviewed that day.
const reviewedScope = pages.filter(page => page.reviewedAt === reviewedAt).map(page => page.id);
const bundle = JSON.stringify({version: 1, reviewedAt, reviewedScope, pages}, null, 2) + '\n';
if (process.argv.includes('--check')) {
  if (await readFile(target, 'utf8') !== bundle) throw new Error('Wiki bundle is stale. Run node scripts/build_project_wiki.mjs');
} else await writeFile(target, bundle);
console.log(`Wiki: ${pages.length} pages ${process.argv.includes('--check') ? 'verified' : 'packaged'}`);
