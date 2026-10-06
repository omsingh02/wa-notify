'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { load, delay } = require('./harness');

/** A content.js instance running against a fake event target and a fake chrome.runtime. */
function makeContent(sendMessage) {
  const listeners = [];
  const sent = [];
  const errors = [];
  const warnings = [];
  const runtime = {
    id: 'self-id',
    sendMessage: (msg) => {
      sent.push(msg);
      return sendMessage ? sendMessage(msg) : Promise.resolve();
    },
  };
  const sandbox = {
    document: {
      addEventListener: (type, fn) => type === '__wa_notify_event__' && listeners.push(fn),
      removeEventListener: (type, fn) => {
        const i = listeners.indexOf(fn);
        if (i >= 0) listeners.splice(i, 1);
      },
    },
    chrome: { runtime },
    console: { log() {}, warn: (...a) => warnings.push(a.join(' ')), error: (...a) => errors.push(a.join(' ')) },
  };
  load('content.js', sandbox);
  return {
    sandbox,
    runtime,
    listeners,
    sent,
    errors,
    warnings,
    fire: (detail) => [...listeners].forEach((fn) => fn({ detail })),
    loadAgain: () => load('content.js', sandbox),
  };
}

const entry = (id) => ({ capturedAt: 1, convenience: { id }, raw: {} });

test('only well-formed capture entries are relayed to the background worker', () => {
  const c = makeContent();
  const junk = [undefined, null, 'text', 42, [], {}, { convenience: null }, { convenience: 'x' }, { raw: {} }, [{ convenience: {} }]];
  for (const detail of junk) c.fire(detail);
  assert.equal(c.sent.length, 0);

  const e = entry('m1');
  c.fire(e);
  assert.equal(c.sent.length, 1);
  assert.equal(c.sent[0].type, 'wa-notify-message');
  assert.equal(c.sent[0].payload, e, 'the entry itself is forwarded');
});

test('a rejected sendMessage is logged and the relay keeps running', async () => {
  const c = makeContent(() => Promise.reject(new Error('worker asleep')));
  c.fire(entry('m1'));
  await delay(20);
  assert.equal(c.errors.length, 1);
  assert.equal(c.warnings.length, 0);
  assert.equal(c.listeners.length, 1, 'an ordinary failure must not switch the relay off');
});

test('a synchronous "context invalidated" throw stops the relay with ONE clear warning', () => {
  const c = makeContent(() => {
    throw new Error('Extension context invalidated.');
  });
  assert.doesNotThrow(() => c.fire(entry('m1')));
  assert.equal(c.errors.length, 0, 'no per-message error spam');
  assert.equal(c.warnings.length, 1);
  assert.match(c.warnings[0], /Reload this WhatsApp Web tab/);
  assert.equal(c.listeners.length, 0, 'the listener is removed');

  c.fire(entry('m2'));
  c.fire(entry('m3'));
  assert.equal(c.sent.length, 1, 'nothing is sent after the relay stopped');
  assert.equal(c.warnings.length, 1, 'and it stays quiet');
});

test('an orphaned copy (chrome.runtime.id is gone) stops before trying to send', () => {
  const c = makeContent();
  c.runtime.id = undefined;
  c.fire(entry('m1'));
  assert.equal(c.sent.length, 0);
  assert.equal(c.warnings.length, 1);
  assert.equal(c.listeners.length, 0);
});

test('a rejected promise that says "context invalidated" also stops the relay', async () => {
  const c = makeContent(() => Promise.reject(new Error('Extension context invalidated.')));
  c.fire(entry('m1'));
  await delay(20);
  assert.equal(c.errors.length, 0);
  assert.equal(c.warnings.length, 1);
  assert.equal(c.listeners.length, 0);
});

test('running the script twice in a tab with a live relay installs only one listener', () => {
  const c = makeContent();
  c.loadAgain();
  c.loadAgain();
  assert.equal(c.listeners.length, 1);
  c.fire(entry('m1'));
  assert.equal(c.sent.length, 1, 'one relay, one message');
});

test('a fresh copy takes over from an orphaned one that has stopped', () => {
  const c = makeContent();
  c.runtime.id = undefined;
  c.fire(entry('m1')); // the orphan notices and stops
  assert.equal(c.listeners.length, 0);

  c.runtime.id = 'new-id'; // the re-injected copy runs in a context that is alive
  c.loadAgain();
  assert.equal(c.listeners.length, 1, 'a new relay is installed');
  c.fire(entry('m2'));
  assert.equal(c.sent.length, 1);
  assert.equal(c.sent[0].payload.convenience.id, 'm2');
});
