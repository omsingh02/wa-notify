'use strict';

/** Shared helpers: start a throw-away server in temp dirs on a free port, talk HTTP to it. */

const { spawn } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { DatabaseSync } = require('node:sqlite');

const SERVER_JS = path.join(__dirname, '..', 'server.js');

/**
 * Start server.js with isolated data/config dirs and port 0 (the OS picks a free port).
 * @param {object} [extraEnv]
 * @returns {Promise<{port:number, dataDir:string, configDir:string, dbPath:string, stop:() => Promise<void>, output:() => string, child:object}>}
 */
async function startServer(extraEnv = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-notify-test-'));
  const dataDir = path.join(root, 'data');
  const configDir = path.join(root, 'config');
  const env = {
    ...process.env,
    HOME: root,
    WA_NOTIFY_PORT: '0',
    WA_NOTIFY_DATA_DIR: dataDir,
    WA_NOTIFY_CONFIG_DIR: configDir,
    NODE_NO_WARNINGS: '1',
    ...extraEnv,
  };
  const child = spawn(process.execPath, [SERVER_JS], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  let out = '';
  child.stdout.on('data', (d) => (out += d));
  child.stderr.on('data', (d) => (out += d));

  const port = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill('SIGKILL'); // never leave a stray server behind
      reject(new Error(`server did not start:\n${out}`));
    }, 20000);
    const check = () => {
      const m = out.match(/running on http:\/\/127\.0\.0\.1:(\d+)/);
      if (m) {
        clearTimeout(timer);
        resolve(Number(m[1]));
      }
    };
    child.stdout.on('data', check);
    child.on('exit', (code) => {
      clearTimeout(timer);
      reject(new Error(`server exited (code ${code}) before it was ready:\n${out}`));
    });
  });

  return {
    port,
    dataDir,
    configDir,
    dbPath: path.join(dataDir, 'wa-notify.db'),
    child,
    output: () => out,
    stop: () =>
      new Promise((resolve) => {
        if (child.exitCode !== null) return resolve();
        child.once('exit', () => resolve());
        child.kill('SIGTERM');
        setTimeout(() => child.kill('SIGKILL'), 3000).unref();
      }),
  };
}

/**
 * Minimal HTTP client. `chunks` sends the body in several writes (to split multi-byte characters).
 * @returns {Promise<{status:number, headers:object, body:string, json:()=>any}>}
 */
function request(port, { method = 'GET', path: urlPath = '/', headers = {}, body = null, chunks = null, gapMs = 120 } = {}) {
  return new Promise((resolve, reject) => {
    const parts = chunks || (body === null ? [] : [Buffer.isBuffer(body) ? body : Buffer.from(String(body))]);
    const total = parts.reduce((n, p) => n + p.length, 0);
    const reqHeaders = { ...headers };
    if (parts.length) reqHeaders['Content-Length'] = total;
    const req = http.request({ host: '127.0.0.1', port, method, path: urlPath, headers: reqHeaders }, (res) => {
      const got = [];
      res.on('data', (d) => got.push(d));
      res.on('end', () => {
        const text = Buffer.concat(got).toString('utf8');
        resolve({ status: res.statusCode, headers: res.headers, body: text, json: () => JSON.parse(text) });
      });
    });
    req.on('error', reject);
    (async () => {
      for (let i = 0; i < parts.length; i++) {
        req.write(parts[i]);
        if (i < parts.length - 1) await new Promise((r) => setTimeout(r, gapMs));
      }
      req.end();
    })();
  });
}

/** POST /log with a JSON body. */
function postLog(port, payload, headers = {}) {
  return request(port, {
    method: 'POST',
    path: '/log',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: typeof payload === 'string' ? payload : JSON.stringify(payload),
  });
}

/** A realistic captured entry. */
function makeEntry(id, overrides = {}) {
  const now = Date.now();
  return {
    capturedAt: now,
    convenience: {
      id,
      chatId: '911234567890@c.us',
      timestamp: Math.floor(now / 1000),
      type: 'chat',
      fromMe: false,
      notifyName: 'Tester',
      body: 'hello',
      ...overrides,
    },
    raw: {},
  };
}

/** Read-only query helper against the server's database. */
function query(dbPath, sql, ...params) {
  const db = new DatabaseSync(dbPath, { readOnly: true });
  try {
    return db.prepare(sql).all(...params);
  } finally {
    db.close();
  }
}

module.exports = { startServer, request, postLog, makeEntry, query, SERVER_JS };
