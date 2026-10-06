> **Historical document** — written before later changes; may be outdated. See [KNOWN_ISSUES.md](../KNOWN_ISSUES.md) for the current state.

# wa-notify pipeline — issues found & proposed architecture

Scope: `extension/` (Chrome MV3 extension hooking WhatsApp Web), `local-server/server.js`
(Node relay to disk), and the `tools/*.py` scripts (reel detection, download, AI summary,
fuzzel menu, digest) plus the systemd/hyprland/mako glue.

---

## 1. Browser extension

### 1.1 Race condition in the persisted queue — *high impact*
`background.js`'s `enqueue()` does read‑modify‑write against `chrome.storage.local`
with no locking:

```js
async function enqueue(entry) {
  const queue = await getQueue();   // read
  queue.push(entry);                // modify
  await setQueue(queue);            // write
  flushQueue();
}
```

The manifest's own description calls out "bursts" as a case this needs to handle —
but a burst is exactly when this breaks. If two `wa-notify-message` runtime messages
arrive close together, both calls can read the same queue snapshot before either has
written back, and the second write overwrites the first — one message vanishes with
no error, no log line, nothing.

**Fix:** serialize all queue mutations through a single in‑process promise chain
(a trivial mutex) inside the service worker so writes never overlap:

```js
let writeChain = Promise.resolve();
function enqueue(entry) {
  writeChain = writeChain.then(async () => {
    const queue = await getQueue();
    queue.push(entry);
    await setQueue(queue);
  }).catch(e => console.error('[wa-notify] enqueue failed', e));
  writeChain.then(flushQueue);
}
```

### 1.2 No `unlimitedStorage` permission, and quota errors are unhandled
`manifest.json` only requests `["storage", "alarms"]`. Default `chrome.storage.local`
quota is small (~10MB), and queued entries include full `raw: safeSerialize(msg)`
dumps plus link‑preview thumbnails. If the local server is down for a while (laptop
closed, forgot to start it), the queue can hit quota — and since neither `enqueue()`
nor `setQueue()` catch errors, the rejection becomes an unhandled promise rejection
in the service worker: messages are silently dropped with no visible failure.

**Fix:** add `"unlimitedStorage"` to `permissions`, and wrap the storage write in
try/catch with an explicit fallback (drop‑oldest + a visible counter/badge), not a
silent throw.

### 1.3 No freshness filtering — the biggest correctness gap
`attach()` subscribes to every `add` event on the `Msg` collection:

```js
store.Msg.on('add', (msg) => { post(extractMessage(msg)); });
```

`add` fires for **any** message entering the in‑memory collection — including the
history WhatsApp Web loads when it boots, or when you open/scroll an old chat, not
just messages that just arrived. Nothing anywhere in the pipeline distinguishes
"this just happened" from "this got loaded because I opened a chat from 3 months
ago." Downstream, that means the very first run (or just scrolling an old
conversation) can replay months of Instagram reels as if they all just arrived:
a wall of desktop notifications, a burst of `yt-dlp` downloads, and a burst of
billed Gemini summarization calls, all for content that isn't new.

**Fix:** tag every captured message with a freshness flag computed from WhatsApp's
own message timestamp vs. capture time, e.g. `isLikelyLive = (Date.now()/1000 - msg.t) < 120`,
and have every consumer (archiving vs. alerting) treat that flag differently —
archive everything, but only notify/download/summarize when `isLikelyLive` is true.

### 1.4 Alarm period below Chrome's minimum
```js
chrome.alarms.create('wa-notify-flush', { periodInMinutes: 0.5 });
```
Chrome enforces a 1‑minute minimum period for alarms in a normally‑installed
extension; `0.5` will not fire twice as often as intended, it'll just quietly behave
like `1`. Not fatal (every `enqueue()` also triggers an immediate flush), but the
"fast retry" cadence the code implies doesn't actually exist.

**Fix:** set `periodInMinutes: 1` and don't rely on the alarm for time‑sensitive
retries — it's a backstop, not the primary path.

### 1.5 `__d` interceptor can mask itself as "already installed"
The getter/setter on `window.__d` returns a fully working `wrappedD` *before*
WhatsApp assigns the real one. If any part of WhatsApp's own bootstrap does an
idempotency check like `if (typeof window.__d !== 'function') installLoader()`,
that check now sees a function and may skip its own setup in some builds. The
existing fallback of checking `WELL_KNOWN_MODULES` directly via `window.require`
provides a safety net for the common case, but the dynamic‑registry path (used when
module names drift) has no such backstop.

**Fix:** low‑risk mitigation — log a warning if `realD` is still `null` after a
short timeout post‑`document_start`, so a broken hook is at least visible in the
console instead of failing silently for weeks.

