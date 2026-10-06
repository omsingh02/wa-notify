'use strict';

/**
 * Human-readable sender label for a captured message.
 *
 * WhatsApp Web does not always expose a display name (privacy "LID" identifiers, channels, groups),
 * so this walks a list of fallbacks. It is a heuristic: the label is for display only and the raw
 * identifiers are always kept in the stored JSON.
 */

const E164_JID = /^(\d{7,15})@(c\.us|s\.whatsapp\.net)$/;
const CHANNEL_NAME_IN_TITLE = /by\s+([^|•\n]+)/i;
const MAX_CHANNEL_TITLE_LENGTH = 30;

/** @param {unknown} v @returns {string} trimmed string, or '' when v is not a non-blank string */
function nonBlank(v) {
  return typeof v === 'string' && v.trim() ? v.trim() : '';
}

/**
 * @param {object} c    the `convenience` object of a captured entry
 * @param {object} [raw] the `raw` object of a captured entry
 * @returns {string}
 */
function cleanSender(c, raw) {
  if (c.fromMe) return 'You';

  const named = nonBlank(c.notifyName) || nonBlank(c.senderName);
  if (named) return named;

  const sender = c.senderObj || (raw && raw.senderObj);
  if (sender) {
    const label = nonBlank(sender.name) || nonBlank(sender.shortName) || nonBlank(sender.pushname);
    if (label) return label;
    if (sender.phoneNumber && sender.phoneNumber.user) return `+${sender.phoneNumber.user}`;
  }

  const from = String(c.from || c.author || c.chatId || 'Unknown');
  const chat = String(c.chatId || '');

  // WhatsApp Channel / newsletter: the channel name is only available through the link-preview title.
  if (chat.endsWith('@newsletter') || from.endsWith('@newsletter')) {
    const title = typeof c.linkPreviewTitle === 'string' ? c.linkPreviewTitle : '';
    if (title) {
      const byline = title.match(CHANNEL_NAME_IN_TITLE);
      if (byline) return `Channel: ${byline[1].trim()}`;
      if (title.length < MAX_CHANNEL_TITLE_LENGTH) return `Channel: ${title}`;
    }
    return 'WhatsApp Channel';
  }

  if (chat.endsWith('@g.us')) return from.endsWith('@lid') ? 'Group Member' : 'Group';
  if (from.endsWith('@lid')) return 'Contact';

  const phone = from.match(E164_JID);
  if (phone) return `+${phone[1]}`;

  return from.split('@')[0] || 'Unknown';
}

module.exports = { cleanSender };
