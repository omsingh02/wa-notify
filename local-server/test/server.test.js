'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { DatabaseSync } = require('node:sqlite');
const { startServer, request, postLog, makeEntry, query } = require('./helpers');

/** Run a test body against a fresh server and always stop it afterwards. */
function withServer(name, fn, env) {
  test(name, async () => {
    const srv = await startServer(env);
    try {
      await fn(srv);
    } finally {
      await srv.stop();
    }
  });
}

const REEL = 'https://www.instagram.com/reel/AAAAAAAAAAA/?igsh=x';

withServer('UTF-8 characters split across two TCP writes are stored intact', async (srv) => {
  const text = 'before 😀 ❤ नमस्ते after';
  const buf = Buffer.from(JSON.stringify({ entries: [makeEntry('split_1', { body: text })] }));
  const cut = buf.indexOf(Buffer.from('😀')) + 2; // inside the 4-byte emoji
  const res = await request(srv.port, {
    method: 'POST',
    path: '/log',
    headers: { 'Content-Type': 'application/json' },
    chunks: [buf.subarray(0, cut), buf.subarray(cut)],
  });
  assert.equal(res.status, 200);
  const [row] = query(srv.dbPath, "SELECT body FROM messages WHERE id = 'split_1'");
  assert.equal(row.body, text);
  assert.ok(!row.body.includes('�'));
});

withServer('large emoji-heavy batches (many TCP chunks) are stored without corruption', async (srv) => {
  const entries = [];
  for (let i = 0; i < 25; i++) entries.push(makeEntry(`big_${i}`, { body: 'x'.repeat(i) + '😀'.repeat(900) }));
  const res = await postLog(srv.port, { entries });
  assert.equal(res.status, 200);
  const rows = query(srv.dbPath, 'SELECT id, body FROM messages');
  assert.equal(rows.length, 25);
  for (const r of rows) assert.ok(!r.body.includes('�'), `${r.id} was corrupted`);
});

withServer('re-delivery is idempotent: one row, one backup line', async (srv) => {
  const payload = { entries: [makeEntry('dup_1', { body: REEL })] };
  const first = await postLog(srv.port, payload);
  const second = await postLog(srv.port, payload);
  assert.deepEqual([first.json().inserted, second.json().inserted], [1, 0]);
  assert.equal(query(srv.dbPath, "SELECT COUNT(*) AS n FROM messages WHERE id = 'dup_1'")[0].n, 1);
  assert.equal(query(srv.dbPath, 'SELECT COUNT(*) AS n FROM reels')[0].n, 1);
  await new Promise((r) => setTimeout(r, 200));
  const lines = fs.readFileSync(path.join(srv.dataDir, 'chat_archive.jsonl'), 'utf8').split('\n');
  assert.deepEqual(lines.filter(Boolean).length, 1);
  assert.equal(lines.filter((l) => l === '' ).length, 1, 'only the trailing newline, no blank lines');
});

withServer('empty batches write nothing (no blank backup lines)', async (srv) => {
  assert.equal((await postLog(srv.port, { entries: [] })).status, 200);
  assert.equal(fs.existsSync(path.join(srv.dataDir, 'chat_archive.jsonl')), false);
});

withServer('WA_NOTIFY_JSONL_BACKUP=0 disables the backup file', async (srv) => {
  await postLog(srv.port, { entries: [makeEntry('nb_1')] });
  await new Promise((r) => setTimeout(r, 200));
  assert.equal(fs.existsSync(path.join(srv.dataDir, 'chat_archive.jsonl')), false);
  assert.equal(query(srv.dbPath, 'SELECT COUNT(*) AS n FROM messages')[0].n, 1);
}, { WA_NOTIFY_JSONL_BACKUP: '0' });

withServer('trust-on-first-use pairing, then 401 for a missing or wrong token', async (srv) => {
  const tokenFile = path.join(srv.configDir, 'token.txt');
  assert.equal((await postLog(srv.port, { entries: [makeEntry('t0')] })).status, 200, 'unpaired accepts');
  assert.equal(fs.existsSync(tokenFile), false);

  const paired = await postLog(srv.port, { entries: [makeEntry('t1')] }, { 'X-WA-Notify-Token': 'abcdefgh12345678' });
  assert.equal(paired.status, 200);
  assert.equal(fs.readFileSync(tokenFile, 'utf8'), 'abcdefgh12345678');
  assert.equal(fs.statSync(tokenFile).mode & 0o777, 0o600);

  assert.equal((await postLog(srv.port, { entries: [makeEntry('t2')] })).status, 401);
  assert.equal((await postLog(srv.port, { entries: [makeEntry('t3')] }, { 'X-WA-Notify-Token': 'zzzzzzzzzzzzzzzz' })).status, 401);
  assert.equal((await postLog(srv.port, { entries: [makeEntry('t4')] }, { 'X-WA-Notify-Token': 'abcdefgh12345678' })).status, 200);
  assert.deepEqual(query(srv.dbPath, 'SELECT id FROM messages ORDER BY id').map((r) => r.id), ['t0', 't1', 't4']);
});

