/**
 * Run tools/check_examples.py through whichever Python this machine calls
 * Python, so that checking the specification's examples looks like everything
 * else here:
 *
 *   npm run lint
 *   npm run build
 *   npm run validate
 *   npm run examples
 *
 * and so that a missing interpreter or a missing package produces one clear
 * sentence instead of a spawn error. The check is Python for the same reason
 * validation is: the schema is XSD 1.1, which no Node validator implements.
 *
 * Usage:
 *   npm run examples                     check every example in spec/
 *   npm run examples -- --verbose        list each example and known exception
 *   node tools/check_examples.mjs        same thing without npm
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const windows = process.platform === 'win32';
const binDir = windows ? 'Scripts' : 'bin';
const exe = windows ? 'python.exe' : 'python';

// An activated virtualenv, then a .venv beside the repo, then the system
// interpreter. Installing xmlschema into a venv is the tidiest way to get it,
// so looking there first means `npm run examples` finds it without ceremony.
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

  const hasPackage = probe(cmd, prefix, 'import xmlschema');
  if (hasPackage.status === 0) {
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
      ? 'examples FAILED: Python 3 is installed but the xmlschema package is not.\n'
        + `Install it with:  ${how} -m pip install -r tools/requirements.txt`
      : 'examples FAILED: no Python 3 interpreter found.\n'
        + 'The schema is XSD 1.1, which no Node validator implements; checking the\n'
        + 'examples needs Python 3 plus the xmlschema package. See tools/check_examples.py.',
  );
  process.exit(2);
}

const [cmd, prefix] = interpreter;
const run = spawnSync(cmd, [...prefix, 'tools/check_examples.py', ...process.argv.slice(2)], {
  stdio: 'inherit',
  windowsHide: true,
});

process.exit(run.status ?? 2);
