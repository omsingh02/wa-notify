> **Historical document** — written before later changes; may be outdated. See [KNOWN_ISSUES.md](../KNOWN_ISSUES.md) for the current state.

# Implementation Plan: wa-notify v2 Architecture Overhaul (rev 2)

Transition the wa-notify and Instagram reel alerting pipeline from loosely-coupled flat
files to a robust, unified SQLite-backed architecture, resolving queue race conditions,
chat-scroll notification flooding, and code duplication as documented in
[wa-notify-audit-and-architecture.md](audit-and-architecture.md).

## Changes from rev 1

- **Freshness is now computed once, server-side**, from the `capturedAt` field the
  extension already emits — no `injected.js` change needed, and no risk of the
  extension's and server's freshness math disagreeing after a delivery retry.
- **Added a pairing/bootstrap step** for the shared auth token — the extension can't
  read `~/.config/wa-notify/token.txt` directly, so it needs to generate and hand
  over its own token on first contact.
- **Fixed the CORS origin value** — `chrome-extension://*` isn't valid; the server
  must echo back an exact, validated `Origin`.
- **Added a one-time migration script** so existing opened/unopened state survives
  the cutover instead of every past reel resetting to "unopened."
- **Re-added the Gemini inline-payload size fix** from the audit, which had dropped
  out of scope.
- Tightened a few implementation details: polling interval for the alert daemon,
  SQLite concurrency notes for the two-writer (Node + Python) setup, and endpoint
  naming consistency.

---

## User Review Required

> [!IMPORTANT]
> - **SQLite backend**: Node's native `node:sqlite` (`DatabaseSync`), zero external
>   npm dependencies, WAL mode, DB at `~/.local/share/wa-notify/wa-notify.db`.
>   **Check your Node version first** — `node:sqlite` is a recent addition and may
>   still need `--experimental-sqlite` depending on what you have installed. If it's
>   not available without the flag, fall back to `better-sqlite3` instead of adding
>   the flag to a long-running service.
> - **Freshness filtering**: computed once, server-side, as
>   `is_likely_live = abs(capturedAt/1000 - message_ts) < 120`, using the
>   extension's own capture timestamp — not the server's receipt time — so a
>   message delivered late (e.g. after the server was briefly down) is still judged
>   by when it actually arrived, not when it happened to get ingested. Historical
>   reels are archived with `is_likely_live = 0` and never trigger notifications,
>   downloads, or Gemini calls.
> - **Auth bootstrap (new)**: the extension generates its own random token on
>   install and sends it on its first request; the server trusts whatever token
>   arrives *only if* `~/.config/wa-notify/token.txt` doesn't exist yet, then
>   persists it and requires an exact match forever after. To re-pair (e.g. after
>   reinstalling the extension), delete `token.txt` and reload the extension.
> - **Migration (new)**: a one-time script imports `reels.jsonl` +
>   `opened_reels.json` into SQLite before the new pipeline goes live, so nothing
>   that was already "opened" resurfaces as new.
> - **Backward compatibility**: `chat_archive.jsonl` keeps being appended to as a
>   flat backup during the transition — plan to remove this once the SQLite path
>   has run for a couple of weeks without issues; nothing downstream reads it once
>   the migration is done.
> - **Browser extension reload**: after updating extension files, WhatsApp Web will
>   need a refresh (`Ctrl+R` or re-toggling the extension) to load the new queue
>   mutex and pick up the pairing token.

---

## Proposed Changes

### 1. Ingestion Layer & Local Server

#### [MODIFY] [local-server/server.js](../../local-server/server.js)
- Initialize SQLite database `~/.local/share/wa-notify/wa-notify.db` in WAL mode.
  Set `PRAGMA busy_timeout = 5000` so brief lock contention with the Python side
  retries instead of erroring.
- Schema: `messages` (`PRIMARY KEY id`), `reels` (`PRIMARY KEY reel_id`, columns
  include `first_seen_at`, `message_ts`, `is_likely_live`, `is_opened`, `alerted`,
  `local_path`, `summary`, `summary_status`).
- **Auth pairing**: on `POST /log`, if `~/.config/wa-notify/token.txt` doesn't
  exist, accept the request's `X-WA-Notify-Token` value as-is and write it to that
  file; on all subsequent requests, reject (401) any request whose token doesn't
  match exactly.
