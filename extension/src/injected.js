/**
 * wa-notify — injected.js
 * Runs in the PAGE's own JS context (manifest "world": "MAIN") at
 * document_start, i.e. before WhatsApp's own bundle has executed.
 *
 * WhatsApp Web (as of the Comet/Haste-based rewrite) does not expose a
 * plain webpack module registry the way older builds did. Instead it
 * uses Meta's internal Haste module system:
 *   - window.__d(id, deps, factory, ...)   registers a module by NAME
 *   - window.require(id)                   lazily resolves + caches it
 *
 * Haste ids are real, human-readable strings (e.g. "WAWebMsgCollection"),
 * not obfuscated numeric ids, which makes this more robust than the old
 * webpack approach — but we still don't hard-code an exact id, since
 * naming can change between releases. Instead we intercept every module
 * registration as it happens and test the ones whose name looks
 * relevant against a structural heuristic.
 *
 * This script does NOT fetch() anywhere — see content.js / background.js
 * for why (WhatsApp's page CSP would block it).
 */
(function () {
  const TAG = '[wa-notify]';
  const log = (...a) => console.log(TAG, ...a);
  const warn = (...a) => console.warn(TAG, ...a);

  // ---------------------------------------------------------------------
  // 1. Install the __d() interceptor as early as possible.
  //    __d may not exist yet (document_start runs before WhatsApp's
  //    bootstrap assigns it), so we use a getter/setter to catch the
  //    assignment itself, not just wrap an already-existing function.
  // ---------------------------------------------------------------------
  if (window.__waNotifyHasteHookInstalled) return; // avoid double-install
  window.__waNotifyHasteHookInstalled = true;
  console.log(TAG, '⚡ Hook installed at document_start. Waiting for WhatsApp modules...');

  const registry = new Map(); // moduleId -> { attempts, resolved, failed }
  window.__WA_NOTIFY_REGISTRY__ = registry;

  let realD = typeof window.__d === 'function' ? window.__d : null;

  function wrappedD(id, ...rest) {
    try {
      if (!registry.has(id)) registry.set(id, { attempts: 0, resolved: false, failed: false });
    } catch {
      /* ignore */
    }

    if (typeof realD === 'function') {
      return realD.apply(this, [id, ...rest]);
    }
  }

  // Safely intercept __d without masking its absence during early bootstrap
  try {
    Object.defineProperty(window, '__d', {
      configurable: true,
      enumerable: true,
      get() {
        return realD ? wrappedD : undefined;
      },
      set(fn) {
        realD = fn;
      },
    });
  } catch (e) {
    window.__d = wrappedD;
  }

  // ---------------------------------------------------------------------
  // Lightweight, non-recursive attribute extractor.
  // Never crawls linked Backbone models (chat, collection, models) to prevent
  // main-thread freezing, memory spikes, and WebSocket dropouts.
  // ---------------------------------------------------------------------
  function safeShallowAttributes(msg) {
    if (!msg || typeof msg !== 'object') return {};
    const source = (msg.attributes && typeof msg.attributes === 'object') ? msg.attributes : msg;
    const out = {};

    let keys;
    try {
      keys = Object.keys(source);
    } catch {
      return out;
    }

    for (const key of keys) {
      if (key.startsWith('_') || key === 'collection' || key === 'chat' || key === 'msgButtons') continue;
      try {
        const val = source[key];
        const t = typeof val;
        if (t === 'string' || t === 'number' || t === 'boolean' || val === null) {
          out[key] = val;
        } else if (key === 'id' && val && typeof val === 'object') {
          out.id = idToString(val);
        }
      } catch {
        /* ignore getter throws */
      }
    }
    return out;
  }

  // ---------------------------------------------------------------------
  // 2. Structural heuristic for "this looks like the message store".
  //    Handles two shapes seen across builds:
  //      a) an aggregator object with both .Msg (event-emitting
  //         collection) and .Chat
  //      b) a module that exports the Msg collection directly (possibly
  //         nested one level under a named export)
  // ---------------------------------------------------------------------
  function looksLikeMsgCollection(val) {
    if (!val || typeof val !== 'object') return false;
    const hasOn = typeof val.on === 'function' || typeof val.addListener === 'function' || typeof val.listenTo === 'function';
    return (
      hasOn &&
      (Array.isArray(val.models) || val._byId || val._events || val._index || typeof val.get === 'function')
    );
  }

  function extractStoreFrom(exportsObj, moduleId = '') {
    if (!exportsObj || typeof exportsObj !== 'object') return null;

    // Shape (a): aggregator with .Msg (or .MsgCollection)
    const Msg = exportsObj.Msg || exportsObj.MsgCollection;
    const Chat = exportsObj.Chat || exportsObj.ChatCollection;
    if (Msg && typeof Msg.on === 'function') {
      return { Msg, Chat, ...exportsObj };
    }

    // Shape (b): some key on this module IS the Msg collection. Only trusted when the export name or
    // the module id says "Msg" — every other collection (chats, contacts, ...) looks just the same.
    const moduleLooksLikeMsg = MSG_NAME.test(String(moduleId));
    for (const key of Object.keys(exportsObj)) {
      if (!moduleLooksLikeMsg && !MSG_NAME.test(key)) continue;
      let val;
      try {
        val = exportsObj[key];
      } catch {
        continue;
      }
      if (looksLikeMsgCollection(val)) {
        return { Msg: val, Chat: null, __synthetic: true };
      }
    }
    return null;
  }

  // ---------------------------------------------------------------------
  // 3. Poll: check well-known collection modules first, avoid speculative requires
  // ---------------------------------------------------------------------
  // Only modules that can yield the MESSAGE collection. The chat collection is deliberately absent:
  // attaching to it would capture chat objects instead of messages.
  const WELL_KNOWN_MODULES = ['WAWebCollections', 'WAWebMsgCollection'];

  /** Module ids / export names that may hold the message collection. */
  const MSG_NAME = /Msg/i;
  /** Registered module ids that __waNotifyDebug() lists as candidates. */
  const CANDIDATE_PATTERN = /Msg|Message|Collection/i;

  const POLL_INTERVAL_MS = 1000;
  const MAX_TOTAL_MS = 120000;

  let elapsed = 0;
  let pollHandle = null;

  function tryFind() {
    if (typeof window.require !== 'function') return null;

    // 1. Check well-known modules ONLY if registered in WhatsApp's haste module registry
    for (const id of WELL_KNOWN_MODULES) {
      if (!registry.has(id)) continue;
      try {
        const mod = window.require(id);
        const store = extractStoreFrom(mod, id) || extractStoreFrom(mod && mod.default, id);
        if (store) return store;
      } catch {
        /* may resolve later */
      }
    }

    // 2. Safe fallback: only if well-known modules haven't matched after 10s of bootstrap,
    // probe MsgCollection candidate modules gently (never during early handshake)
    if (elapsed >= 10000) {
      for (const [id, meta] of registry) {
        if (meta.resolved || meta.failed) continue;
        if (!/(?:WAWeb)?MsgCollections?$/i.test(String(id))) continue;
        if (meta.attempts >= 3) {
          meta.failed = true;
          continue;
        }

        meta.attempts++;
        let mod;
        try {
          mod = window.require(id);
        } catch {
          continue;
        }

        const store = extractStoreFrom(mod, id) || extractStoreFrom(mod && mod.default, id);
        if (store) {
          meta.resolved = true;
          return store;
        }
      }
    }

    return null;
  }

  let isPolling = false;

  function startPolling() {
    if (isPolling) return;
    isPolling = true;

    // Delay start by 2s so WhatsApp completes initial bootstrapping
    setTimeout(() => {
      function tick() {
        const store = tryFind();
        if (store) {
          isPolling = false;
          attach(store);
          return;
        }

        elapsed += POLL_INTERVAL_MS;
        if (elapsed % 5000 === 0) {
          log(`Searching for message store (${elapsed / 1000}s)... ${registry.size} modules registered`);
        }
        if (elapsed >= MAX_TOTAL_MS) {
          isPolling = false;
          warn(`gave up locating the message store after ${elapsed / 1000}s.`);
          return;
        }

        setTimeout(tick, POLL_INTERVAL_MS);
      }

      tick();
    }, 2000);
  }

  // ---------------------------------------------------------------------
  // Message extraction
  // ---------------------------------------------------------------------
  function idToString(idLike) {
    if (!idLike) return undefined;
    if (typeof idLike === 'string') return idLike;
    return idLike._serialized || idLike.id || undefined;
  }

  function extractMessage(msg) {
    const convenience = {};
    const tryField = (name, ...getters) => {
      for (const g of getters) {
        try {
          const v = typeof g === 'function' ? g() : msg[g];
          if (v !== undefined && v !== null) {
            convenience[name] = v;
            return;
          }
        } catch {
          /* ignore and try next getter */
        }
      }
    };

    tryField('id', () => idToString(msg.id) || (msg.id && String(msg.id)));
    tryField('chatId', () => msg.id && idToString(msg.id.remote));
    tryField('fromMe', () => msg.id && msg.id.fromMe);
    tryField('from', () => idToString(msg.from));
    tryField('to', () => idToString(msg.to));
    tryField('author', () => idToString(msg.author));
    tryField('timestamp', 't', 'timestamp');
    tryField('type', 'type');
    tryField('subtype', 'subtype');
    tryField('body', 'body');
    tryField('caption', 'caption');
    tryField('notifyName', 'notifyName', () => msg.senderObj && (msg.senderObj.name || msg.senderObj.shortName || msg.senderObj.pushname));
    tryField('senderName', 'senderName', () => msg.senderObj && (msg.senderObj.name || msg.senderObj.shortName || msg.senderObj.pushname));
    tryField('ack', 'ack');

    tryField('linkPreviewTitle', 'title');
    tryField('linkPreviewDescription', 'description');
    tryField('linkPreviewUrl', 'canonicalUrl', 'matchedText');
    tryField('linkPreviewThumbnail', 'thumbnail');

    tryField('senderObj', () => {
      const s = msg.senderObj;
      if (!s) return undefined;
      return {
        name: s.name,
        shortName: s.shortName,
        pushname: s.pushname,
        phoneNumber: s.phoneNumber ? { user: s.phoneNumber.user } : undefined,
      };
    });

    try {
      const quoted =
        typeof msg.quotedMsgObj === 'function' ? msg.quotedMsgObj() : msg.quotedMsg;
      if (quoted) {
        convenience.quoted = {
          id: idToString(quoted.id),
          from: idToString(quoted.from),
          body: quoted.body,
        };
      } else if (msg.quotedStanzaID) {
        convenience.quoted = {
          id: msg.quotedStanzaID,
          from: idToString(msg.quotedParticipant),
        };
      }
    } catch {
      /* best-effort only */
    }

    return {
      capturedAt: Date.now(),
      convenience,
      raw: safeShallowAttributes(msg),
    };
  }

  function post(entry) {
    document.dispatchEvent(new CustomEvent('__wa_notify_event__', { detail: entry }));
  }

  /** Collections that already carry our listener, so attaching twice cannot duplicate events. */
  const attachedCollections = new WeakSet();

  /**
   * Accept whatever a person might paste into the console: the store ({Msg}), a bare Msg collection,
   * or a module's exports. Returns a normalized store, or null when it holds no message collection.
   */
  function normalizeStore(input) {
    if (!input || typeof input !== 'object') return null;
    if (input.Msg && typeof input.Msg.on === 'function') return input;
    return (
      extractStoreFrom(input) ||
      extractStoreFrom(input.default) ||
      (looksLikeMsgCollection(input) ? { Msg: input, Chat: null, __synthetic: true } : null)
    );
  }

  /** @returns {boolean} true when a listener is (now) attached */
  function attach(input) {
    const store = normalizeStore(input);
    if (!store) {
      warn('attach: not a message collection, a {Msg} store, or a module exporting one');
      return false;
    }
    if (attachedCollections.has(store.Msg)) {
      log('already listening on this collection');
      return true;
    }
    attachedCollections.add(store.Msg);

    log('message store located — attaching listener', store.__synthetic ? '(direct collection)' : '(aggregator)');
    window.__WA_NOTIFY_STORE__ = store; // exposed for debugging

    store.Msg.on('add', (msg) => {
      try {
        post(extractMessage(msg));
      } catch (e) {
        warn('failed to process an incoming message', e);
      }
    });

    log('listening for new messages');
    return true;
  }

  // ---------------------------------------------------------------------
  // Debug helpers, usable from the devtools console at any time.
  // ---------------------------------------------------------------------
  window.__waNotifyDebug = function () {
    const candidates = [...registry.entries()]
      .filter(([id]) => CANDIDATE_PATTERN.test(String(id)))
      .map(([id, meta]) => ({ id, ...meta }));
    console.log(TAG, `debug: ${registry.size} module(s) registered so far, ${candidates.length} candidate(s):`);
    console.table(candidates);
    return candidates;
  };

  window.__waNotifyRequire = function (id) {
    try {
      const mod = window.require(id);
      console.log(TAG, `require("${id}") =`, mod);
      const store = extractStoreFrom(mod, id) || extractStoreFrom(mod && mod.default, id);
      if (store) console.log(TAG, `"${id}" looks like a match — call window.__waNotifyAttach(window.__waNotifyRequire("${id}")) style manually, or just wait for auto-detect.`);
      return mod;
    } catch (e) {
      console.warn(TAG, `require("${id}") threw`, e);
      return undefined;
    }
  };

  window.__waNotifyAttach = attach;

  startPolling();
})();
