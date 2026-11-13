import { cp, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const packageRoot = fileURLToPath(new URL('./node_modules/pdfjs-dist/', import.meta.url));
const { version } = JSON.parse(await readFile(path.join(packageRoot, 'package.json'), 'utf8'));
if (version !== '6.5.438') throw new Error(`Unexpected PDF.js version: ${version}`);
const destination = fileURLToPath(new URL(`../../static/reader/vendor/pdfjs/${version}/`, import.meta.url));
await mkdir(destination, { recursive: true });
for (const name of ['pdf.min.mjs', 'pdf.worker.min.mjs']) {
  await cp(path.join(packageRoot, 'legacy', 'build', name), path.join(destination, name));
}
for (const name of ['pdf_viewer.mjs', 'pdf_viewer.css', 'images']) {
  await cp(path.join(packageRoot, 'legacy', 'web', name), path.join(destination, name), { recursive: true });
}
for (const name of ['cmaps', 'standard_fonts', 'wasm', 'iccs', 'LICENSE']) {
  await cp(path.join(packageRoot, name), path.join(destination, name), { recursive: true });
}
const hashes = {};
async function hashDirectory(directory, prefix = '') {
  for (const entry of (await readdir(directory, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    if (entry.name === 'SHA256SUMS.json') continue;
    const relative = prefix + entry.name;
    if (entry.isDirectory()) await hashDirectory(path.join(directory, entry.name), `${relative}/`);
    else hashes[relative] = createHash('sha256').update(await readFile(path.join(directory, entry.name))).digest('hex');
  }
}
await hashDirectory(destination);
await writeFile(path.join(destination, 'SHA256SUMS.json'), `${JSON.stringify({ package: 'pdfjs-dist', version, files: hashes }, null, 2)}\n`);
console.log(`Copied PDF.js ${version}: ${Object.keys(hashes).length} files`);