- **CORS**: read the incoming `Origin` header; only echo it back verbatim in
  `Access-Control-Allow-Origin` if it matches the extension's known origin
  (`chrome-extension://<id>`, captured during pairing). Never emit a wildcarded
  scheme like `chrome-extension://*` — it isn't a valid header value. Note the
  token check is the actual security boundary here; CORS is defense-in-depth
  against other browser tabs and doesn't affect the extension's own requests
  (those bypass CORS via `host_permissions`).
- Ingest messages idempotently: `INSERT OR IGNORE INTO messages`.
- Extract reel URLs on ingestion (moves the regex work out of the Python tailer);
  `INSERT OR IGNORE INTO reels` so a re-forwarded/duplicate reel doesn't overwrite
  an already-computed `is_likely_live` on a later, stale re-send.
- Compute `is_likely_live` from the payload's own `capturedAt` field vs.
  `message_ts` — **not** from the server's wall-clock receipt time (see rationale
  above).
- Keep the existing `/log` path (verification below assumes this — rename it and
  the extension's `ENDPOINT` constant together if you'd rather call it
  `/api/messages`, but pick one and make sure both sides agree).
- Keep the `/health` endpoint from v1.

---

### 2. Browser Extension Hardening

#### [MODIFY] [extension/manifest.json](../../extension/manifest.json)
- Add `"unlimitedStorage"` to `permissions`.

#### [MODIFY] [extension/src/injected.js](../../extension/src/injected.js)
- No change needed for freshness — `capturedAt` is already emitted by
  `extractMessage()`, and the server computes `is_likely_live` from it. Keeping
  this logic server-side avoids maintaining the same freshness math in two
  languages.

#### [MODIFY] [extension/src/content.js](../../extension/src/content.js)
- Add defense-in-depth origin validation: `if (event.origin !== 'https://web.whatsapp.com') return;`

#### [MODIFY] [extension/src/background.js](../../extension/src/background.js)
- Serialize `enqueue()` through an in-memory promise mutex (`writeChain`) to
  eliminate the burst race condition.
- **Token pairing**: on first run (no token in `chrome.storage.local`), generate
  one with `crypto.getRandomValues`, store it, and send it as `X-WA-Notify-Token`
  on every request from then on.
- Set alarm period to `1` minute (Chrome's enforced minimum — `0.5` was silently
  ignored).

---

### 3. Shared Python Library

#### [NEW] [tools/walib.py](../../tools/walib.py)
- `get_db()` — opens `~/.local/share/wa-notify/wa-notify.db` with
  `PRAGMA busy_timeout` set, matching the server's WAL setup. Keep any transaction
  that wraps a network call (yt-dlp, Gemini) as short as possible — do the network
  work *outside* an open transaction, then do a quick isolated `UPDATE` after, so a
  slow download doesn't hold a write lock and starve the Node server's inserts.
- Common paths (`REELS_DIR`, `SUMMARIES_DIR`, `DB_PATH`).
- URL cleaning and `get_reel_id(url)`.
- State management (`mark_as_opened(reel_id)`, `mark_all_as_opened()`) — one
  signature, used everywhere, operating on `reel_id` consistently.
- MPV process spawning (`play_in_mpv(reel_id, url)`).
- Relative time and notification string formatting.

---

### 4. Reel Daemon & Multimodal AI

#### [MODIFY] [tools/wa-reel-alert.py](../../tools/wa-reel-alert.py)
- Import shared functions from `walib.py`.
- Replace file-tailing/inode checks with a polling loop against SQLite (there's no
  cross-process push notification for SQLite, so this is a poll, not a trigger):
  every ~2 seconds, `SELECT * FROM reels WHERE alerted = 0 ORDER BY first_seen_at ASC`.
- For each unhandled row: if `is_likely_live = 1`, fire the notification and
  pre-download as before; if `is_likely_live = 0`, just set `alerted = 1` silently
  — note this only suppresses the *notification*, the reel still shows up as
  unopened in the menu/digest for anyone who wants to catch up on it.
- When pre-download completes, update `local_path` and asynchronously trigger AI
  summarization.

