#!/usr/bin/env node
'use strict';

/**
 * wa-notify — local-server/server.js
 *
 * Receives captured WhatsApp messages from the browser extension and stores them in SQLite
 * (node:sqlite, no npm dependencies). Instagram reel/post links found in a message are also
 * written to the `reels` table, which the Python tools in ../tools consume.
 *
 * Properties worth knowing:
 *  - messages are keyed by WhatsApp message id, so re-delivery is a no-op (INSERT OR IGNORE)
 *  - every request is ingested in ONE transaction; if the database fails the client gets HTTP 500
 *    and keeps its queue, so nothing is silently lost
 *  - the freshness flag (`is_likely_live`) is computed here, once, from the extension's capture time
 *  - listens on loopback only; a shared token is paired on first use (trust-on-first-use)
 *
 * Configuration is read from the environment (all optional):
 *   WA_NOTIFY_PORT            listen port                          (default 8765)
 *   WA_NOTIFY_DATA_DIR        database + backup directory          (default ~/.local/share/wa-notify)
 *   WA_NOTIFY_CONFIG_DIR      token directory                      (default ~/.config/wa-notify)
 *   WA_NOTIFY_EXTENSION_ID    extension id allowed in CORS         (default: none)
 *   WA_NOTIFY_JSONL_BACKUP    "0" disables the flat-file backup    (default: enabled)
 *   WA_NOTIFY_BUSY_TIMEOUT_MS SQLite lock wait                     (default 5000)
 *   WA_NOTIFY_DEBUG           "1" logs every request               (default: off)
 */

const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { DatabaseSync } = require('node:sqlite');
const { cleanSender } = require('./lib/sender');
const { findReelUrls, cleanReelUrl, extractReelId, isLikelyLive } = require('./lib/reels');

// ---------------------------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------------------------

const env = process.env;

/** Non-negative integer from the environment, or the fallback. */
function envInt(name, fallback) {
  const raw = env[name];
  if (raw === undefined || raw === '') return fallback;
  const n = Number(raw);
  return Number.isInteger(n) && n >= 0 ? n : fallback;
}

const CONFIG = Object.freeze({
  host: '127.0.0.1', // loopback only; deliberately not configurable
  port: envInt('WA_NOTIFY_PORT', 8765),
  dataDir: env.WA_NOTIFY_DATA_DIR || path.join(os.homedir(), '.local', 'share', 'wa-notify'),
  configDir: env.WA_NOTIFY_CONFIG_DIR || path.join(os.homedir(), '.config', 'wa-notify'),
  extensionId: env.WA_NOTIFY_EXTENSION_ID || '',
  jsonlBackup: env.WA_NOTIFY_JSONL_BACKUP !== '0',
  busyTimeoutMs: envInt('WA_NOTIFY_BUSY_TIMEOUT_MS', 5000),
  debug: env.WA_NOTIFY_DEBUG === '1',
});

const DB_PATH = path.join(CONFIG.dataDir, 'wa-notify.db');
const BACKUP_JSONL = path.join(CONFIG.dataDir, 'chat_archive.jsonl');
const TOKEN_FILE = path.join(CONFIG.configDir, 'token.txt');

