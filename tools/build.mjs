/**
 * Render the specification to HTML and fail on any Asciidoctor diagnostic.
 *
 * Treating warnings as errors is the point of the exercise: an unresolved
 * <<sec-...>> reference is a warning, so a section that gets deleted without
 * its inbound references being fixed now breaks the build instead of shipping
 * as a dead pointer -- which is exactly what happened between 1.0 and 1.1.
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import asciidoctor from '@asciidoctor/core';

const Asciidoctor = asciidoctor();

const source = process.argv[2] ?? 'spec/xmile.adoc';
const outFile = process.argv[3] ?? 'build/xmile.html';

if (!fs.existsSync(source)) {
  console.error(`build: source not found: ${source}`);
  process.exit(2);
}

const logger = Asciidoctor.MemoryLogger.create();
Asciidoctor.LoggerManager.setLogger(logger);

fs.mkdirSync(path.dirname(outFile), { recursive: true });
Asciidoctor.convertFile(source, {
  safe: 'unsafe',
  to_file: path.resolve(outFile),
  standalone: true,
  mkdirs: true,
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

// Copy images next to the output so the page renders standalone.
const imagesSrc = path.join(path.dirname(source), 'images');
if (fs.existsSync(imagesSrc)) {
  const imagesOut = path.join(path.dirname(outFile), 'images');
  fs.mkdirSync(imagesOut, { recursive: true });
  for (const f of fs.readdirSync(imagesSrc)) {
    fs.copyFileSync(path.join(imagesSrc, f), path.join(imagesOut, f));
  }
}

if (problems.length > 0) {
  console.error(`\nbuild FAILED: ${problems.length} problem(s); see above.`);
  process.exit(1);
}

console.log(`build OK -> ${outFile}`);
