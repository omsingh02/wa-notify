'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { webcrypto } = require('node:crypto');
const { load, delay, until } = require('./harness');

const QUEUE_KEY = 'wa_notify_queue';
const clone = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
// Values built inside the vm sandbox belong to another realm: compare plain copies, not the originals.
const plain = clone;

/**
 * A background.js instance running against a fake chrome.storage and a slow fake server.
 * `state` can be changed while the test runs (server up/down, status code, storage failures).
 */
function makeWorker(initial = {}) {
  const state = { fetchMs: 5, storageMs: 1, ok: true, status: 200, failSet: false, throwFetch: false, ...initial };
  const store = {};
  const posted = [];
  const badge = [];
  const warnings = [];
  const counters = { fetches: 0, bytesWritten: 0 };
  const injections = [];
  const tabQueries = [];
  let onMessage;
  let onAlarm;
  let onInstalled;

  const chrome = {
    storage: {
      local: {
        async get(key) {
          await delay(state.storageMs);
          return { [key]: clone(store[key]) };
        },
        async set(obj) {
          await delay(state.storageMs);
          if (state.failSet) throw new Error('storage failure');
          for (const [k, v] of Object.entries(obj)) {
            const text = JSON.stringify(v);
            counters.bytesWritten += text.length;
            store[k] = JSON.parse(text);
          }
        },
      },
    },
    alarms: { create() {}, onAlarm: { addListener: (fn) => (onAlarm = fn) } },
    runtime: {
      id: 'self-id',
      onMessage: { addListener: (fn) => (onMessage = fn) },
      onInstalled: { addListener: (fn) => (onInstalled = fn) },
    },
    tabs: {
      async query(q) {
        tabQueries.push(q);
        if (state.failQuery) throw new Error('tabs unavailable');
        return state.tabs || [];
      },
    },
    scripting: {
      async executeScript(opts) {
        if ((state.rejectTabs || []).includes(opts.target.tabId)) throw new Error('Cannot access contents of the page');
        injections.push(opts);
      },
    },
    action: { setBadgeText: ({ text }) => badge.push(text), setBadgeBackgroundColor() {} },
  };

  const sandbox = {
    chrome,
    crypto: webcrypto,
    AbortSignal,
    setTimeout,
    Promise,
    Uint8Array,
    Array,
    JSON,
    Date,
    Error,
    console: { log() {}, warn: (...a) => warnings.push(a.join(' ')), error() {} },
    fetch: async (_url, opts) => {
      await delay(state.fetchMs);
      counters.fetches++;
      if (state.throwFetch) throw new Error('connection refused');
      if (!state.ok) return { ok: false, status: state.status };
      for (const entry of JSON.parse(opts.body).entries) posted.push(entry.id);
      return { ok: true, status: 200 };
    },
  };
  load('background.js', sandbox);

  return {
    state,
    store,
    posted,
    badge,
    warnings,
    counters,
    injections,
    tabQueries,
    installed: (details) => onInstalled(details),
    send: (payload, sender = { id: 'self-id' }) => onMessage({ type: 'wa-notify-message', payload }, sender),
    rawMessage: (msg, sender) => onMessage(msg, sender),
    alarm: () => onAlarm({ name: 'wa-notify-flush' }),
    queue: () => store[QUEUE_KEY] || [],
  };
}

const entry = (id, size = 3000) => ({ id, pad: 'x'.repeat(size) });