### 1.6 Minor: origin check missing on `postMessage` listener
`content.js` checks `event.source !== window` but not `event.origin`. Low risk on
`web.whatsapp.com`, but a one‑line defense‑in‑depth fix:
```js
if (event.origin !== 'https://web.whatsapp.com') return;
```

---

## 2. Local server (`local-server/server.js`)

### 2.1 No authentication + wildcard CORS on a server that logs private chats — *high impact*
```js
res.setHeader('Access-Control-Allow-Origin', '*');
```
with no shared secret anywhere in the request path. Any web page open in the same
browser — not just WhatsApp Web — can `fetch('http://127.0.0.1:8765/log', {method:'POST', body: ...})`
and either inject fabricated entries into your permanent chat archive, or flood it
to fill disk. Binding to `127.0.0.1` protects you from the *network*, not from other
tabs in your own browser.

**Fix:** generate a random token once (e.g. on first server start), store it in the
extension's storage and in a local config file; require it as a header
(`X-WA-Notify-Token`) on `/log`; restrict CORS to the extension's real origin
(`chrome-extension://<id>`) instead of `*`.

### 2.2 No de‑duplication by message id — *high impact, compounds §1.3*
The server blindly appends every batch it receives:
```js
function appendEntries(entries) {
  const lines = entries.map((e) => JSON.stringify(e)).join('\n') + '\n';
  fs.appendFileSync(DATA_FILE, lines, 'utf8');
}
```
Combined with §1.3, every WhatsApp Web reload/hydration re‑appends messages the
archive has already seen, so the file grows with duplicates forever, and every
downstream reader re‑does the "is this a new reel?" question from scratch.

**Fix:** key inserts by message id (`INSERT OR IGNORE` with a `UNIQUE`/`PRIMARY KEY`
constraint) so re‑delivery is naturally idempotent — this is much easier once the
storage is a real database (see architecture section).

### 2.3 Synchronous disk write inside the request handler
`fs.appendFileSync` blocks Node's single event‑loop thread for the write duration.
Harmless today (small batches, one client), but it will start adding latency as the
archive file grows or if the disk is slow (encrypted volume, network drive).

**Fix:** use `fs.promises.appendFile`, or move to a proper embedded DB.

### 2.4 No rotation/retention for `chat_archive.jsonl`
Single ever‑growing file for the tool's entire lifetime. Fine at first, awkward
forever after. **Fix:** rotate monthly, or store in SQLite where old rows can be
pruned/exported without file surgery.

---

## 3. Python tools (`wa-reel-alert.py`, `wa-reel-menu.py`, `wa-reel-digest.py`, `wa_reel_ai.py`)

### 3.1 Core helpers triplicated, with inconsistent signatures — *maintainability risk*
`get_reel_id`, `load_opened_ids`, and especially `mark_as_opened` are reimplemented
in all three scripts — and `mark_as_opened` doesn't even mean the same thing in each:

| File | `mark_as_opened(...)` takes |
|---|---|
| `wa-reel-alert.py` | a full `url` |
| `wa-reel-menu.py` | a full `url` |
| `wa-reel-digest.py` | a `reel_id` directly |

Every path constant (`REELS_ARCHIVE_PATH`, `OPENED_FILE`, etc.) is also
re‑declared three times, so the three scripts can silently drift apart the next
time one of them is edited.

**Fix:** extract a shared `walib.py` (path constants, id parsing, opened‑state,
mpv launch, notification formatting) and import it from all three call sites.

### 3.2 Non‑atomic state writes + broad exception swallowing
```python
def load_opened_ids():
    ...
    except Exception:
        return set()
```
`opened_reels.json` is written with a plain `open(path, 'w')` + `json.dump`, not a
temp‑file‑then‑rename. A crash or power loss mid‑write leaves invalid JSON, and the
blanket `except Exception: return set()` treats that as "nothing has ever been
opened" — every previously‑seen reel resurfaces and renotifies at once.

**Fix:** atomic writes (write to `opened_reels.json.tmp`, then `os.replace`), or —
better — move this state into the same store as the reels themselves so there's
nothing to get out of sync.

### 3.3 `wa-reel-digest.py` blocks synchronously on AI calls in a loop
```python
for idx, r in enumerate(reels, 1):
    summary = get_cached_summary(reel_id) or summarize_reel(reel_id)
```
For several un‑cached reels, each with up to ~45s × 2 retries × 2 model fallbacks
of worst‑case latency, running the digest can hang for minutes before printing or
notifying about *anything*.

**Fix:** summarize concurrently (thread pool / asyncio), or better, let the
always‑running alert daemon generate summaries continuously in the background so
the digest only ever reads cache.

