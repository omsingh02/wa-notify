/**
 * wa-notify — content.js
 * Runs in the ISOLATED world (normal content script context). Its only job is to relay messages
 * dispatched by injected.js (MAIN world) to the background service worker, which is the only part
 * allowed to make network requests without hitting WhatsApp's page CSP.
 *
 * Any script in the page can dispatch the relay event, so only well-formed capture entries are
 * forwarded; the background worker and the server validate again.
 *
 * Extension reloads: Chrome leaves the old copy of this script running in tabs that were already open,
 * but it can no longer reach the extension (chrome.runtime.id disappears and sendMessage throws
 * "Extension context invalidated"). Such an orphan stops relaying after one clear warning instead of
 * throwing on every message. background.js attaches a fresh copy to the open WhatsApp tabs when the
 * extension is installed or updated, so this file must be safe to run twice in the same tab.
 */
(() => {
  const TAG = '[wa-notify]';
  const RELAY_EVENT = '__wa_notify_event__';
  const INSTANCE_KEY = '__waNotifyRelay';

  let stopped = false;

  /** True while this script can still reach its extension. */
  const contextAlive = () => Boolean(globalThis.chrome && chrome.runtime && chrome.runtime.id);

  const previous = globalThis[INSTANCE_KEY];
  if (previous && previous.isAlive()) return; // a live relay is already installed in this tab

  /** A capture entry is an object carrying at least a `convenience` object. */
  function isCaptureEntry(detail) {
    return (
      detail !== null &&
      typeof detail === 'object' &&
      !Array.isArray(detail) &&
      detail.convenience !== null &&
      typeof detail.convenience === 'object'
    );
  }

  const isInvalidated = (err) => /context invalidated/i.test(String((err && err.message) || err));

  /** Give up quietly: this copy belongs to an extension instance that no longer exists. */
  function stop() {
    stopped = true;
    document.removeEventListener(RELAY_EVENT, onEvent);
    console.warn(
      `${TAG} The extension was reloaded or updated, so this tab can no longer reach it. ` +
        'Reload this WhatsApp Web tab to resume capturing.'
    );
  }

  function onEvent(event) {
    const detail = event.detail;
    if (!isCaptureEntry(detail)) return;
    if (!contextAlive()) return stop();

    try {
      const sending = chrome.runtime.sendMessage({ type: 'wa-notify-message', payload: detail });
      if (sending && typeof sending.catch === 'function') {
        sending.catch((err) => {
          if (isInvalidated(err)) stop();
          else console.error(TAG, 'failed to relay message to background', err);
        });
      }
    } catch (err) {
      // A synchronous throw: a .catch() on the returned promise would never see it.
      if (isInvalidated(err)) stop();
      else console.error(TAG, 'failed to relay message to background', err);
    }
  }

  document.addEventListener(RELAY_EVENT, onEvent);
  globalThis[INSTANCE_KEY] = { isAlive: () => !stopped && contextAlive() };
  console.log(`${TAG} Content script relay initialized`);
})();
