# Changelog

All notable changes to this hobby project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning: [SemVer](https://semver.org/) (loosely — it is a hobby project).

## [Unreleased]

### Added
- Extension: on install/update a fresh relay is attached to the WhatsApp Web tabs that are already open, so reloading the extension no longer needs a tab refresh to keep capturing (new `scripting` permission).
- `wa-reel-dl` and `wa-reel-summary` accept `--help`; flags may come before the reel id.
- Landing page: Vercel Git deploys from `main` (Root Directory `site`, builds skipped when `site/` did not change).

### Fixed
- Extension: an orphaned content script ("Extension context invalidated") stops with one warning instead of throwing on every message.
- `[instagram] profile` accepts `~` (yt-dlp does not expand it).
- Docs: removed figures taken from the author's private archive.

### Changed
- CI enforces `ruff format --check .` in addition to `ruff check .`; the Node tests run as `node --test "extension/test/*.test.js" "local-server/test/*.test.js"` (Node 22 rejects directory arguments).
- Documentation re-verified against the code: limitations tables, configuration, architecture and known issues now name the real files, constants and tests.

## [0.2.0] — 2026-10-06

First public release. Every fix listed below is covered by an automated test; the test names are in [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).

### Added
- **v2 architecture**: SQLite (`node:sqlite`, WAL) as the single store (`messages`, `reels`), idempotent inserts, server-side freshness rule, reel extraction at ingestion, TOFU token pairing, CORS allow-list, `/health`.
- **Reel pipeline in Python** (`tools/`): alert daemon, fuzzel menu, fzf digest, cleanup timer, Gemini summaries, one-time v1→v2 migration.
- **Polite fetch queue** (`wa_reel_queue.py`): single worker, 3–7 s pacing, newest first, cool-down ladder for Instagram blocks, separate network pauses, backoff retries, 24 h age cap, backlog seeding on start.
- **Per-reel cross-process lock** so a click during a prefetch waits instead of racing a second download.
- **Photo posts and carousels** open in the floating mpv window (images via yt-dlp metadata); `<`/`>` navigation.
- **Optional Instagram session** (`wa_reel_auth.py`): re-uses browser cookies via yt-dlp, Instagram-only `0600` cache, private per-run copies, `--check` / `--refresh`.
- Clear failure notifications when the browser fallback is used (blocked / network / private / timeout).
- Interactive fetches retry once on network failure; yt-dlp socket timeout raised to 30 s.
- Cleanup removes photo-post images and abandoned partial downloads (> 3 days) and no longer holds the DB write lock while deleting.
- Configuration: `tools/wa_settings.py` reads `config.toml` (`[ui]`, `[ai]`; `[instagram]` is read by `wa_reel_auth.py`) and `WA_NOTIFY_DATA_DIR` / `WA_NOTIFY_CONFIG_DIR`; the server reads `WA_NOTIFY_PORT`, `WA_NOTIFY_DATA_DIR`, `WA_NOTIFY_CONFIG_DIR`, `WA_NOTIFY_EXTENSION_ID`, `WA_NOTIFY_JSONL_BACKUP`, `WA_NOTIFY_BUSY_TIMEOUT_MS`, `WA_NOTIFY_DEBUG`. The extension endpoint can be overridden through the `chrome.storage.local` key `wa_notify_endpoint`.
- Extension: a `!` badge and a rate-limited console warning while delivery fails; `minimum_chrome_version` 111.
- Documentation, tests (Python and JS), CI and deployment files for the public repository.

### Changed
- Own sent reels are no longer prefetched (still fetched on demand).
- A reel counts as *opened* once its media is resolved, not before the download starts.
- JSONL backup appends only newly inserted messages and can be disabled.
- The server answers `5xx` when a database write fails, checks the `Host` header (`421` otherwise), fails closed when the token file cannot be read, sends no CORS header for unknown origins and keeps its database and backup files `0600`.
- Per-request log lines are off unless `WA_NOTIFY_DEBUG=1`.
- "Mark all as opened" in the menu marks only the reels it listed.
- The Gemini downscale uses a private temporary directory.

### Fixed
- Extension queue lost-update race between flush and enqueue.
- Server: `busy_timeout` set before `journal_mode` (startup crash); UTF-8 corruption of multi-byte characters split across TCP chunks; `200` returned when inserts failed.
- `__waNotifyDebug` `ReferenceError`; `__waNotifyAttach` input shapes; auto-detect no longer attaches to the Chat collection.
- Digest: Rich-markup injection from sender names/summaries; `--float` self-relaunch loop.
- `is_fast_network` bitrate rule; API-key parsing; silent re-tries on empty Gemini responses.

### Known limitations
See [docs/LIMITATIONS.md](docs/LIMITATIONS.md) and [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).

## [0.1.0] — 2026-09-09

Initial private version: Manifest V3 extension (Haste-module hook), Node server appending JSONL, early Python reel scripts. No authentication, no deduplication.
