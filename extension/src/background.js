/**
 * wa-notify — background.js (MV3 service worker)
 *
 * Receives captured messages from content.js, keeps them in a durable queue in chrome.storage.local
 * and relays batches to the local ingestion server with a shared token.
 *
 * Every read-modify-write of the stored queue goes through ONE promise mutex (`exclusive`):
 * enqueueing a message AND removing a delivered batch. Removing a batch from a stale copy of the
 * queue used to overwrite messages enqueued while the request was in flight (lost messages).
 * Delivery is at-least-once; the server de-duplicates by message id.
 */

// ---- configuration -------------------------------------------------------------------------
// The port is fixed in the manifest's host_permissions too. To use another endpoint, store it under
// ENDPOINT_KEY in chrome.storage.local (and add the host to the manifest).
const DEFAULT_ENDPOINT = 'http://127.0.0.1:8765/log';
const ENDPOINT_KEY = 'wa_notify_endpoint';
const QUEUE_KEY = 'wa_notify_queue';
const TOKEN_KEY = 'wa_notify_auth_token';
const MAX_QUEUE = 10000; // oldest entries are dropped beyond this
const BATCH_SIZE = 25;
const FETCH_TIMEOUT_MS = 30000;
const FLUSH_ALARM = 'wa-notify-flush';
const FLUSH_PERIOD_MINUTES = 1; // Chrome's minimum alarm period
const WARN_INTERVAL_MS = 5 * 60 * 1000; // repeat a delivery warning at most this often
const TAG = '[wa-notify]';

// ---- queue storage ---------------------------------------------------------------------------

/** Serializes every queue mutation. Errors reach the caller but never break the chain. */
let chain = Promise.resolve();
function exclusive(task) {
  const result = chain.then(task);
  chain = result.catch(() => {});
  return result;
}

async function getQueue() {
  const { [QUEUE_KEY]: queue = [] } = await chrome.storage.local.get(QUEUE_KEY);
  return queue;
}

async function setQueue(queue) {
  await chrome.storage.local.set({ [QUEUE_KEY]: queue.slice(-MAX_QUEUE) });
}

/** Append one entry to the stored queue. */
function enqueue(entry) {
  return exclusive(async () => {
    const queue = await getQueue();
    queue.push(entry);
    await setQueue(queue);
  }).catch((e) => console.error(TAG, 'could not store message (storage quota?):', e));
}

/** Number of leading entries of `queue` that are exactly the entries of `batch`, in order. */
function sharedPrefix(queue, batch) {
  let n = 0;
  while (n < batch.length && n < queue.length && JSON.stringify(queue[n]) === JSON.stringify(batch[n])) n++;
  return n;
}

/** Remove a delivered batch from the CURRENT stored queue (never from a stale snapshot). */
function removeDelivered(batch) {
  return exclusive(async () => {
    const queue = await getQueue();
    await setQueue(queue.slice(sharedPrefix(queue, batch)));
  });
}

// ---- pairing token ---------------------------------------------------------------------------

/**
 * The shared secret sent as X-WA-Notify-Token. Generated once and kept in extension storage; the
 * server pairs with it on first use. Throws if it cannot be read or stored, so nothing is sent
 * without a real token (fail closed).
 */
async function getOrInitToken() {
  const data = await chrome.storage.local.get(TOKEN_KEY);
  if (data[TOKEN_KEY]) return data[TOKEN_KEY];
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  const token = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  await chrome.storage.local.set({ [TOKEN_KEY]: token });
  console.log(TAG, 'generated client auth token for pairing');
  return token;
}

// ---- delivery status (kept visible instead of failing silently) -------------------------------

let consecutiveFailures = 0;
let lastWarningAt = 0;

function setBadge(text) {
  try {
    chrome.action?.setBadgeText?.({ text });
    if (text) chrome.action?.setBadgeBackgroundColor?.({ color: '#c0392b' });
  } catch {
    /* the badge is cosmetic */
  }
}

function reportFailure(message) {
  consecutiveFailures++;
  setBadge('!');
  const now = Date.now();
  if (now - lastWarningAt >= WARN_INTERVAL_MS) {
    lastWarningAt = now;
    console.warn(TAG, `delivery failing (${consecutiveFailures} in a row): ${message}`);
  }
}

function reportSuccess() {
  if (consecutiveFailures > 0) {
    consecutiveFailures = 0;
    setBadge('');
  }
}

function describeStatus(status) {
  if (status === 401) {
    return 'server rejected the token (401). To re-pair, delete token.txt in the server config dir and reload the extension';
  }
  return `server responded ${status}`;
}

// ---- delivery --------------------------------------------------------------------------------

async function getEndpoint() {
  const data = await chrome.storage.local.get(ENDPOINT_KEY);
  return data[ENDPOINT_KEY] || DEFAULT_ENDPOINT;
}

/** POST one batch. Returns true only for a 2xx answer. */
async function postBatch(batch, token) {
  try {
    const res = await fetch(await getEndpoint(), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-WA-Notify-Token': token },
      body: JSON.stringify({ entries: batch }),
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    if (!res.ok) {
      reportFailure(describeStatus(res.status));
      return false;
    }
    reportSuccess();
    return true;
  } catch (e) {
    reportFailure(`${e.message} (is the local server running?)`);
    return false;
  }
}

let flushing = false;

/** Send queued entries in batches until the queue is empty or a batch fails. */
async function flushQueue() {
  if (flushing) return;
  flushing = true;
  try {
    let token;
    try {
      token = await getOrInitToken();
    } catch (e) {
      reportFailure(`cannot read or create the pairing token, not sending anything: ${e.message}`);
      return;
    }
    for (;;) {
      const batch = (await getQueue()).slice(0, BATCH_SIZE);
      if (batch.length === 0) return;
      if (!(await postBatch(batch, token))) return; // the alarm retries
      await removeDelivered(batch);
    }
  } catch (e) {
    console.error(TAG, 'flush error:', e);
  } finally {
    flushing = false;
  }
}

// ---- wiring ----------------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (sender && sender.id && chrome.runtime.id && sender.id !== chrome.runtime.id) return;
  if (msg?.type !== 'wa-notify-message' || msg.payload === null || typeof msg.payload !== 'object') return;
  enqueue(msg.payload).then(flushQueue);
});

// Chrome does not re-inject declared content scripts into tabs that were already open when the extension is
// installed or reloaded, and the old copy running there is orphaned (it can no longer reach this worker). Attach
// a fresh relay to those tabs; content.js is safe to run twice. Only the relay is needed: injected.js keeps
// running in the page and keeps dispatching events.
const WHATSAPP_TABS = 'https://web.whatsapp.com/*';

async function reattachRelays() {
  const tabs = await chrome.tabs.query({ url: WHATSAPP_TABS });
  for (const tab of tabs) {
    try {
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['src/content.js'] });
    } catch (e) {
      console.warn(TAG, `could not attach to WhatsApp tab ${tab.id} (reload the tab): ${e.message}`);
    }
  }
}

chrome.runtime.onInstalled.addListener((details) => {
  if (details && (details.reason === 'install' || details.reason === 'update')) {
    reattachRelays().catch((e) => console.warn(TAG, 'reattaching to open tabs failed:', e.message));
  }
});

// Backstop for a server that was down: retry every minute.
chrome.alarms.create(FLUSH_ALARM, { periodInMinutes: FLUSH_PERIOD_MINUTES });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === FLUSH_ALARM) flushQueue();
});

flushQueue(); // drain whatever a previous worker left behind
