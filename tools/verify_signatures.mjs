/**
 * Run tools/verify_signatures.py through whichever Python this machine calls
 * Python, so that verifying looks like everything else here:
 *
 *   npm run lint
 *   npm run build
 *   npm run validate
 *   npm run verify
 *
 * and so that a missing interpreter or package produces one clear sentence
 * instead of a spawn error.
 *
 * Usage:
 *   npm run verify                          verify spec/schema/*.xmile
 *   npm run verify -- FILE [FILE...]        verify specific files
 *   npm run verify -- --offline             build messages, skip signatures
 *   node tools/verify_signatures.mjs FILE   same thing without npm
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
// get them, so looking there first means `npm run verify` finds them.
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

  if (probe(cmd, prefix, 'import cryptography').status === 0) {
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
      ? 'verify FAILED: Python 3 is installed but the cryptography package is not.\n'
        + `Install it with:  ${how} -m pip install -r tools/requirements.txt`
      : 'verify FAILED: no Python 3 interpreter found.\n'
        + 'Verifying the ed25519 signatures needs Python 3 plus the cryptography\n'
        + 'package. See tools/verify_signatures.py.',
  );
  process.exit(2);
}

const [cmd, prefix] = interpreter;
const run = spawnSync(cmd, [...prefix, 'tools/verify_signatures.py', ...process.argv.slice(2)], {
  stdio: 'inherit',
  windowsHide: true,
});

process.exit(run.status ?? 2);
