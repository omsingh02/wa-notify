'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { findReelUrls, extractReelId, cleanReelUrl, isLikelyLive } = require('../lib/reels');
const { cleanSender } = require('../lib/sender');

test('reel URLs: reel, reels and p paths are found, with or without www/query/trailing slash', () => {
  const text = [
    'a https://www.instagram.com/reel/AbC_123-xyz/?igsh=abc',
    'b http://instagram.com/reels/Zzz999/',
    'c https://www.instagram.com/p/PostId1',
    'd https://www.instagram.com/reel/AbC_123-xyz/ (duplicate)',
  ].join(' ');
  const urls = findReelUrls(text);
  assert.equal(urls.length, 3, 'duplicates are collapsed');
  assert.deepEqual(urls.map(extractReelId).sort(), ['AbC_123-xyz', 'PostId1', 'Zzz999']);
});

test('reel URLs: look-alike hosts and non-reel paths are not matched', () => {
  assert.deepEqual(findReelUrls('https://instagram.com.evil.example/reel/abc'), []);
  assert.deepEqual(findReelUrls('https://notinstagram.com/reel/abc'), []);
  assert.deepEqual(findReelUrls('https://www.instagram.com/someuser/'), []);
  assert.deepEqual(findReelUrls('https://www.instagram.com/stories/someuser/123/'), []);
  assert.deepEqual(findReelUrls(null), []);
});

test('reel ids and clean URLs', () => {
  assert.equal(extractReelId('https://www.instagram.com/reel/AAAA/?x=1'), 'AAAA');
  assert.equal(cleanReelUrl('https://www.instagram.com/reel/AAAA/?x=1'), 'https://www.instagram.com/reel/AAAA');
});

test('freshness: within two minutes is live, in either direction', () => {
  const now = 1_700_000_000_000;
  assert.equal(isLikelyLive(now, 1_700_000_000), true);
  assert.equal(isLikelyLive(now, 1_700_000_000 - 119), true);
  assert.equal(isLikelyLive(now, 1_700_000_000 + 119), true, 'a slightly fast clock still counts');
  assert.equal(isLikelyLive(now, 1_700_000_000 - 120), false);
  assert.equal(isLikelyLive(now, 1_700_000_000 - 3600), false);
});

test('sender labels', () => {
  assert.equal(cleanSender({ fromMe: true }, {}), 'You');
  assert.equal(cleanSender({ notifyName: '  Alice ' }, {}), 'Alice');
  assert.equal(cleanSender({ senderName: 'Bob' }, {}), 'Bob');
  assert.equal(cleanSender({ senderObj: { shortName: 'Cy' } }, {}), 'Cy');
  assert.equal(cleanSender({ senderObj: { phoneNumber: { user: '911234567890' } } }, {}), '+911234567890');
  assert.equal(cleanSender({ from: '911234567890@c.us', chatId: '911234567890@c.us' }, {}), '+911234567890');
  assert.equal(cleanSender({ from: '12345@lid', chatId: '12345@lid' }, {}), 'Contact');
  assert.equal(cleanSender({ from: '123-456@g.us', chatId: '123-456@g.us' }, {}), 'Group');
  assert.equal(cleanSender({ from: '12345@lid', chatId: '123-456@g.us' }, {}), 'Group Member');
  assert.equal(cleanSender({ from: 'x@broadcast', chatId: 'x@broadcast' }, {}), 'x');
  assert.equal(cleanSender({}, {}), 'Unknown');
});

test('sender labels: channels are named from the link-preview title heuristic only', () => {
  const channel = (title) => cleanSender({ chatId: '1@newsletter', from: '1@newsletter', linkPreviewTitle: title }, {});
  assert.equal(channel('Breaking news by Some Channel | Site'), 'Channel: Some Channel');
  assert.equal(channel('Short title'), 'Channel: Short title');
  assert.equal(channel('A very long article title that is certainly not a channel name'), 'WhatsApp Channel');
  assert.equal(channel(undefined), 'WhatsApp Channel');
});