### 3.4 Inline base64 video upload can exceed the API's payload ceiling
`wa_reel_ai.py` always base64‑encodes the whole video file into the JSON request
body. Longer or higher‑bitrate reels can exceed the API's inline‑payload limit —
summarization then fails silently for exactly the content most worth summarizing.

**Fix:** check file size and switch to a file‑upload‑then‑reference flow above a
threshold instead of always inlining.

### 3.5 File‑tailing reimplements what a real store gives you for free
`wa-reel-alert.py`'s watch loop hand‑rolls rotation/truncation detection via
`os.stat` + inode comparisons on a 200ms poll. It works, but it's bespoke
bookkeeping that exists purely because the source of truth is a raw append‑only
file rather than something queryable — see the architecture section.

### 3.6 No freshness filtering here either (mirrors §1.3)
`--replay 0` (the default) protects the very first `tail_archive()` call from
replaying old lines on startup, but the *live* tail has no way to tell "this
message just arrived" from "this message arrived because I scrolled up a chat 3
minutes ago" — nothing marks archive rows as historical vs. live at the point of
capture.

---

## 4. Operational config

- **No cleanup job** for downloaded reel videos or generated summaries under
  `~/.local/share/wa-notify/` — disk usage grows without bound. A periodic systemd
  timer that deletes videos for reels already marked opened after N days would fix
  this cheaply.
- **Hyprland window rule** hardcodes an exact pixel offset "for 1080p" — will
  misplace the floating player on any other resolution or multi‑monitor layout.
- **Plaintext, unencrypted, unbounded retention** of full chat content — reasonable
  for a personal tool, but worth being deliberate about, especially if the home
  directory is cloud‑backed (that silently extends WhatsApp history retention to
  wherever the backup goes).
- **`wa-reel-alert.service`** has no explicit `After=wa-notify-server.service` — 
  harmless today since the script just polls until the archive file exists, but
  worth stating the dependency for clarity.

---

## 5. Proposed architecture

The current design's problems mostly trace back to two root causes:
**(a)** the same "is this new?" question is answered independently — and
inconsistently — at four different layers (extension, server, alert daemon, menu),
and **(b)** state lives in loosely‑coupled flat files (`chat_archive.jsonl`,
`reels.jsonl`, `opened_reels.json`) that nothing enforces consistency between.

The fix is to answer "is this new?" **once**, as close to the source as possible,
and to make the rest of the pipeline read from one durable, queryable store instead
of re‑deriving state from raw files.

**Capture layer (browser extension) — same technique, hardened**
Keep the Haste‑hook approach (there's no better option for tapping WhatsApp Web's
internals), but: request `unlimitedStorage`, serialize queue writes with a mutex
(§1.1), tag every captured message with `capturedAt` + `messageTs` so freshness can
be computed downstream, and sign requests with a shared token.

**Ingestion service (Node, local) — becomes the single source of truth**
Replace the flat‑file archive with a small SQLite database
(`messages` and `reels` tables). Inserts are keyed by WhatsApp message id
(`INSERT OR IGNORE`), which makes replayed/duplicate deliveries a no‑op instead of
disk growth. The reel‑URL regex extraction moves here too, computed once at
ingestion time, with an `is_fresh` column derived from `now - message_ts` — so
every consumer downstream gets a clean, already‑deduplicated, already‑classified
feed instead of re‑parsing raw text and re‑guessing freshness. This also removes
the need for hand‑rolled inode/truncation tracking (§3.5) — a consumer just
remembers the last row id it processed and does `SELECT ... WHERE id > ?`.

**Reel worker (Python daemon)** subscribes to *fresh* rows only — this is the only
place `yt-dlp`, the Gemini summarizer, `mpv`, and `notify-send` get invoked, so the
"only alert on things that actually just happened" rule is enforced in exactly one
place instead of being (re‑)implemented per script.

**CLI tools (menu, digest)** become thin, read‑only SQL queries against the same
database — opened‑state lives as a column on the `reels` table rather than a
separate JSON file, so there's no longer a second file that can drift out of sync
or get corrupted independently.

**Shared library (`walib.py`)** holds the one implementation of id parsing, mpv
launch, and notification formatting that all three Python entry points import,
eliminating the triplicated/inconsistent helpers in §3.1.

**Retention timer (systemd)** periodically deletes downloaded videos and summaries
for reels already marked opened past N days, and can archive/export old DB rows
without needing file surgery.

This keeps the same workflow end‑to‑end (WhatsApp → capture → detect reel → notify
→ play/download → summarize → browse via menu/digest) — it just removes the four
independent, inconsistent implementations of "is this message new and should I do
something about it," and replaces four loosely‑related flat files with one store
that every component reads and writes through.
