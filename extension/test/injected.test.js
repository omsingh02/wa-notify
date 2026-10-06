'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { load, delay } = require('./harness');

/** injected.js in a fake page: `window` is the sandbox itself, events and logs are captured. */
function makePage() {
  const events = [];
  const logs = [];
  const sandbox = {
    setTimeout,
    clearTimeout,
    Object,
    Map,
    Array,
    String,
    Date,
    WeakSet,
    JSON,
    Error,
    TypeError,
    Symbol,
    Promise,
    CustomEvent: class {
      constructor(type, init) {
        this.type = type;
        this.detail = init && init.detail;
      }
    },
    document: { dispatchEvent: (e) => events.push(e) },
    console: {
      log: (...a) => logs.push(a.join(' ')),
      warn: (...a) => logs.push(a.join(' ')),
      error: (...a) => logs.push(a.join(' ')),
      table() {},
    },
  };
  sandbox.window = sandbox;
  load('injected.js', sandbox);
  return { win: sandbox, events, logs };
}

/** A Backbone-style collection that remembers its listeners. */
function collection(name) {
  return {
    name,
    models: [],
    _events: {},
    handlers: {},
    get() {},
    on(event, fn) {
      (this.handlers[event] = this.handlers[event] || []).push(fn);
    },
  };
}

test('__waNotifyDebug works with a populated module registry and lists only candidates', () => {
  const { win } = makePage();
  win.__d = function () {}; // WhatsApp's loader being assigned
  win.__d('WAWebMsgCollection');
  win.__d('WAWebChatCollection');
  win.__d('WAWebSomethingUnrelated');
  const candidates = win.__waNotifyDebug();
  assert.deepEqual([...candidates].map((c) => c.id).sort(), ['WAWebChatCollection', 'WAWebMsgCollection']);
});

test('__waNotifyAttach accepts {Msg}, a bare collection and a module export', () => {
  for (const shape of [
    (c) => ({ Msg: c }),
    (c) => c,
    (c) => ({ MsgCollection: c }),
    (c) => ({ default: { Msg: c } }),
  ]) {
    const { win } = makePage();
    const coll = collection('Msg');
    assert.equal(win.__waNotifyAttach(shape(coll)), true);
    assert.equal((coll.handlers.add || []).length, 1);
  }
});

test('attaching the same collection twice does not duplicate the listener', () => {
  const { win } = makePage();
  const coll = collection('Msg');
  win.__waNotifyAttach({ Msg: coll });
  win.__waNotifyAttach(coll);
  assert.equal(coll.handlers.add.length, 1);
});

test('the chat collection is never attached from a module export', () => {
  const { win, logs } = makePage();
  const chat = collection('Chat');
  assert.equal(win.__waNotifyAttach({ ChatCollection: chat }), false);
  assert.equal(win.__waNotifyAttach({ Chat: chat }), false);
  assert.equal(win.__waNotifyAttach({ default: { ChatCollection: chat } }), false);
  assert.equal(Object.keys(chat.handlers).length, 0);

  win.require = () => ({ ChatCollection: chat });
  win.__waNotifyRequire('WAWebChatCollection');
  assert.ok(!logs.some((l) => /looks like a match/.test(l)), 'must not report the chat module as a match');
});

test('__waNotifyRequire output can be fed straight to __waNotifyAttach', () => {
  const { win } = makePage();
  const msg = collection('Msg');
  win.require = () => ({ MsgCollection: msg });
  assert.equal(win.__waNotifyAttach(win.__waNotifyRequire('WAWebMsgCollection')), true);
  assert.equal(msg.handlers.add.length, 1);
});

test('auto-detection attaches to the message collection and never to the chat collection', async () => {
  const run = async (registeredId, exportsObj) => {
    const { win } = makePage();
    win.__d = function () {};
    win.__d(registeredId);
    win.require = (id) => {
      if (id === registeredId) return exportsObj;
      throw new Error(`unknown module ${id}`);
    };
    await delay(3300); // the poller starts after a 2 s delay
  };
  const chat = collection('Chat');
  const msg = collection('Msg');
  await Promise.all([run('WAWebChatCollection', { ChatCollection: chat }), run('WAWebMsgCollection', { MsgCollection: msg })]);
  assert.equal(Object.keys(chat.handlers).length, 0, 'chat collection must stay untouched');
  assert.equal((msg.handlers.add || []).length, 1, 'message collection must be attached');
});

test('a captured message is flattened into a capture entry', () => {
  const { win, events } = makePage();
  const coll = collection('Msg');
  win.__waNotifyAttach({ Msg: coll });

  const key = { _serialized: 'false_911234567890@c.us_3EB0ABC', remote: { _serialized: '911234567890@c.us' }, fromMe: false, id: '3EB0ABC' };
  const msg = {
    id: key,
    from: { _serialized: '911234567890@c.us' },
    t: 1700000000,
    type: 'chat',
    body: 'look https://www.instagram.com/reel/AAAAAAAAAAA/',
    notifyName: 'Alice',
    ack: 1,
    attributes: { id: key, body: 'look', t: 1700000000, chat: { huge: 'object' }, collection: {}, msgButtons: [], mediaKey: 'abc' },
  };
  coll.handlers.add[0](msg);

  assert.equal(events.length, 1);
  assert.equal(events[0].type, '__wa_notify_event__');
  const { capturedAt, convenience, raw } = events[0].detail;
  assert.equal(typeof capturedAt, 'number');
  assert.equal(convenience.id, 'false_911234567890@c.us_3EB0ABC');
  assert.equal(convenience.chatId, '911234567890@c.us');
  assert.equal(convenience.fromMe, false);
  assert.equal(convenience.timestamp, 1700000000);
  assert.equal(convenience.notifyName, 'Alice');
  assert.match(convenience.body, /instagram\.com\/reel/);
  assert.equal(raw.id, 'false_911234567890@c.us_3EB0ABC', 'the key object becomes its string form');
  assert.ok(!('chat' in raw) && !('collection' in raw) && !('msgButtons' in raw), 'linked models are never crawled');
});

test('a message whose getters throw is skipped without breaking the listener', () => {
  const { win, events, logs } = makePage();
  const coll = collection('Msg');
  win.__waNotifyAttach({ Msg: coll });
  const hostile = {};
  Object.defineProperty(hostile, 'body', { get() { throw new Error('boom'); }, enumerable: true });
  assert.doesNotThrow(() => coll.handlers.add[0](hostile));
  assert.doesNotThrow(() => coll.handlers.add[0](null));
  assert.ok(events.length <= 2);
  assert.ok(Array.isArray(logs));
});