withServer('a token file that exists but cannot be read fails closed (503), not open', async (srv) => {
  fs.mkdirSync(srv.configDir, { recursive: true });
  fs.mkdirSync(path.join(srv.configDir, 'token.txt')); // a directory: reading it throws EISDIR
  const res = await postLog(srv.port, { entries: [makeEntry('x1')] }, { 'X-WA-Notify-Token': 'abcdefgh12345678' });
  assert.equal(res.status, 503);
  assert.equal(query(srv.dbPath, 'SELECT COUNT(*) AS n FROM messages')[0].n, 0);
});

withServer('payload validation: junk is rejected with 400 and nothing is stored', async (srv) => {
  const bad = ['{}', '{"entries":"x"}', '{"entries":[null]}', '[1,2]', '"text"', '42', 'not json', '{"entries":[{}]}'];
  for (const payload of bad) {
    const res = await postLog(srv.port, payload);
    assert.equal(res.status, 400, `payload ${payload} should be 400, got ${res.status}`);
  }
  const tooMany = { entries: Array.from({ length: 501 }, (_, i) => makeEntry(`many_${i}`)) };
  assert.equal((await postLog(srv.port, tooMany)).status, 400);
  assert.equal(query(srv.dbPath, 'SELECT COUNT(*) AS n FROM messages')[0].n, 0);
});

withServer('accepted payload shapes: {entries}, bare array, single entry; odd field types never throw', async (srv) => {
  assert.equal((await postLog(srv.port, { entries: [makeEntry('s1')] })).status, 200);
  assert.equal((await postLog(srv.port, [makeEntry('s2')])).status, 200);
  assert.equal((await postLog(srv.port, makeEntry('s3'))).status, 200);
  const odd = makeEntry('s4', { body: { not: 'a string' }, timestamp: 'nope', chatId: 12345, type: ['x'] });
  assert.equal((await postLog(srv.port, [odd])).status, 200);
  assert.equal(query(srv.dbPath, 'SELECT COUNT(*) AS n FROM messages')[0].n, 4);
});

withServer('entries without an id get a stable content-hash id (retries stay idempotent)', async (srv) => {
  const noId = { capturedAt: 1700000000000, convenience: { chatId: 'c@c.us', body: 'x', timestamp: 1700000000 }, raw: {} };
  await postLog(srv.port, [noId]);
  await postLog(srv.port, [noId]);
  const rows = query(srv.dbPath, 'SELECT id FROM messages');
  assert.equal(rows.length, 1);
  assert.match(rows[0].id, /^gen_[0-9a-f]{24}$/);
});

withServer('reel links are extracted; freshness comes from capturedAt vs message time', async (srv) => {
  const now = Date.now();
  await postLog(srv.port, [
    makeEntry('fresh', { body: REEL, timestamp: Math.floor(now / 1000) }),
    makeEntry('old', { body: 'https://www.instagram.com/p/BBBBBBBBBBB/', timestamp: Math.floor(now / 1000) - 7200 }),
  ]);
  const reels = query(srv.dbPath, 'SELECT reel_id, url, is_likely_live FROM reels ORDER BY reel_id');
  assert.deepEqual(reels.map((r) => [r.reel_id, r.url, r.is_likely_live]), [
    ['AAAAAAAAAAA', 'https://www.instagram.com/reel/AAAAAAAAAAA', 1],
    ['BBBBBBBBBBB', 'https://www.instagram.com/p/BBBBBBBBBBB', 0],
  ]);
});

withServer('a write-locked database answers 500 (not 200), the client retries, then it is stored once', async (srv) => {
  const holder = new DatabaseSync(srv.dbPath);
  holder.exec('PRAGMA busy_timeout = 0');
  holder.exec('BEGIN IMMEDIATE');
  const payload = { entries: [makeEntry('locked_1', { body: REEL })] };
  const failed = await postLog(srv.port, payload);
  assert.equal(failed.status, 500);
  assert.deepEqual(failed.json(), { error: 'db_error' });
  holder.exec('ROLLBACK');
  holder.close();
  assert.equal(query(srv.dbPath, 'SELECT COUNT(*) AS n FROM messages')[0].n, 0, 'nothing from the failed batch is kept');
  assert.equal(query(srv.dbPath, 'SELECT COUNT(*) AS n FROM reels')[0].n, 0);
  assert.equal((await postLog(srv.port, payload)).status, 200);
  assert.equal(query(srv.dbPath, "SELECT COUNT(*) AS n FROM messages WHERE id = 'locked_1'")[0].n, 1);
  await new Promise((r) => setTimeout(r, 200));
  const backup = fs.readFileSync(path.join(srv.dataDir, 'chat_archive.jsonl'), 'utf8').split('\n').filter(Boolean);
  assert.equal(backup.length, 1, 'the failed attempt was not written to the backup');
}, { WA_NOTIFY_BUSY_TIMEOUT_MS: '300' });

