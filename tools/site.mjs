/**
 * Build the specification website.
 *
 * The GitHub release page is a serviceable place to read one release and a poor
 * place to move between them: no prior or next link, no way to see a draft
 * alongside the standard it will replace, and a URL nobody can cite from a
 * committee paper. This produces the same content at stable, citable paths.
 *
 * Layout of the output, all internal links relative so the tree can be served
 * from any prefix:
 *
 *   index.html          redirect to whichever release is current
 *   1.0/index.html      release page, plus its specification asset
 *   1.1/index.html      ...
 *   1.2/index.html      a release under review, plus a link to its redline
 *
 * Content comes from site/releases.json and the AsciiDoc under site/notes.
 * Assets are built from each release's git ref, so what the site serves is
 * whatever that tag actually says, not a file someone remembered to update.
 *
 * Usage:
 *   node tools/site.mjs [--out build/site] [--skip-assets]
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import asciidoctor from '@asciidoctor/core';

const Asciidoctor = asciidoctor();

const args = process.argv.slice(2);
const outRoot = argValue('--out') ?? path.join('build', 'site');
const skipAssets = args.includes('--skip-assets');

function argValue(flag) {
  const index = args.indexOf(flag);
  return index >= 0 ? args[index + 1] : null;
}

const config = JSON.parse(fs.readFileSync(path.join('site', 'releases.json'), 'utf8'));
const releases = config.releases;
const current = releases.find((r) => r.status === 'current') ?? releases[releases.length - 1];

const STATUS = {
  current: { label: 'Current release', tone: 'current' },
  'under-review': { label: 'Under review', tone: 'review' },
  superseded: { label: 'Superseded', tone: 'past' },
};

const escape = (s) => String(s).replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function run(cmd, cmdArgs) {
  return execFileSync(cmd, cmdArgs, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
}

/** A checkout of `ref`, or the working tree when the ref is HEAD. */
function withTree(ref, fn) {
  if (ref === 'HEAD') return fn('.');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'site-'));
  const tree = path.join(dir, 'tree');
  try {
    run('git', ['worktree', 'add', '--detach', tree, ref]);
    return fn(tree);
  } finally {
    try { run('git', ['worktree', 'remove', '--force', tree]); } catch { /* already gone */ }
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function buildAssets(release, dir) {
  withTree(release.ref, (tree) => {
    for (const asset of release.assets) {
      const source = asset.source === 'errata'
        ? path.join(tree, asset.sourcePath)
        : path.join(tree, 'spec', 'xmile.adoc');
      if (!fs.existsSync(source)) {
        console.warn(`  ! ${release.version}: ${source} not found, skipping ${asset.file}`);
        asset.missing = true;
        continue;
      }
      run('node', ['tools/release-build.mjs', source, path.join(dir, asset.file)]);
      asset.bytes = fs.statSync(path.join(dir, asset.file)).size;
      console.log(`  ${release.version}/${asset.file} (${Math.round(asset.bytes / 1024)} KiB)`);
    }
  });
}

function buildRedline(release, dir) {
  const out = path.join(dir, 'redline.html');
  // redline.py drives its own worktree for the base revision.
  run('node', ['tools/redline.mjs', '--base', release.reviewBase, '--out', out,
    '--title', `XMILE ${release.reviewBase.replace(/^v/, '')} to ${release.version} — redline`]);
  release.redlineBytes = fs.statSync(out).size;
  console.log(`  ${release.version}/redline.html (${Math.round(release.redlineBytes / 1024)} KiB)`);
}

function renderNotes(release) {
  const file = path.join('site', 'notes', release.notes);
  return Asciidoctor.convert(fs.readFileSync(file, 'utf8'), {
    safe: 'safe',
    attributes: { showtitle: false, sectids: false, 'source-highlighter': null },
  });
}

const CSS = `
:root{color-scheme:light dark;
--bg:#fff;--fg:#16181c;--muted:#5b6570;--rule:#e2e5e9;--soft:#f6f7f9;
--link:#0b5cad;--accent:#0b5cad;
--now-bg:#e8f2ff;--now-fg:#0b4a8f;--rev-bg:#fff4de;--rev-fg:#8a5300;
--past-bg:#f1f2f4;--past-fg:#5b6570}
@media (prefers-color-scheme:dark){:root{
--bg:#15171b;--fg:#e7e9ec;--muted:#9aa4af;--rule:#2a2f36;--soft:#1b1e23;
--link:#7fb6f5;--accent:#7fb6f5;
--now-bg:#12294a;--now-fg:#a8cdff;--rev-bg:#3a2c10;--rev-fg:#ffd489;
--past-bg:#22262c;--past-fg:#9aa4af}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:var(--link);text-decoration-thickness:.06em;text-underline-offset:.15em}
.skip{position:absolute;left:-9999px}
.skip:focus{left:.5rem;top:.5rem;background:var(--bg);padding:.5rem;z-index:9}
header.site{border-bottom:1px solid var(--rule);background:var(--soft)}
.bar{max-width:54rem;margin:0 auto;padding:1.1rem 1.25rem;display:flex;
flex-wrap:wrap;gap:.4rem 1rem;align-items:baseline}
.bar .name{font-weight:600;letter-spacing:-.01em}
.bar .name a{color:inherit;text-decoration:none}
.bar .tagline{color:var(--muted);font-size:.88rem}
nav.versions{max-width:54rem;margin:0 auto;padding:0 1.25rem 1rem;
display:flex;flex-wrap:wrap;gap:.4rem}
nav.versions a,nav.versions span{display:inline-flex;align-items:center;gap:.4rem;
border:1px solid var(--rule);border-radius:999px;padding:.25rem .75rem;
font-size:.87rem;text-decoration:none;color:var(--fg);background:var(--bg)}
nav.versions .here{background:var(--accent);border-color:var(--accent);color:var(--bg);
font-weight:600}
nav.versions .dot{width:.45rem;height:.45rem;border-radius:50%}
.dot.current{background:#1f8a4c}.dot.review{background:#c98a00}
.dot.past{background:#9aa4af}
nav.versions .here .dot{background:currentColor}
main{max-width:54rem;margin:0 auto;padding:2.25rem 1.25rem 5rem}
.badge{display:inline-block;font-size:.78rem;font-weight:600;letter-spacing:.03em;
text-transform:uppercase;border-radius:999px;padding:.2rem .6rem}
.badge.current{background:var(--now-bg);color:var(--now-fg)}
.badge.review{background:var(--rev-bg);color:var(--rev-fg)}
.badge.past{background:var(--past-bg);color:var(--past-fg)}
h1{font-size:1.75rem;line-height:1.2;margin:.6rem 0 .2rem;letter-spacing:-.02em}
.desig{color:var(--muted);margin:0 0 1.75rem}
.callout{border:1px solid var(--rule);border-left:4px solid var(--accent);
border-radius:0 8px 8px 0;background:var(--soft);padding:1rem 1.15rem;margin:0 0 2rem}
.callout.review{border-left-color:#c98a00}
.callout h2{font-size:1rem;margin:0 0 .4rem}
.callout p{margin:0 0 .6rem}.callout p:last-child{margin:0}
main h2{font-size:1.15rem;margin:2.25rem 0 .6rem;padding-top:1.25rem;
border-top:1px solid var(--rule);letter-spacing:-.01em}
main h3{font-size:1rem;margin:1.5rem 0 .4rem}
main :is(p,ul,ol){margin:0 0 1rem}
main ul{padding-left:1.2rem}
main li{margin:0 0 .4rem}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.88em;
background:var(--soft);border:1px solid var(--rule);border-radius:4px;padding:.05em .3em}
table{border-collapse:collapse;width:100%;margin:0 0 1.25rem;font-size:.94rem;display:block;
overflow-x:auto}
th,td{border:1px solid var(--rule);padding:.45rem .6rem;text-align:left;vertical-align:top}
th{background:var(--soft);font-weight:600}
.files{list-style:none;padding:0;margin:0}
.files li{border:1px solid var(--rule);border-radius:8px;padding:.85rem 1rem;
margin:0 0 .6rem;background:var(--soft)}
.files .row{display:flex;flex-wrap:wrap;gap:.5rem 1rem;align-items:baseline}
.files a{font-weight:600}
.files .size{color:var(--muted);font-size:.85rem}
.files .note{color:var(--muted);font-size:.9rem;margin:.25rem 0 0}
.pager{display:flex;flex-wrap:wrap;gap:1rem;justify-content:space-between;
margin:3rem 0 0;padding-top:1.25rem;border-top:1px solid var(--rule);font-size:.92rem}
.pager .none{color:var(--muted)}
footer.site{border-top:1px solid var(--rule);background:var(--soft);
color:var(--muted);font-size:.85rem}
footer.site .bar{display:block;padding:1.25rem}
footer.site p{margin:0 0 .35rem}
@media print{header.site,nav.versions,.pager,footer.site{display:none}
main{max-width:none;padding:0}body{font-size:11pt}}
`;

function versionNav(active) {
  const items = releases.map((r) => {
    const tone = STATUS[r.status].tone;
    const label = `XMILE ${escape(r.version)}`;
    const dot = `<span class="dot ${tone}" aria-hidden="true"></span>`;
    if (r.version === active) {
      return `<span class="here" aria-current="page">${dot}${label}</span>`;
    }
    return `<a href="../${escape(r.version)}/">${dot}${label}</a>`;
  });
  return `<nav class="versions" aria-label="Releases">${items.join('')}</nav>`;
}

function pager(index) {
  const older = releases[index - 1];
  const newer = releases[index + 1];
  const left = older
    ? `<a href="../${escape(older.version)}/">&larr; XMILE ${escape(older.version)}
       <span class="size">${escape(STATUS[older.status].label)}</span></a>`
    : '<span class="none">&larr; no earlier release</span>';
  const right = newer
    ? `<a href="../${escape(newer.version)}/">XMILE ${escape(newer.version)}
       <span class="size">${escape(STATUS[newer.status].label)}</span> &rarr;</a>`
    : '<span class="none">no later release &rarr;</span>';
  return `<div class="pager">${left}${right}</div>`;
}

function kib(bytes) {
  return bytes ? `${Math.round(bytes / 1024)} KiB` : '';
}

function releasePage(release, index) {
  const status = STATUS[release.status];
  const notes = renderNotes(release);
  const review = release.status === 'under-review';

  const callout = review
    ? `<div class="callout review">
         <h2>This version is under review</h2>
         <p>XMILE ${escape(release.version)} is a working draft. It is not approved
            and it is not the current standard. The current standard is
            <a href="../${escape(current.version)}/">XMILE ${escape(current.version)}</a>.</p>
         <p><a href="redline.html"><strong>Read the redline against
            ${escape(release.reviewBase.replace(/^v/, ''))}</strong></a>${
              release.redlineBytes ? ` <span class="size">(${kib(release.redlineBytes)})</span>` : ''
            } &mdash; every change marked, grouped by section.</p>
       </div>`
    : release.status === 'superseded'
      ? `<div class="callout">
           <h2>Superseded</h2>
           <p>A later version of this specification exists. Unless you have a
              reason to work from this one, use
              <a href="../${escape(current.version)}/">XMILE ${escape(current.version)}</a>.</p>
         </div>`
      : '';

  const files = release.assets.filter((a) => !a.missing).map((asset) => `
    <li><div class="row"><a href="${escape(asset.file)}">${escape(asset.label)}</a>
      <span class="size">${escape(asset.file)}${asset.bytes ? ` &middot; ${kib(asset.bytes)}` : ''}</span></div>
      ${asset.note ? `<p class="note">${escape(asset.note)}</p>` : ''}</li>`).join('');

  const redlineFile = review && release.redlineBytes ? `
    <li><div class="row"><a href="redline.html">Redline against ${escape(release.reviewBase.replace(/^v/, ''))}</a>
      <span class="size">redline.html &middot; ${kib(release.redlineBytes)}</span></div>
      <p class="note">Every change against the previous version, marked and grouped by section.</p></li>` : '';

  return `<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>XMILE ${escape(release.version)} &mdash; ${escape(config.title)}</title>
<meta name="description" content="XMILE version ${escape(release.version)}, ${escape(release.designation)}, ${escape(release.date)}.">
<link rel="canonical" href="${escape(config.canonical)}/${escape(release.version)}/">
<style>${CSS}</style>
<a class="skip" href="#main">Skip to content</a>
<header class="site">
  <div class="bar">
    <span class="name"><a href="../">${escape(config.title)}</a></span>
    <span class="tagline">${escape(config.tagline)}</span>
  </div>
  ${versionNav(release.version)}
</header>
<main id="main">
  <span class="badge ${status.tone}">${escape(status.label)}</span>
  <h1>XMILE Version ${escape(release.version)}</h1>
  <p class="desig">${escape(release.designation)} &mdash; ${escape(release.date)}</p>
  ${callout}
  ${notes}
  <h2>Files</h2>
  <ul class="files">${files}${redlineFile}</ul>
  ${pager(index)}
</main>
<footer class="site">
  <div class="bar">
    <p>${escape(config.title)}, maintained by the ${escape(config.steward)}.</p>
    <p>Source and issue tracker: <a href="${escape(config.repository)}">${escape(config.repository)}</a></p>
  </div>
</footer>
</html>
`;
}

function indexPage() {
  const target = `${current.version}/`;
  return `<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escape(config.title)}</title>
<link rel="canonical" href="${escape(config.canonical)}/${escape(target)}">
<meta http-equiv="refresh" content="0; url=${escape(target)}">
<style>${CSS}</style>
<main id="main">
  <h1>${escape(config.title)}</h1>
  <p>Redirecting to the current release, XMILE ${escape(current.version)}.</p>
  <p><a href="${escape(target)}">Continue to XMILE ${escape(current.version)}</a></p>
</main>
<script>location.replace(${JSON.stringify(target)});</script>
</html>
`;
}

// ------------------------------------------------------------------- build --

fs.rmSync(outRoot, { recursive: true, force: true });
fs.mkdirSync(outRoot, { recursive: true });

for (const release of releases) {
  const dir = path.join(outRoot, release.version);
  fs.mkdirSync(dir, { recursive: true });
  if (!skipAssets) {
    buildAssets(release, dir);
    if (release.status === 'under-review' && release.reviewBase) buildRedline(release, dir);
  }
}

releases.forEach((release, index) => {
  fs.writeFileSync(path.join(outRoot, release.version, 'index.html'),
    releasePage(release, index), 'utf8');
});
fs.writeFileSync(path.join(outRoot, 'index.html'), indexPage(), 'utf8');

const pages = releases.length + 1;
console.log(`site OK -> ${outRoot} (${pages} pages, current is ${current.version})`);
