'use strict';

/**
 * Instagram reel / post URL detection and message-freshness helpers.
 * Pure functions, no I/O, so they can be unit-tested directly.
 */

/** Matches https://instagram.com/{reel|reels|p}/<shortcode>, optionally with www. Query strings are not captured. */
const REEL_URL_SOURCE = String.raw`https?:\/\/(?:www\.)?instagram\.com\/(?:reel|reels|p)\/([A-Za-z0-9_-]+)`;

/** A message counts as "live" when it was captured within this many seconds of its own timestamp. */
const LIVE_WINDOW_SECONDS = 120;

/**
 * All distinct Instagram reel/post URLs found in a piece of text.
 * @param {string} text
 * @returns {string[]}
 */
function findReelUrls(text) {
  const matches = String(text || '').match(new RegExp(REEL_URL_SOURCE, 'gi'));
  return [...new Set(matches || [])];
}

/**
 * Strip the query string and a trailing slash from a URL.
 * @param {string} url
 * @returns {string}
 */
function cleanReelUrl(url) {
  return url.split('?')[0].replace(/\/$/, '');
}

/**
 * The shortcode (last path segment) of a reel/post URL.
 * @param {string} url
 * @returns {string}
 */
function extractReelId(url) {
  const clean = cleanReelUrl(url);
  const parts = clean.split('/');
  return parts[parts.length - 1] || clean;
}

/**
 * Whether a message was captured (roughly) when it was sent, as opposed to being history that the
 * WhatsApp Web UI loaded later. The comparison is symmetric so a slightly skewed clock still counts.
 * @param {number} capturedAtMs   extension capture time, epoch milliseconds
 * @param {number} messageTsSec   WhatsApp's own message timestamp, epoch seconds
 * @param {number} [windowSeconds]
 * @returns {boolean}
 */
function isLikelyLive(capturedAtMs, messageTsSec, windowSeconds = LIVE_WINDOW_SECONDS) {
  return Math.abs(Math.floor(capturedAtMs / 1000) - messageTsSec) < windowSeconds;
}

module.exports = { REEL_URL_SOURCE, LIVE_WINDOW_SECONDS, findReelUrls, cleanReelUrl, extractReelId, isLikelyLive };