#### [MODIFY] [tools/wa_reel_ai.py](../../tools/wa_reel_ai.py)
- Use `walib.py` for paths and DB connection.
- **Check file size before uploading.** For files above a threshold (e.g. ~15-18MB
  to leave margin under the API's inline limit), use the file-upload-then-reference
  flow instead of always base64-inlining the video — otherwise longer/higher-quality
  reels fail summarization silently.
- Write summaries to both `~/.local/share/wa-notify/summaries/<reel_id>.txt` and
  `UPDATE reels SET summary = ?, summary_status = 'done' WHERE reel_id = ?`.
- Keep the universal, case-free prompt and `gemini-3.5-flash` → `gemini-3.5-flash-lite`
  fallback.

---

### 5. CLI Tools & Cleanup

#### [MODIFY] [tools/wa-reel-menu.py](../../tools/wa-reel-menu.py)
- Import `walib.py`; query `SELECT * FROM reels WHERE is_opened = 0 ORDER BY message_ts DESC`.
- Display cached AI summary previews in Fuzzel.

#### [MODIFY] [tools/wa-reel-digest.py](../../tools/wa-reel-digest.py)
- Query SQLite directly; display whatever summary text exists without blocking —
  if `summary_status` isn't `done` yet, show a "not summarized yet" placeholder
  rather than calling Gemini synchronously in the loop (that job now belongs to
  the always-running alert daemon).

#### [NEW] [tools/wa-reel-cleanup.py](../../tools/wa-reel-cleanup.py) & systemd timer
- Deletes cached `.mp4` files for reels with `is_opened = 1` older than 7 days.
- `wa-reel-cleanup.service` + `wa-reel-cleanup.timer`, running daily.

#### [NEW] [tools/migrate_v1_to_v2.py](../../tools/migrate_v1_to_v2.py)
- One-time script, run once before switching the daemon over to the new schema:
  - Reads `reels.jsonl`, inserts each row into `reels` (`INSERT OR IGNORE`).
  - Reads `opened_reels.json`, sets `is_opened = 1` for every matching `reel_id`.
  - Marks every imported row `alerted = 1` and `is_likely_live = 0` — these are all
    pre-existing reels by definition, so none of them should trigger a
    notification on the new system's first run.
  - Prints a summary count (`N reels imported, M already marked opened`) so you can
    sanity-check it against the old `reels.jsonl` line count before trusting it.

---

## Verification Plan

### Automated / Daemon Tests
1. **Migration**:
   - Run `migrate_v1_to_v2.py` against a copy of the real data directory first.
   - Confirm imported row count matches the old `reels.jsonl`'s distinct reel count,
     and that every id in the old `opened_reels.json` shows `is_opened = 1`.
2. **Pairing & auth**:
   - Delete `token.txt`, reload the extension, confirm the server accepts the first
     request and writes the token file.
   - Confirm a second request with a wrong/missing token gets a 401.
3. **Node server & SQLite ingestion**:
   - `curl` a simulated message payload (with the paired token) to `http://127.0.0.1:8765/log`.
   - Verify rows land in `messages` and `reels`.
   - Re-send the identical payload; verify `INSERT OR IGNORE` prevents duplicates.
4. **Freshness test**:
   - Payload with `capturedAt`/`message_ts` ~1 hour apart → `is_likely_live = 0`,
     daemon marks `alerted = 1`, no notification.
   - Payload with them ~5 seconds apart → notification + pre-download fire.
   - Payload that's fresh but delivered late (simulate by holding it in the
     extension's queue, e.g. stop the server, capture a message, restart the
     server after 5+ minutes) → still classified live, since freshness is judged
     against `capturedAt`, not receipt time.
5. **Database-driven menu & digest**:
   - Run `wa-reel-digest` and `wa-reel-menu`; confirm instant rendering from SQLite,
     with cached summaries showing and unsummarized ones showing a placeholder
     instead of blocking.
6. **Cleanup timer**:
   - Manually run `systemctl --user start wa-reel-cleanup.service` and confirm only
     opened reels older than 7 days lose their `.mp4`.
7. **Service health**:
   - `systemctl --user status wa-notify-server.service`
   - `systemctl --user status wa-reel-alert.service`
   - `systemctl --user status wa-reel-cleanup.timer`
