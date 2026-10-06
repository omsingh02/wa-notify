'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { load, delay } = require('./harness');

function makeContent(sendMessage) {
  const listeners = {};
  const sent = [];
  const errors = [];
  const sandbox = {
    document: { addEventListener: (type, fn) => (listeners[type] = fn) },
    chrome: {
      runtime: {
        sendMessage: (msg) => {
          sent.push(msg);
          return sendMessage ? sendMessage(msg) : Promise.resolve();
        },
      },
    },
    console: { log() {}, error: (...a) => errors.push(a.join(' ')) },
  };
  load('content.js', sandbox);
  return { fire: (detail) => listeners.__wa_notify_event__({ detail }), sent, errors };
}

test('only well-formed capture entries are relayed to the background worker', () => {
  const c = makeContent();
  const junk = [undefined, null, 'text', 42, [], {}, { convenience: null }, { convenience: 'x' }, { raw: {} }, [{ convenience: {} }]];
  for (const detail of junk) c.fire(detail);
  assert.equal(c.sent.length, 0);

  const entry = { capturedAt: 1, convenience: { id: 'm1' }, raw: {} };
  c.fire(entry);
  assert.equal(c.sent.length, 1);
  assert.equal(c.sent[0].type, 'wa-notify-message');
  assert.equal(c.sent[0].payload, entry, 'the entry itself is forwarded');
});

test('a rejected sendMessage is logged, not thrown', async () => {
  const c = makeContent(() => Promise.reject(new Error('worker asleep')));
  c.fire({ convenience: {} });
  await delay(20);
  assert.equal(c.errors.length, 1);
});

test('a synchronous throw (stale context after an extension reload) is caught', () => {
  const c = makeContent(() => {
    throw new Error('Extension context invalidated.');
  });
  assert.doesNotThrow(() => c.fire({ convenience: {} }));
  assert.equal(c.errors.length, 1);
});
