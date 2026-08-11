/**
 * Guard against the failure that damaged version 1.1.
 *
 * In the Word original every "Section 6.4.2" was ordinary typed text, so
 * nothing could tell that a referenced section had been deleted or renumbered.
 * Version 1.1 shipped with 19 references to sections that no longer existed
 * and 13 more that silently pointed at different content.
 *
 * Here every reference should be a real <<anchor>> cross-reference, which
 * Asciidoctor validates and renumbers. This check covers the other half of the
 * gap: a reference typed as literal text is invisible to the build, so it is
 * rejected unless it is listed in tools/known-literal-xrefs.tsv.
 *
 * That file is not a way to silence the check. It records references whose
 * target 1.1 deleted, so there is nothing to point at until the TC decides
 * whether to drop the requirement or restore the section. Each entry is
 * described in ERRATA-v1.1.adoc. Anything not already listed fails.
 *
 * Usage:
 *   node tools/lint.mjs [specDir]            check
 *   node tools/lint.mjs [specDir] --update   rewrite the allowlist
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const args = process.argv.slice(2);
const update = args.includes('--update');
const specDir = args.find((a) => !a.startsWith('--')) ?? 'spec';
const allowPath = path.join('tools', 'known-literal-xrefs.tsv');

// Verbatim regions are exempt: text inside them is sample code, not prose.
const VERBATIM_SPAN = /``\+[\s\S]*?\+``|`\++[\s\S]*?\++`|pass:c\[(?:\\\]|[^\]])*\]/g;
const LITERAL_REF = /\bSections?\s+\d+(?:\.\d+)*/gi;

const blankOut = (line) => line.replace(VERBATIM_SPAN, (m) => ' '.repeat(m.length));

/** @returns {Map<string, {count: number, lines: number[]}>} keyed "file\tref" */
function findLiteralRefs(dir) {
  const found = new Map();
  for (const file of fs.readdirSync(dir).filter((f) => f.endsWith('.adoc')).sort()) {
    const lines = fs.readFileSync(path.join(dir, file), 'utf8').split('\n');
    let inListing = false;
    lines.forEach((line, index) => {
      if (line.startsWith('----')) {
        inListing = !inListing;
        return;
      }
      if (inListing) return;
      for (const match of blankOut(line).matchAll(LITERAL_REF)) {
        const key = `${file}\t${match[0]}`;
        const entry = found.get(key) ?? { count: 0, lines: [] };
        entry.count += 1;
        entry.lines.push(index + 1);
        found.set(key, entry);
      }
    });
  }
  return found;
}

function readAllowlist() {
  const allowed = new Map();
  if (!fs.existsSync(allowPath)) return allowed;
  for (const raw of fs.readFileSync(allowPath, 'utf8').split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const [file, ref, count] = line.split('\t');
    allowed.set(`${file}\t${ref}`, Number(count));
  }
  return allowed;
}

const found = findLiteralRefs(specDir);

if (update) {
  const header = [
    '# References that 1.1 left pointing at sections it deleted, so there is',
    '# nothing to link to. Each is described in ERRATA-v1.1.adoc; they are',
    '# listed here so the check still fails on any NEW literal reference.',
    '# Regenerate with: npm run lint -- --update',
    '#',
    '# file\treference\tcount',
  ].join('\n');
  const rows = [...found.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, { count }]) => `${key}\t${count}`);
  fs.mkdirSync(path.dirname(allowPath), { recursive: true });
  fs.writeFileSync(allowPath, `${header}\n${rows.join('\n')}\n`, 'utf8');
  console.log(`wrote ${allowPath} with ${rows.length} entr(ies)`);
  process.exit(0);
}

const allowed = readAllowlist();
let failures = 0;

for (const [key, { count, lines }] of [...found.entries()].sort()) {
  const [file, ref] = key.split('\t');
  const budget = allowed.get(key) ?? 0;
  if (count > budget) {
    console.log(
      `${file}:${lines.join(',')}: literal cross-reference "${ref}" `
      + `(${count} occurrence(s), ${budget} recorded) — use <<sec-...>> so the `
      + 'build can verify it',
    );
    failures += 1;
  }
}

const stale = [...allowed.keys()].filter((k) => (found.get(k)?.count ?? 0) < allowed.get(k));
if (stale.length > 0) {
  console.log(
    `\nnote: ${stale.length} allowlist entr(ies) no longer needed — `
    + 'rerun with --update to shrink the list.',
  );
}

if (failures > 0) {
  console.error(`\nlint FAILED: ${failures} unrecorded literal cross-reference(s).`);
  process.exit(1);
}
console.log(
  `lint OK: no unrecorded literal cross-references `
  + `(${allowed.size} known, see ERRATA-v1.1.adoc).`,
);
