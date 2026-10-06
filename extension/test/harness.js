'use strict';

/** Load an extension source file into a vm context that stands in for the browser. */

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SRC_DIR = path.join(__dirname, '..', 'src');

/** Run src/<file> inside `sandbox` (which becomes the global object). Returns the sandbox. */
function load(file, sandbox) {
  const code = fs.readFileSync(path.join(SRC_DIR, file), 'utf8');
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: file });
  return sandbox;
}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Poll `condition` until it is truthy or `timeoutMs` passes (then throws). */
async function until(condition, timeoutMs = 8000, stepMs = 10) {
  const start = Date.now();
  while (!(await condition())) {
    if (Date.now() - start > timeoutMs) throw new Error('timed out waiting for condition');
    await delay(stepMs);
  }
}

module.exports = { load, delay, until, SRC_DIR };
