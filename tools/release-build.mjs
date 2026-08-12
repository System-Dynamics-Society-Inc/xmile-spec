/**
 * Render one tag's specification to a single self-contained HTML file.
 *
 * Differs from tools/build.mjs in one respect: data-uri embeds the figures in
 * the page, so the release asset is one file a reader can download and open
 * rather than an HTML that silently loses its images without a sibling
 * images/ directory.
 *
 * Usage: node tools/release-build.mjs <source.adoc> <out.html>
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import asciidoctor from '@asciidoctor/core';

const Asciidoctor = asciidoctor();

const source = process.argv[2];
const outFile = process.argv[3];

if (!source || !outFile) {
  console.error('usage: node release-build.mjs <source.adoc> <out.html>');
  process.exit(2);
}
if (!fs.existsSync(source)) {
  console.error(`release-build: source not found: ${source}`);
  process.exit(2);
}

const logger = Asciidoctor.MemoryLogger.create();
Asciidoctor.LoggerManager.setLogger(logger);

fs.mkdirSync(path.dirname(path.resolve(outFile)), { recursive: true });
Asciidoctor.convertFile(source, {
  safe: 'unsafe',
  to_file: path.resolve(outFile),
  standalone: true,
  mkdirs: true,
  attributes: {
    'data-uri': '',
    imagesdir: path.join(path.dirname(path.resolve(source)), 'images'),
  },
});

const messages = logger.getMessages();
const problems = messages.filter((m) => m.severity !== 'DEBUG' && m.severity !== 'INFO');

for (const m of messages) {
  const text = typeof m.message === 'string' ? m.message : (m.message.text ?? JSON.stringify(m.message));
  const loc = typeof m.message === 'object' && m.message.source_location
    ? `${m.message.source_location.getPath()}:${m.message.source_location.getLineNumber()}: `
    : '';
  console.log(`[${m.severity}] ${loc}${text}`);
}

if (problems.length > 0) {
  console.error(`\nrelease-build FAILED: ${problems.length} problem(s); see above.`);
  process.exit(1);
}

const bytes = fs.statSync(outFile).size;
const embedded = fs.readFileSync(outFile, 'utf8').split('data:image/').length - 1;
console.log(`release-build OK -> ${outFile} (${(bytes / 1024).toFixed(0)} KiB, ${embedded} embedded image(s))`);
