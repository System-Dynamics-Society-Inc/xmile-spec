/**
 * Run tools/redline.py through whichever Python this machine calls Python, so
 * that producing a redline looks like everything else here:
 *
 *   npm run lint
 *   npm run build
 *   npm run validate
 *   npm run verify
 *   npm run redline
 *
 * and so that a missing interpreter produces one clear sentence instead of a
 * spawn error. Unlike verify, this needs nothing beyond the standard library.
 *
 * Usage:
 *   npm run redline                                compare HEAD against origin/main
 *   npm run redline -- --base v1.1                 compare against a tag
 *   npm run redline -- --out build/tc-review.html  choose the output file
 *   node tools/redline.mjs --base v1.1             same thing without npm
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const windows = process.platform === 'win32';
const binDir = windows ? 'Scripts' : 'bin';
const exe = windows ? 'python.exe' : 'python';

// An activated virtualenv, then a .venv beside the repo, then the system
// interpreter. Installing the dependencies into a venv is the tidiest way to
// get them, so looking there first means `npm run redline` finds them.
const venvs = [process.env.VIRTUAL_ENV, '.venv', 'venv']
  .filter(Boolean)
  .map((dir) => path.join(dir, binDir, exe))
  .filter((candidate) => fs.existsSync(candidate));

// py -3 before a bare `python` on Windows: the launcher picks a real
// interpreter, while `python` there is often the App Execution Alias stub that
// only opens the Microsoft Store.
const system = windows
  ? [['py', ['-3']], ['python', []], ['python3', []]]
  : [['python3', []], ['python', []]];

const CANDIDATES = [...venvs.map((p) => [p, []]), ...system];

const probe = (cmd, prefix, code) =>
  spawnSync(cmd, [...prefix, '-c', code], { encoding: 'utf8', windowsHide: true });

let interpreter = null;
let sawInterpreterWithoutPackage = false;

for (const [cmd, prefix] of CANDIDATES) {
  const found = probe(cmd, prefix, 'import sys; print(sys.version_info[0])');
  if (found.status !== 0 || found.stdout.trim() !== '3') continue;

  if (probe(cmd, prefix, 'import difflib, html').status === 0) {
    interpreter = [cmd, prefix];
    break;
  }
  sawInterpreterWithoutPackage = true;
}

if (!interpreter) {
  const [cmd, prefix] = system[0];
  const how = [cmd, ...prefix].join(' ');
  console.error(
    sawInterpreterWithoutPackage
      ? 'redline FAILED: Python 3 is installed but the standard library probe failed.\n'
        + `Install it with:  ${how} -m pip install -r tools/requirements.txt`
      : 'redline FAILED: no Python 3 interpreter found.\n'
        + 'Verifying the ed25519 signatures needs Python 3 plus the cryptography\n'
        + 'package. See tools/redline.py.',
  );
  process.exit(2);
}

const [cmd, prefix] = interpreter;
const run = spawnSync(cmd, [...prefix, 'tools/redline.py', ...process.argv.slice(2)], {
  stdio: 'inherit',
  windowsHide: true,
});

process.exit(run.status ?? 2);
