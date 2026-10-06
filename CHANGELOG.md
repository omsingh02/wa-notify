# Changelog

All notable changes to this hobby project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning: [SemVer](https://semver.org/) (loosely — it is a hobby project).

## [0.2.0] — 2026-10-06

First public release. Items marked *(verify)* were changed while preparing the release; confirm them in the test suite.

### Added
- **v2 architecture**: SQLite (`node:sqlite`, WAL) as the single store (`messages`, `reels`), idempotent inserts, server-side freshness rule, reel extraction at ingestion, TOFU token pairing, CORS allow-list, `/health`.
- **Reel pipeline in Python** (`tools/`): alert daemon, fuzzel menu, fzf digest, cleanup timer, Gemini summaries, one-time v1→v2 migration.
- **Polite fetch queue** (`wa_reel_queue.py`): single worker, 3–7 s pacing, newest first, cool-down ladder for Instagram blocks, separate network pauses, backoff retries, 24 h age cap, backlog seeding on start.
- **Per-reel cross-process lock** so a click during a prefetch waits instead of racing a second download.
- **Photo posts and carousels** open in the floating mpv window (images via yt-dlp metadata); `<`/`>` navigation.
- **Optional Instagram session** (`wa_reel_auth.py`): re-uses browser cookies via yt-dlp, Instagram-only `0600` cache, private per-run copies, `--check` / `--refresh`.
- Clear failure notifications when the browser fallback is used (blocked / network / private / timeout).
- Interactive fetches retry once on network failure; socket timeout raised to 30 s.
- Cleanup removes photo-post images and abandoned partial downloads (> 3 days) and no longer holds the DB write lock while deleting.
- Configuration: `config.toml` (`[instagram]`, `[ui]`, `[ai]`) and `WA_NOTIFY_*` environment variables *(verify)*.
- Documentation, tests, CI and deployment files for the public repository.

### Changed
- Own sent reels are no longer prefetched (still fetched on demand).
- A reel counts as *opened* once its media is resolved, not before the download starts.
- JSONL backup appends only newly inserted messages and can be disabled *(verify)*.

### Fixed *(verify — see [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md))*
- Extension queue lost-update race between flush and enqueue.
- Server: `busy_timeout` set before `journal_mode`; UTF-8-safe body decoding; `5xx` instead of `200` when inserts fail; Host allow-list, fail-closed token read, timing-safe comparison.
- `__waNotifyDebug` `ReferenceError`; `__waNotifyAttach` input shapes; auto-detect no longer attaches to the Chat collection.
- Digest: Rich-markup injection from sender names/summaries; `--float` self-relaunch loop.
- `is_fast_network` bitrate rule; API-key parsing; silent re-tries on empty Gemini responses.

### Known limitations
See [docs/LIMITATIONS.md](docs/LIMITATIONS.md) and [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).

## [0.1.0] — 2026-09-09

Initial private version: Manifest V3 extension (Haste-module hook), Node server appending JSONL, early Python reel scripts. No authentication, no deduplication.
