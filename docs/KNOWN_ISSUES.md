# Known issues

> Hobby project: this is the author's honest bug list. Evidence comes from an audit of the author's own deployment
> plus sandbox reproductions. Figures are rough observations from that one setup, not benchmarks.
> The original audit text is in [history/](history/).

Status legend: **Fixed in 0.2.0 (verify)** — fixed while preparing the public release, confirm in the tests; **Open** — known and not fixed.

## Fixed in 0.2.0 (verify)

| # | Area | Issue | Evidence |
|---|---|---|---|
| F1 | extension | **Queue lost-update race**: `flushQueue` wrote back a stale copy of the queue after its POST, erasing messages enqueued during the round trip (the earlier mutex only covered enqueue vs enqueue). | Sandbox: 10 messages 5 ms apart with a 30 ms server → 8 lost; 200-message flood → 75 lost and 83 duplicate posts. Fix: remove sent entries from the *current* queue inside the same mutex (0 lost). Also quadratic storage writes (63 MB for 0.6 MB of messages). |
| F2 | server | `PRAGMA journal_mode = WAL` ran **before** `busy_timeout`, so a lock at startup killed the process. | Seen once in production (`database is locked` at boot); reproduced 7/70 starts under contention, 0/70 with the lines swapped. |
| F3 | server | **UTF-8 corruption**: `body += chunk` decoded each TCP chunk separately; a multi-byte character split across chunks became U+FFFD. | Reproduced (3×U+FFFD per split emoji); runs of 3–4 U+FFFD found in the author's DB. |
| F4 | server | Insert failures were logged but the response was **HTTP 200**, so the extension deleted its only copy; the event loop also stalled (`/health` hung 8.5 s) during a lock. | Reproduced with a write-locked DB. Now `5xx` on failure. |
| F5 | server | JSONL backup appended every delivery: **more than half of the lines were duplicates** (the file grew several times faster than the database), some messages dozens of times. | Counted from the author's file. Now only newly inserted rows. |
| F6 | extension | `window.__waNotifyDebug()` threw `ReferenceError: NAME_PATTERN is not defined`. | Reproduced. |
| F7 | extension | `__waNotifyAttach` rejected a bare Msg collection and the output of `__waNotifyRequire` although the README said otherwise. | Reproduced. |
| F8 | extension | Auto-detect could attach to the **Chat** collection (any export that "looks like a collection"). | Reproduced in a sandbox. |
| F9 | digest | Untrusted strings (sender names, summaries) were interpolated into Rich markup: `[/x]` crashed the preview; `[chorus]` was swallowed. | Reproduced with the real script. |
| F10 | digest | `wa-reel-digest -fa` / `--fl` re-launched itself forever in new terminals (only exact `-f`/`--float` were stripped). | Reproduced with a stub terminal. |
| F11 | alert | `is_fast_network`: the "≥ 25 Mbit/s" rule could only return True (slow Wi-Fi fell through to a ping that succeeds). | Reproduced. |
| F12 | ai | API-key parsing took the first line containing the name even if commented, and kept inline comments. | Reproduced. |
| F13 | ai | An HTTP 200 without usable text (e.g. safety block) was retried silently — 4 identical uploads per reel. | Reproduced. |

## Open

| # | Severity | Issue | Evidence / detail | Workaround |
|---|---|---|---|---|
| O1 | medium | **Re-shared reels are dropped**: `INSERT OR IGNORE` keyed by shortcode keeps the first sighting forever; a later *live* re-share of a reel first seen as history never alerts. | a small share of shared reels were re-shares, and a few of those were live alerts that were lost. | Open it from the menu. |
| O2 | medium | **Own sent reels still notify** (they are no longer prefetched). | roughly one in six live alerts were reels the author had sent to other chats (none to the self-chat). | Ignore, or filter `fromMe` in `wa-reel-alert.py`. |
| O3 | low | Image posts (`/p/`) raise a "LIVE REEL" notification. | roughly one in seven live alerts. | — |
| O4 | low | **Mixed carousels** (video + photos) show only the video. | yt-dlp returns only video entries. | Open in the browser. |
| O5 | medium (privacy) | `raw_json` stores every primitive WhatsApp attribute, including **`mediaKey` and `directPath`** (the inputs WhatsApp client libraries use to fetch and decrypt media) and base64 thumbnails as `body`; the JSONL backup repeats it. | `mediaKey` in 1 349 messages, `directPath` in 1 110. | Treat the archive as a credential store; encrypt at rest; disable the JSONL backup. |
| O6 | low | `/health` is unauthenticated and reveals the DB path (Host allow-list added in 0.2.0 — verify). | | Bound to loopback. |
| O7 | low | **Unpaired server accepts unauthenticated writes** until the first token arrives; a stale token after reinstalling the extension looks like "server down" (the queue grows to 10 000 then drops the oldest silently). | | Pair immediately after first start; `rm token.txt` + reload the extension to re-pair. |
| O8 | low | `content.js` relays any `__wa_notify_event__` from the page without validation and loads at `document_idle` (events before that are lost). | by inspection | — |
| O9 | low | Cleanup only handles **opened** reels; unopened reels' media never expires. | by inspection | `wa-reel-cleanup` + manual deletes. |
| O10 | low | **DDL is duplicated** in `server.js` and `walib.py`; there is no schema versioning or migration. | by inspection | Change both together. |
| O11 | low | **Mixed units**: `message_ts`/`timestamp`/`opened_at` are seconds; `captured_at`/`first_seen_at` are milliseconds. | schema | Convert explicitly. |
| O12 | low | **`pkill -f title=wa-reel`** matches *any* process whose command line contains that text, including the shell that launched it (it killed an automation wrapper during testing). | reproduced once | Avoid that string in other commands' arguments; planned: track the PID. |
| O13 | low | **Hyprland snippet pitfall**: an unquoted heredoc expands `$mainMod` to nothing → `bind = SHIFT, R` captures every capital R. | seen in a generated snippet | Quote the heredoc delimiter. |
| O14 | low | Sender labels are heuristics: a hard-coded `Republic` channel special case; `from` preferred over `author` in groups; labels guessed from link-preview titles. | `cleanSender` | — |
| O15 | low | Summaries: `summary_status` never becomes `failed`; no retry sweep for pending summaries; on-demand downloads are not summarised unless queued; Gemini 429s are retried within seconds. | about a third of live reels had no summary at audit time (image posts, rate-limited downloads, and downloads whose summary call failed) | `wa_reel_ai.py <id> --force`. |
| O16 | low | Predictable ffmpeg temp file `/tmp/wa_reel_opt_<id>.mp4`, not removed on failure. | by inspection | — |
| O17 | low | Alert loop has no per-row exception isolation (e.g. missing `notify-send` with `--autoplay` repeats one row). | by inspection | — |
| O18 | low | The `footclient`→`foot` fallback only triggers when the binary is missing, not when the foot server is down. | by inspection | Run `foot --server`. |
| O19 | low | "Mark all as opened" marks every unopened reel, including ones that arrived while the menu was open. | by inspection | — |
| O20 | low | `/share/…` and `/<user>/reel/…` Instagram URL forms are not recognised. | 0 occurrences in 1 145 links, so far | — |
| O21 | info | **Local DNS stalls** (10–22 s lookups, "network unreachable" blips with `systemd-resolved` flapping between DoT and UDP) surface as network pauses/retries. | author's machine | Fix the resolver. |
| O22 | info | WhatsApp Web changes break capture without notice; Instagram/yt-dlp changes break fetching. | by nature | See [LIMITATIONS.md](LIMITATIONS.md). |