test('a burst during a slow request loses nothing and duplicates nothing', async () => {
  const w = makeWorker({ fetchMs: 30 });
  await delay(20);
  for (let i = 0; i < 10; i++) {
    w.send(entry(i));
    await delay(5);
  }
  await until(() => w.queue().length === 0 && w.posted.length >= 10);
  await delay(100);
  assert.deepEqual([...w.posted].sort((a, b) => a - b), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
});

test('a flood of 100 messages is delivered exactly once, with bounded storage writes', async () => {
  const n = 100;
  const size = 3000;
  const w = makeWorker({ fetchMs: 10 });
  await delay(20);
  for (let i = 0; i < n; i++) w.send(entry(i, size));
  await until(() => w.queue().length === 0 && w.posted.length >= n, 20000);
  await delay(100);
  assert.equal(new Set(w.posted).size, n, 'every message arrived');
  assert.equal(w.posted.length, n, 'and none twice');
  // Each enqueue rewrites the whole queue (quadratic). Removal writes shrink it. Stay within 2x of the worst case.
  assert.ok(w.counters.bytesWritten < n * (n + 1) * size, `wrote ${w.counters.bytesWritten} bytes`);
});

test('server down: messages stay queued, then arrive after the next retry', async () => {
  const w = makeWorker({ throwFetch: true });
  await delay(20);
  for (let i = 0; i < 5; i++) w.send(entry(i, 50));
  await until(() => w.queue().length === 5 && w.counters.fetches > 0);
  assert.equal(w.posted.length, 0);
  w.state.throwFetch = false;
  await w.alarm();
  await until(() => w.queue().length === 0);
  assert.deepEqual([...w.posted].sort(), [0, 1, 2, 3, 4]);
});

test('a rejected token is made visible (badge + warning) and cleared after a success', async () => {
  const w = makeWorker({ ok: false, status: 401 });
  await delay(20);
  w.send(entry(1, 10));
  await until(() => w.badge.includes('!'));
  assert.ok(w.warnings.some((m) => /401/.test(m) && /re-pair/.test(m)), w.warnings.join('|'));
  assert.equal(w.queue().length, 1, 'the message is kept');
  w.state.ok = true;
  await w.alarm();
  await until(() => w.queue().length === 0);
  assert.equal(w.badge.at(-1), '', 'badge cleared');
  assert.deepEqual(w.posted, [1]);
});

test('warnings are rate limited, not logged for every failed batch', async () => {
  const w = makeWorker({ ok: false, status: 500 });
  await delay(20);
  for (let i = 0; i < 4; i++) {
    w.send(entry(i, 10));
    await delay(30);
  }
  await delay(100);
  assert.equal(w.warnings.length, 1, w.warnings.join('|'));
});

test('no pairing token => nothing is ever sent (fail closed)', async () => {
  const w = makeWorker({ failSet: true });
  await delay(20);
  w.send(entry(1, 10));
  await delay(150);
  assert.equal(w.counters.fetches, 0);
  assert.ok(w.badge.includes('!'));
  assert.ok(w.warnings.some((m) => /pairing token/.test(m)), w.warnings.join('|'));
});

test('messages from other senders or with bad payloads are ignored', async () => {
  const w = makeWorker();
  await delay(20);
  w.send(entry(1, 10), { id: 'some-other-extension' });
  w.rawMessage({ type: 'something-else', payload: entry(2, 10) }, { id: 'self-id' });
  w.rawMessage({ type: 'wa-notify-message', payload: null }, { id: 'self-id' });
  w.rawMessage({ type: 'wa-notify-message', payload: 'text' }, { id: 'self-id' });
  w.rawMessage(undefined, { id: 'self-id' });
  await delay(100);
  assert.equal(w.queue().length, 0);
  assert.equal(w.counters.fetches, 0);
});

test('the token is generated once and reused', async () => {
  const w = makeWorker();
  await delay(20);
  w.send(entry(1, 10));
  await until(() => w.posted.length === 1);
  const token = w.store.wa_notify_auth_token;
  assert.match(token, /^[0-9a-f]{32}$/);
  w.send(entry(2, 10));
  await until(() => w.posted.length === 2);
  assert.equal(w.store.wa_notify_auth_token, token);
});

// ---- reattaching the relay after an extension install / reload --------------------------------

test('installing or updating attaches a fresh relay to the WhatsApp tabs that are already open', async () => {
  const w = makeWorker({ tabs: [{ id: 11 }, { id: 12 }] });
  w.installed({ reason: 'update' });
  await until(() => w.injections.length === 2);
  assert.deepEqual(plain(w.tabQueries), [{ url: 'https://web.whatsapp.com/*' }]);
  assert.deepEqual(
    w.injections.map((i) => i.target.tabId),
    [11, 12]
  );
  for (const i of w.injections) assert.deepEqual(plain(i.files), ['src/content.js'], 'only the relay, not injected.js');
});

test('browser and shared-module updates leave the tabs alone', async () => {
  const w = makeWorker({ tabs: [{ id: 1 }] });
  w.installed({ reason: 'chrome_update' });
  w.installed({ reason: 'shared_module_update' });
  await delay(50);
  assert.equal(w.tabQueries.length, 0);
  assert.equal(w.injections.length, 0);
});

test('one tab that cannot be injected does not stop the others, and is reported', async () => {
  const w = makeWorker({ tabs: [{ id: 1 }, { id: 2 }, { id: 3 }], rejectTabs: [2] });
  w.installed({ reason: 'install' });
  await until(() => w.injections.length === 2);
  assert.deepEqual(
    w.injections.map((i) => i.target.tabId),
    [1, 3]
  );
  assert.ok(w.warnings.some((m) => m.includes('tab 2') && m.includes('reload the tab')), w.warnings.join(' | '));
});

test('a failing tab query is reported, not thrown', async () => {
  const w = makeWorker({ failQuery: true });
  assert.doesNotThrow(() => w.installed({ reason: 'update' }));
  await until(() => w.warnings.some((m) => m.includes('reattaching')));
  assert.equal(w.injections.length, 0);
});