const MAX_BODY_BYTES = 10_000_000;
const MAX_ENTRIES_PER_REQUEST = 500;
const TOKEN_MIN_LENGTH = 8;
const DB_OPEN_ATTEMPTS = 20;
const DB_OPEN_RETRY_MS = 250;
const WHATSAPP_ORIGIN = 'https://web.whatsapp.com';
const LOCAL_ORIGIN_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);
const ALLOWED_HOST_HEADER = /^(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/i;

const SCHEMA = `
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT,
    sender TEXT,
    is_from_me INTEGER DEFAULT 0,
    message_ts INTEGER,
    captured_at INTEGER,
    is_likely_live INTEGER DEFAULT 0,
    type TEXT,
    body TEXT,
    caption TEXT,
    link_url TEXT,
    link_title TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS reels (
    reel_id TEXT PRIMARY KEY,
    message_id TEXT REFERENCES messages(id),
    sender TEXT,
    chat_id TEXT,
    url TEXT,
    title TEXT,
    timestamp INTEGER,
    first_seen_at INTEGER,
    is_likely_live INTEGER DEFAULT 0,
    is_downloaded INTEGER DEFAULT 0,
    local_path TEXT,
    is_opened INTEGER DEFAULT 0,
    opened_at INTEGER,
    alerted INTEGER DEFAULT 0,
    summary TEXT,
    summary_status TEXT DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS idx_reels_unopened ON reels (is_opened, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_reels_alert ON reels (alerted, is_likely_live, first_seen_at ASC);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages (message_ts DESC);
`;

const log = (...args) => console.log('[server]', ...args);
const logError = (...args) => console.error('[server]', ...args);

// ---------------------------------------------------------------------------------------------
// Database
// ---------------------------------------------------------------------------------------------

/** Block the thread for `ms` milliseconds (only used while opening the database at startup). */
function sleepSync(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

/**
 * Open the database and apply pragmas. `busy_timeout` MUST come before `journal_mode`: node:sqlite
 * defaults to no busy timeout, and switching to WAL needs a lock that other processes (the Python
 * tools) may briefly hold, which used to crash the server at startup with "database is locked".
 * @returns {DatabaseSync}
 */
function openDatabase() {
  const db = new DatabaseSync(DB_PATH);
  try {
    db.exec(`PRAGMA busy_timeout = ${CONFIG.busyTimeoutMs};`);
    db.exec('PRAGMA journal_mode = WAL;');
    db.exec('PRAGMA synchronous = NORMAL;');
    db.exec(SCHEMA);
  } catch (err) {
    db.close();
    throw err;
  }
  return db;
}

/** openDatabase() with a few retries while another process holds a lock. Throws if it never opens. */
function openDatabaseWithRetry() {
  for (let attempt = 1; ; attempt++) {
    try {
      return openDatabase();
    } catch (err) {
      const locked = /locked|busy/i.test(String(err && err.message));
      if (!locked || attempt >= DB_OPEN_ATTEMPTS) throw err;
      logError(`database busy while opening (attempt ${attempt}/${DB_OPEN_ATTEMPTS}), retrying`);
      sleepSync(DB_OPEN_RETRY_MS);
    }
  }
}

// ---------------------------------------------------------------------------------------------
// Payload handling
// ---------------------------------------------------------------------------------------------

/** @returns {boolean} true for non-null, non-array objects */
function isPlainObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

/** Coerce anything to a string SQLite can bind ('' for null/objects). */
function toText(v) {
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean' || typeof v === 'bigint') return String(v);
  return '';
}

/** Coerce anything to an integer (0 when it is not a finite number). */
function toInt(v) {
  const n = Number(v);
  return Number.isFinite(n) ? Math.trunc(n) : 0;
}

/**
 * Accept {entries:[...]}, a bare array, or a single entry object, and validate the shape.
 * @param {unknown} parsed
 * @returns {{list: object[]} | {error: string}}
 */
function normalizeEntries(parsed) {
  let list;
  if (Array.isArray(parsed)) list = parsed;
  else if (isPlainObject(parsed) && 'entries' in parsed) list = parsed.entries;
  else if (isPlainObject(parsed)) list = [parsed];
  else return { error: 'invalid_payload' };

  if (!Array.isArray(list)) return { error: 'invalid_payload' };
  if (list.length > MAX_ENTRIES_PER_REQUEST) return { error: 'too_many_entries' };
  for (const entry of list) {
    if (!isPlainObject(entry) || !(isPlainObject(entry.convenience) || isPlainObject(entry.raw))) {
      return { error: 'invalid_entry' };
    }
  }
  return { list };
}

/**
 * Flatten one captured entry into the values stored in `messages` (+ the reel links it carries).
 * Every value is coerced, so a malformed field can never make a database bind throw.
 * @param {object} entry
 */
function toRow(entry) {
  const c = isPlainObject(entry.convenience) ? entry.convenience : {};
  const raw = isPlainObject(entry.raw) ? entry.raw : {};

  const rawId = raw.id && (isPlainObject(raw.id) ? raw.id._serialized : raw.id);
  // Without a message id fall back to a content hash: stable across retries, so still idempotent.
  const id =
    toText(c.id) ||
    toText(rawId) ||
    `gen_${crypto.createHash('sha256').update(JSON.stringify(entry)).digest('hex').slice(0, 24)}`;

  const capturedAt = toInt(entry.capturedAt) || Date.now();
  const messageTs = toInt(c.timestamp) || toInt(raw.t) || Math.floor(capturedAt / 1000);
  const live = isLikelyLive(capturedAt, messageTs) ? 1 : 0;
  const sender = cleanSender(c, raw);
  const chatId = toText(c.chatId) || toText(raw.chatId);
  const body = toText(c.body);
  const caption = toText(c.caption);
  const linkUrl = toText(c.linkPreviewUrl);
  const linkTitle = toText(c.linkPreviewTitle);

  const reels = findReelUrls(`${body} ${caption} ${linkUrl}`).map((url) => ({
    reelId: extractReelId(url),
    url: cleanReelUrl(url),
  }));

  return {
    message: [
      id, chatId, sender, c.fromMe ? 1 : 0, messageTs, capturedAt, live,
      toText(c.type) || toText(raw.type) || 'chat', body, caption, linkUrl, linkTitle, JSON.stringify(entry),
    ],
    reelParams: (r) => [r.reelId, id, sender, chatId, r.url, linkTitle, messageTs, capturedAt, live],
    reels,
  };
}

// ---------------------------------------------------------------------------------------------
// Authentication (shared token, trust-on-first-use)
// ---------------------------------------------------------------------------------------------

/**
 * @returns {{state: 'none'} | {state: 'ok', token: string} | {state: 'error', error: Error}}
 * `none` means unpaired. An unreadable token file is an error, never "unpaired" (fail closed).
 */
function readStoredToken() {
  try {
    const token = fs.readFileSync(TOKEN_FILE, 'utf8').trim();
    return token ? { state: 'ok', token } : { state: 'none' };
  } catch (err) {
    return err.code === 'ENOENT' ? { state: 'none' } : { state: 'error', error: err };
  }
}

/** Persist a newly paired token atomically with owner-only permissions. */
function writeToken(token) {
  fs.mkdirSync(CONFIG.configDir, { recursive: true, mode: 0o700 });
  const tmp = `${TOKEN_FILE}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, token, { encoding: 'utf8', mode: 0o600 });
  fs.renameSync(tmp, TOKEN_FILE);
}

/** Constant-time string comparison. */
function safeEqual(a, b) {
  const digest = (s) => crypto.createHash('sha256').update(s).digest();
  return crypto.timingSafeEqual(digest(a), digest(b));
}

/**
 * Check the X-WA-Notify-Token header. While no token is paired, the first request that carries a
 * usable token pairs it; later requests must present exactly that token.
 * @param {http.IncomingMessage} req
 * @returns {{ok: true} | {ok: false, status: number, error: string}}
 */
function authorize(req) {
  const header = req.headers['x-wa-notify-token'];
  const presented = typeof header === 'string' ? header.trim() : '';
  const stored = readStoredToken();

  if (stored.state === 'error') {
    logError('token file exists but cannot be read:', stored.error.message);
    return { ok: false, status: 503, error: 'token_unavailable' };
  }
  if (stored.state === 'ok') {
    return safeEqual(presented, stored.token) ? { ok: true } : { ok: false, status: 401, error: 'unauthorized' };
  }
  if (presented.length >= TOKEN_MIN_LENGTH) {
    try {
      writeToken(presented);
    } catch (err) {
      logError('could not persist the pairing token:', err.message);
      return { ok: false, status: 503, error: 'token_unavailable' };
    }
    log(`paired with client token on first use (saved to ${TOKEN_FILE})`);
  }
  return { ok: true }; // unpaired: accepted, exactly as documented for trust-on-first-use
}

// ---------------------------------------------------------------------------------------------
// HTTP plumbing
// ---------------------------------------------------------------------------------------------

/** @param {string | undefined} host the Host header */
function isAllowedHost(host) {
  return host === undefined || ALLOWED_HOST_HEADER.test(host); // browsers always send Host; rebinding uses a foreign one
}

/** @param {string} origin */
function isAllowedOrigin(origin) {
  if (origin === WHATSAPP_ORIGIN) return true;
  let url;
  try {
    url = new URL(origin);
  } catch {
    return false;
  }
  if (url.protocol === 'chrome-extension:') return CONFIG.extensionId !== '' && url.hostname === CONFIG.extensionId;
  return url.protocol === 'http:' && LOCAL_ORIGIN_HOSTS.has(url.hostname);
}

/** Add CORS headers for known origins only. Requests without an Origin (non-browser) get none. */
function applyCors(req, res) {
  const origin = req.headers.origin;
  if (!origin || !isAllowedOrigin(origin)) return;
  res.setHeader('Access-Control-Allow-Origin', origin);
  res.setHeader('Vary', 'Origin');
  res.setHeader('Access-Control-Allow-Methods', 'POST, GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-WA-Notify-Token');
}

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) });
  res.end(body);
}

/**
 * Read a request body as UTF-8. Chunks are collected as raw bytes and decoded once, so a multi-byte
 * character split across two TCP segments is never corrupted; the size limit counts bytes.
 * @returns {Promise<string>} rejects with code 'too_large' above `limit`
 */
function readBody(req, limit) {
  return new Promise((resolve, reject) => {
    const tooLarge = () => Object.assign(new Error('payload too large'), { code: 'too_large' });
    const declared = Number(req.headers['content-length']);
    if (Number.isFinite(declared) && declared > limit) return reject(tooLarge());

    const chunks = [];
    let size = 0;
    let settled = false;
    const settle = (fn, value) => {
      if (!settled) {
        settled = true;
        fn(value);
      }
    };
    req.on('data', (chunk) => {
      if (settled) return;
      size += chunk.length;
      if (size > limit) return settle(reject, tooLarge());
      chunks.push(chunk);
    });
    req.on('end', () => settle(resolve, Buffer.concat(chunks, size).toString('utf8')));
    req.on('error', (err) => settle(reject, err));
    req.on('close', () => settle(reject, new Error('connection closed before the body was complete')));
  });
}

// ---------------------------------------------------------------------------------------------
// Request handlers
// ---------------------------------------------------------------------------------------------

let db;
let insertMessage;
let insertReel;

/**
 * Store a batch atomically.
 * @param {object[]} entries validated entries
 * @returns {object[]} the entries that were new (not already stored)
 * @throws if the database fails; nothing from the batch is kept in that case
 */
function ingest(entries) {
  const inserted = [];
  db.exec('BEGIN IMMEDIATE');
  try {
    for (const entry of entries) {
      const row = toRow(entry);
      if (insertMessage.run(...row.message).changes > 0) inserted.push(entry);
      for (const reel of row.reels) insertReel.run(...row.reelParams(reel));
    }
    db.exec('COMMIT');
  } catch (err) {
    try {
      db.exec('ROLLBACK');
    } catch {
      /* the connection may already have rolled back */
    }
    throw err;
  }
  return inserted;
}

/** Append newly stored entries to the flat-file backup (never duplicates, never blank lines). */
function appendBackup(entries) {
  if (!CONFIG.jsonlBackup || entries.length === 0) return;
  const lines = `${entries.map((e) => JSON.stringify(e)).join('\n')}\n`;
  fs.appendFile(BACKUP_JSONL, lines, { mode: 0o600 }, (err) => {
    if (err) logError('backup append failed:', err.message);
  });
}

async function handleIngest(req, res) {
  const auth = authorize(req);
  if (!auth.ok) return sendJson(res, auth.status, { error: auth.error });

  let text;
  try {
    text = await readBody(req, MAX_BODY_BYTES);
  } catch (err) {
    if (err.code === 'too_large') {
      res.setHeader('Connection', 'close');
      return sendJson(res, 413, { error: 'payload_too_large' });
    }
    return sendJson(res, 400, { error: 'bad_request' });
  }

  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    return sendJson(res, 400, { error: 'invalid_json' });
  }

  const checked = normalizeEntries(parsed);
  if (checked.error) return sendJson(res, 400, { error: checked.error });

  let inserted;
  try {
    inserted = ingest(checked.list);
  } catch (err) {
    logError('ingest failed, asking the client to retry:', err.message);
    return sendJson(res, 500, { error: 'db_error' });
  }
  appendBackup(inserted);
  return sendJson(res, 200, { ok: true, count: checked.list.length, inserted: inserted.length });
}

async function handleRequest(req, res) {
  if (!isAllowedHost(req.headers.host)) return sendJson(res, 421, { error: 'misdirected_request' });
  applyCors(req, res);

  let pathname;
  try {
    pathname = new URL(req.url, 'http://localhost').pathname;
  } catch {
    return sendJson(res, 400, { error: 'bad_request' });
  }
  if (CONFIG.debug) log(req.method, pathname);

  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    return res.end();
  }
  if (req.method === 'GET' && pathname === '/health') {
    return sendJson(res, 200, {
      status: 'ok',
      uptime: process.uptime(),
      db: DB_PATH,
      hasToken: readStoredToken().state === 'ok',
    });
  }
  if (req.method === 'POST' && (pathname === '/log' || pathname === '/api/messages')) {
    return handleIngest(req, res);
  }
  return sendJson(res, 404, { error: 'not_found' });
}

// ---------------------------------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------------------------------

/** Best-effort: archives contain private conversations, keep them owner-only. */
function restrictPermissions() {
  for (const file of [DB_PATH, BACKUP_JSONL]) {
    try {
      fs.chmodSync(file, 0o600);
    } catch {
      /* file does not exist yet */
    }
  }
}

function main() {
  fs.mkdirSync(CONFIG.dataDir, { recursive: true, mode: 0o700 });
  fs.mkdirSync(CONFIG.configDir, { recursive: true, mode: 0o700 });

  db = openDatabaseWithRetry();
  restrictPermissions();
  insertMessage = db.prepare(`
    INSERT OR IGNORE INTO messages (
      id, chat_id, sender, is_from_me, message_ts, captured_at,
      is_likely_live, type, body, caption, link_url, link_title, raw_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`);
  insertReel = db.prepare(`
    INSERT OR IGNORE INTO reels (
      reel_id, message_id, sender, chat_id, url, title,
      timestamp, first_seen_at, is_likely_live, alerted
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)`);

  const server = http.createServer((req, res) => {
    handleRequest(req, res).catch((err) => {
      logError('unhandled error:', err);
      if (!res.headersSent) sendJson(res, 500, { error: 'internal' });
      else res.end();
    });
  });

  server.on('error', (err) => {
    logError(`cannot listen on ${CONFIG.host}:${CONFIG.port}: ${err.message}`);
    db.close();
    process.exitCode = 1;
  });

  server.listen(CONFIG.port, CONFIG.host, () => {
    log(`wa-notify server running on http://${CONFIG.host}:${server.address().port}`);
    log(`Database: ${DB_PATH}`);
    log(`Token file: ${TOKEN_FILE} (${readStoredToken().state === 'ok' ? 'paired' : 'awaiting pairing'})`);
  });

  const shutdown = () => {
    server.close(() => {
      db.close();
    });
    server.closeAllConnections();
  };
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
}

main();
