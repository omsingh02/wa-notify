/**
 * wa-notify — content.js
 * Runs in the ISOLATED world (normal content script context). Its only job is to relay messages
 * dispatched by injected.js (MAIN world) to the background service worker, which is the only part
 * allowed to make network requests without hitting WhatsApp's page CSP.
 *
 * Any script in the page can dispatch the relay event, so only well-formed capture entries are
 * forwarded; the background worker and the server validate again.
 */
const RELAY_EVENT = '__wa_notify_event__';

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

document.addEventListener(RELAY_EVENT, (event) => {
  const detail = event.detail;
  if (!isCaptureEntry(detail)) return;

  try {
    // After the extension is reloaded, a stale content script throws "Extension context invalidated".
    const sending = chrome.runtime.sendMessage({ type: 'wa-notify-message', payload: detail });
    if (sending && typeof sending.catch === 'function') {
      sending.catch((err) => console.error('[wa-notify] failed to relay message to background', err));
    }
  } catch (err) {
    console.error('[wa-notify] failed to relay message to background', err);
  }
});

console.log('[wa-notify] Content script relay initialized');