test('startup waits for a lock held by another process instead of crashing (busy_timeout before journal_mode)', async () => {
  const fsx = require('node:fs');
  const os = require('node:os');
  const root = fsx.mkdtempSync(path.join(os.tmpdir(), 'wa-notify-lock-'));
  const dataDir = path.join(root, 'data');
  fsx.mkdirSync(dataDir, { recursive: true });
  // A rollback-journal database held under an exclusive lock: switching to WAL needs that lock.
  const holder = new DatabaseSync(path.join(dataDir, 'wa-notify.db'));
  holder.exec('PRAGMA journal_mode = DELETE');
  holder.exec('CREATE TABLE IF NOT EXISTS probe (x)');
  holder.exec('BEGIN EXCLUSIVE');
  const release = setTimeout(() => holder.exec('COMMIT'), 1200);
  const started = Date.now();
  let srv;
  try {
    srv = await startServer({ WA_NOTIFY_DATA_DIR: dataDir, HOME: root, WA_NOTIFY_CONFIG_DIR: path.join(root, 'cfg') });
    assert.ok(Date.now() - started >= 900, 'the server really had to wait for the lock');
    assert.equal((await request(srv.port, { path: '/health' })).status, 200);
  } finally {
    clearTimeout(release);
    try {
      holder.exec('COMMIT');
    } catch {
      /* already committed */
    }
    holder.close();
    if (srv) await srv.stop();
  }
});

withServer('Host header check: foreign Host is rejected with 421 (DNS-rebinding hardening)', async (srv) => {
  const ok = await request(srv.port, { path: '/health' });
  assert.equal(ok.status, 200);
  assert.equal(ok.json().status, 'ok');
  assert.equal((await request(srv.port, { path: '/health', headers: { Host: 'evil.example:8765' } })).status, 421);
  assert.equal((await request(srv.port, { path: '/health', headers: { Host: 'localhost:1234' } })).status, 200);
  const post = await request(srv.port, { method: 'POST', path: '/log', headers: { Host: 'evil.example', 'Content-Type': 'application/json' }, body: '{"entries":[]}' });
  assert.equal(post.status, 421);
});

withServer('CORS: known origins only, never a wildcard', async (srv) => {
  const cors = async (origin) => (await request(srv.port, { method: 'OPTIONS', path: '/log', headers: origin ? { Origin: origin } : {} })).headers['access-control-allow-origin'];
  assert.equal(await cors('https://web.whatsapp.com'), 'https://web.whatsapp.com');
  assert.equal(await cors('http://localhost:5173'), 'http://localhost:5173');
  assert.equal(await cors('https://evil.example'), undefined);
  assert.equal(await cors('chrome-extension://abcdefghijklmnopabcdefghijklmnop'), undefined, 'extension origin needs WA_NOTIFY_EXTENSION_ID');
  assert.equal(await cors(null), undefined, 'no Origin header -> no CORS headers, in particular no "*"');
});

withServer('CORS: the configured extension id is allowed, other ids are not', async (srv) => {
  const cors = async (origin) => (await request(srv.port, { method: 'OPTIONS', path: '/log', headers: { Origin: origin } })).headers['access-control-allow-origin'];
  assert.equal(await cors('chrome-extension://myextensionid'), 'chrome-extension://myextensionid');
  assert.equal(await cors('chrome-extension://someotherid'), undefined);
}, { WA_NOTIFY_EXTENSION_ID: 'myextensionid' });

withServer('routing: /api/messages alias works, unknown paths are 404', async (srv) => {
  const alias = await request(srv.port, { method: 'POST', path: '/api/messages', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ entries: [makeEntry('alias_1')] }) });
  assert.equal(alias.status, 200);
  assert.equal((await request(srv.port, { path: '/nope' })).status, 404);
});

withServer('database and backup files are owner-only', async (srv) => {
  await postLog(srv.port, { entries: [makeEntry('perm_1')] });
  await new Promise((r) => setTimeout(r, 200));
  assert.equal(fs.statSync(srv.dbPath).mode & 0o777, 0o600);
  assert.equal(fs.statSync(path.join(srv.dataDir, 'chat_archive.jsonl')).mode & 0o777, 0o600);
});
