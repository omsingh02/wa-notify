# Limitations: platform dependence, hard-coded values, assumptions

> **This is a hobby project written for one person's Arch Linux / Hyprland desktop.**
> It is *not* portable. This page lists, as honestly as the author can, everything that is tied to that setup
> or hard-coded. It was checked against the v0.2.0 source; constants are named so you can grep for them.
> "Configurable" means: changeable without editing code (`config.toml`, `WA_NOTIFY_*` environment variables).

## 1. Strict platform dependence

| Dependency | Where | Why | How to port |
|---|---|---|---|
| **Linux only** | `tools/`: `fcntl.flock` (`wa_reel_dl.py`, `wa_reel_auth.py`), `os.killpg` / `signal.SIGKILL` (`wa_reel_dl.py`), `pkill`, `xdg-open`, `$XDG_RUNTIME_DIR` (fallback `reels/.locks`), POSIX file modes `0600`/`0700` (server and tools); systemd units | POSIX/Linux APIs and desktop conventions | macOS: replace `pkill`/`xdg-open`/`wl-copy`/`notify-send`; Windows: not feasible without a rewrite (no `fcntl`, POSIX permissions, symlinked entry points) |
| **Wayland only** | `mpv --wayland-app-id` (`walib.MPV_BASE`), `wl-copy`, `fuzzel`, `foot` | the author runs Wayland | X11: `--x11-name`/`--class`, `xclip`, `rofi`/`dmenu` |
| **Hyprland-specific window rules** | `deploy/hyprland/`, docs; `walib.MPV_BASE` (app id **and** title = `[ui] app_id`, `--autofit` = `[ui] player_size`) | the "floating, pinned, top-right reel window" is a compositor rule, not mpv behaviour; the rules in `deploy/hyprland/` hard-code the same `wa-reel` / `420 750` defaults | any compositor: write an equivalent rule for class/title `wa-reel`; sway: `for_window [app_id="wa-reel"] floating enable, sticky enable` The `windowrule { … }` block syntax needs Hyprland ≥ 0.53 (its release notes: "Windowrule syntax has been completely overhauled"; the author runs 0.56.2). |
| **mako-style notifications with actions** | `wa-reel-alert.py` (`notify-send -A …`), `walib._notify`, menu, digest | action buttons and "default action on click" need a daemon that supports them | dunst/swaync work with different config; no daemon → no click-to-play |
| **fuzzel** picker | `wa-reel-menu.py` `launcher_command()` (`--dmenu --index -p -f -w -l --match-mode=fzf`) | the author's launcher; only its *name*, font, width and line count are configurable, the flags are fuzzel's | any dmenu-compatible tool that accepts these flags and prints an index |
| **foot / footclient** terminal | `wa-reel-menu.py` `open_digest()` (appends `-e <python> <script>`), `wa-reel-digest.py --float` (appends `<python> <script>`) | floating digest window | `[ui] terminal` / `terminal_fallback` (any terminal with a foot-compatible CLI); the fallback is used only when the first command cannot be *started*, not when the foot server is down |
| **a recent fzf** | `wa-reel-digest.py` `run_fzf_ui()` (`--footer`, `--footer-border`, `change-preview-window`, `--expect`, …) | TUI | needs **fzf ≥ 0.66**: the newest option in use, `--gutter`, arrived in 0.66.0 and `--footer`/`--footer-border` in 0.63.0 (read off fzf's CHANGELOG, not tried on an old fzf; the author runs 0.74.4); an fzf that rejects an option makes the digest close without a message because fzf's stderr is discarded; a missing fzf prints a hint |
| **wl-clipboard** | `walib.copy_to_clipboard` | Wayland clipboard | `xclip`/`xsel` |
| **NetworkManager (`nmcli`) and `ping`** | `wa-reel-alert.py`: `is_metered()`, `wifi_rate_mbit()` (`nmcli`), `internet_reachable()` (`ping -c 1 -W 1 1.1.1.1`) | metered / slow-link check before background prefetching | other managers: replace the helpers. A missing `nmcli` counts as "not metered, not on Wi-Fi" and falls back to the ping check; a missing `ping` counts as online |
| **`pkill -f`** | `walib.play_in_mpv` (pattern `title=<app_id>`) | kills the previous player window by command-line match | track the PID instead (see [KNOWN_ISSUES.md](KNOWN_ISSUES.md)) |
| **`xdg-open`** | `walib.play_in_mpv`, `wa-reel-digest.py` (Ctrl-O) | last-resort browser fallback | any URL opener |
| **systemd `--user`** | `deploy/systemd/`, docs | process supervision, timer | cron/launchd/runit; the daemons are plain scripts |
| **ffmpeg** | `wa_reel_ai.py` `prepare_video_bytes()` (`libx264`, `aac`); yt-dlp merges Instagram's separate video/audio streams with it | downscaling for the Gemini upload; media merging | without ffmpeg an oversized video is uploaded as-is (and is likely rejected) and yt-dlp downloads can fail |
| **gnome-keyring + Brave Origin profile path** | `[instagram]` config, `wa_reel_auth.py` | Chromium encrypts cookies with a keyring key; yt-dlp needs `secretstorage` to read it (`v11` cookies) | Firefox needs no keyring; other keyrings via `keyring = …`. A plain `cookies.txt` is **not** supported: the tools only read a browser profile |
| **Chromium-based browser ≥ 111, MV3 APIs** | `extension/manifest.json` (`"world": "MAIN"`, `minimum_chrome_version`), `background.js` (`chrome.alarms` with its 1-minute minimum period, `chrome.storage.local`, `chrome.action` badge) | MAIN-world content scripts | Firefox/Safari: different extension APIs, not attempted |
| **WhatsApp Web internals** | `extension/src/injected.js`: `WELL_KNOWN_MODULES` (`WAWebCollections`, `WAWebMsgCollection`), fallback regex `/(?:WAWeb)?MsgCollections?$/i`, the `Msg` collection and `.on('add')`, `window.__d` / `window.require`, WhatsApp's message attribute names (`t`, `body`, `notifyName`, `senderObj`, …) | no public API; hooks Meta's Haste module registry | **breaks on any WhatsApp Web deploy**; no version pinning is possible; expect periodic fixes |
| **Instagram + yt-dlp behaviour** | `wa_reel_dl.py` (`classify_error` and `_NETWORK_HINTS` match yt-dlp/curl/urllib's English error text; photo posts rely on `yt-dlp -J --ignore-no-formats-error` and its `thumbnails` field) | scraping an undocumented site through a third-party tool | yt-dlp wording changes silently degrade classification; Instagram can block at any time |
| **Gemini API shape and model names** | `wa_reel_ai.py` (REST `generateContent` at the `API_URL` constant, inline base64 video, `X-goog-api-key` header) | the only summariser implemented | `[ai] models`; other providers need new code |
| **`node:sqlite` (experimental)** | `local-server/server.js` | zero npm dependencies | Node ≥ 22.13 prints an `ExperimentalWarning`; the API may change; fallback is `better-sqlite3` |
| **Python ≥ 3.11** | `tomllib` in `tools/wa_settings.py` and `wa_reel_auth.py`; `requires-python` in `pyproject.toml` | config parsing | on older Pythons both modules silently ignore `config.toml` (no `tomli` fallback) |
| **English locale assumptions** | `classify_error`, `walib.format_time` ("m ago"), menu/digest strings | matches English yt-dlp text and UI | n/a |

## 2. Hard-coded values

### Server and extension

| Value | Where | Configurable? |
|---|---|---|
| Port `8765` and bind address `127.0.0.1` | `local-server/server.js` `CONFIG.port` / `CONFIG.host`; `extension/src/background.js` `DEFAULT_ENDPOINT`; `deploy/`, docs | server port: `WA_NOTIFY_PORT`. Extension: reads an override from the `chrome.storage.local` key `wa_notify_endpoint` (no UI; set it from the service worker's devtools console). The manifest's `http://127.0.0.1/*` and `http://localhost/*` host permissions are port-less, so another loopback port needs no manifest change. Bind address: **no**, deliberately |
| Data dir `~/.local/share/wa-notify`, config dir `~/.config/wa-notify` | `server.js` `CONFIG.dataDir` / `CONFIG.configDir`; `tools/wa_settings.py` `DEFAULT_DATA_DIR` / `DEFAULT_CONFIG_DIR` | `WA_NOTIFY_DATA_DIR`, `WA_NOTIFY_CONFIG_DIR` |
| File and directory names `wa-notify.db`, `chat_archive.jsonl`, `token.txt`, `config.toml`, `instagram-cookies.txt`, `reels/`, `summaries/` | `server.js`, `wa_settings.py`, `wa_reel_auth.py` | no |
| Extension id for CORS | `server.js` `CONFIG.extensionId` | `WA_NOTIFY_EXTENSION_ID`; default unset, which means no `chrome-extension://` origin gets CORS headers (the extension's own fetches do not need them) |
| Origin and Host allow-lists: `https://web.whatsapp.com`, `localhost` / `127.0.0.1` / `[::1]` origins, `Host` header regex | `server.js` `WHATSAPP_ORIGIN`, `LOCAL_ORIGIN_HOSTS`, `ALLOWED_HOST_HEADER` | no |
| Freshness window **120 s** (`is_likely_live`) | `local-server/lib/reels.js` `LIVE_WINDOW_SECONDS` (used by `isLikelyLive`, called from `toRow` in `server.js`) | no |
| Request limits: body **10 MB** (`MAX_BODY_BYTES` = 10 000 000 bytes), **500** entries per request (`MAX_ENTRIES_PER_REQUEST`) | `server.js` | no |
| TOFU token minimum length **8** | `server.js` `TOKEN_MIN_LENGTH` (used in `authorize`) | no |
| SQLite pragmas: `journal_mode = WAL`, `synchronous = NORMAL`; `busy_timeout` **5000 ms**; startup retries `DB_OPEN_ATTEMPTS` = 20 × `DB_OPEN_RETRY_MS` = 250 ms | `server.js`; `tools/walib.py` `get_db` (also `sqlite3.connect(timeout=5.0)`) | server `busy_timeout`: `WA_NOTIFY_BUSY_TIMEOUT_MS`; everything else: no, including the Python side |
| Reel regex `instagram.com/(reel\|reels\|p)/<id>` | `local-server/lib/reels.js` `REEL_URL_SOURCE` (the Python side takes the reel id from the stored URL, it has no copy of the regex) | no — misses `/share/…` and `/<user>/reel/…` forms |
| Sender-label heuristics (`You`, `Group`, `Group Member`, `Contact`, `Channel: …`, `+<number>`; title regex; titles under 30 characters) | `local-server/lib/sender.js` (`E164_JID`, `CHANNEL_NAME_IN_TITLE`, `MAX_CHANNEL_TITLE_LENGTH`) | no |
| Extension queue: cap **10 000** (`MAX_QUEUE`), batch **25** (`BATCH_SIZE`), fetch timeout **30 s** (`FETCH_TIMEOUT_MS`), retry alarm **1 min** (`FLUSH_PERIOD_MINUTES`, Chrome's minimum), repeated-warning interval **5 min** (`WARN_INTERVAL_MS`), storage keys `QUEUE_KEY` / `TOKEN_KEY` / `ENDPOINT_KEY` | `background.js` | no (endpoint: see above) |
| Store detection: module names `WAWebCollections`, `WAWebMsgCollection` (`WELL_KNOWN_MODULES`); fallback after 10 s for ids matching `/(?:WAWeb)?MsgCollections?$/i`; `MSG_NAME` and `CANDIDATE_PATTERN` regexes; 2 s start delay; 1 s poll (`POLL_INTERVAL_MS`); **120 s** give-up (`MAX_TOTAL_MS`) | `injected.js` | no |
| Message field mapping (`extractMessage`) and the attributes dropped from `raw` (`collection`, `chat`, `msgButtons`, keys starting with `_`) | `injected.js` | no |
| Custom event name `__wa_notify_event__`, window globals `__waNotify*`, `__WA_NOTIFY_REGISTRY__`, `__WA_NOTIFY_STORE__` | `injected.js`, `content.js` | no |

### Python tools

| Value | Where | Configurable? |
|---|---|---|
| Player class/title `wa-reel`, size `420x750` | `walib.MPV_BASE` | `[ui] app_id`, `player_size` (the compositor rules in `deploy/hyprland/` must be changed to match) |
| mpv flags `--no-border`, `--force-window=immediate`, `--loop-file=inf`, `--image-display-duration=inf`, `--loop-playlist=inf` | `walib.MPV_BASE`, `play_in_mpv` | no |
| Player behaviour: "Fetching reel…" notification after **2.0 s**, interactive fetch timeout **60 s**, notification timeouts 6 s / 8 s, icon `instagram` | `walib.play_in_mpv`, `walib._notify` | no |
| Launcher `fuzzel`, font `JetBrains Mono NF:size=12`, width 125, max 14 lines; history sizes 25 / 20 (`HISTORY_LIMIT`, `FALLBACK_HISTORY_LIMIT`), minimum sender column 22 | `wa-reel-menu.py` | `[ui] launcher`, `launcher_font`, `launcher_width`, `launcher_max_lines`; the rest: no |
| Digest: default limit **50** (`DEFAULT_LIMIT`), preview placement switches at **135** columns, fixed key bindings and colours | `wa-reel-digest.py` | `--limit` only |
| Terminals `footclient -a wa-reel-digest`, fallback `foot -a wa-reel-digest` | `wa_settings.py` defaults, used by `wa-reel-menu.py` and `wa-reel-digest.py` | `[ui] terminal`, `terminal_fallback` |
| Notification app name `wa-reels` | `walib._notify`, `wa-reel-alert.py`, menu, digest | `[ui] notify_app_name` |
| Alert daemon: poll **1.5 s** (error back-off **2 s**), `LIMIT 20` rows per poll, backlog seeding **10 reels / 24 h** (scans the newest 60), summary concurrency **2** | `wa-reel-alert.py` | no |
| Network heuristic: Wi-Fi ≥ **25 Mbit/s** (`MIN_WIFI_MBIT`; slower means no prefetch), helper timeout 1.5 s (`HELPER_TIMEOUT`), ping target `1.1.1.1` | `wa-reel-alert.py` `is_fast_network` | no |
| Queue ladders: `PACE` 3–7 s, `COOLDOWNS` 15/30/60/120 min, `NET_PAUSES` 1/2/5/10 min, `BACKOFF` 5 min/20 min/1 h/3 h, `MAX_AGE` 24 h; one retry after 60 min for `unavailable` | `wa_reel_queue.py` | no |
| Fetch timeouts: yt-dlp `--socket-timeout` **30 s** (`fetch_media(socket_timeout=…)`); per-run timeout **60 s** (`fetch_media` default and the interactive path), **120 s** (queue and the `wa_reel_dl.py` CLI), **45 s** (`download_reel`, used by digest Ctrl-D); image downloads at least 30 s; lock waits **100 s** (interactive) / **300 s** (queue); one automatic retry after a `network` failure, interactive only | `wa_reel_dl.py`, `wa-reel-alert.py`, `walib.py` | no |
| Media sanity and templates: minimum size video **10 kB** / image **1 kB**; video extensions `.mp4 .mkv .webm`; `IMAGE_EXTS`; Chrome-140 browser User-Agent for image downloads (`_BROWSER_UA`); URL template `https://www.instagram.com/reel/<id>` | `walib.py`, `wa_reel_dl.py` | no |
| Cookie cache: stale copy allowed **7 days** (`STALE_OK_SECONDS`), failure back-off **10 min** (`RETRY_AFTER_FAILURE`), forced re-read only if the cache is at least **120 s** old (`refresh_cookies(min_age)`) | `wa_reel_auth.py` | `refresh_hours` only |
| Gemini: model list, **18 MB** inline limit, **90 s** clip, 720 px / CRF 28 / 96 kb/s downscale, ffmpeg timeout **25 s**, request timeout **45 s**, retry only 429/503 once (`Retry-After`, capped at 10 s, default 2.5 s), error-body excerpt 300 bytes, temperature 0.2, system prompt, API URL | `wa_reel_ai.py` | `[ai] models`, `max_inline_mb`, `clip_seconds`; the rest: no |
| Retention **7 days** (opened reels), stale partials **3 days**; timer `OnCalendar=daily` | `wa-reel-cleanup.py` (`DEFAULT_RETENTION_DAYS`, `STALE_PARTIAL_DAYS`); `deploy/systemd/` | `--days`; partials and schedule: no |
| SQLite DDL (duplicated in `server.js` and `walib.py`) | both | no migrations |
| Secrets file `~/.config/.secrets`, key names `GEMINI_API_KEY_REELS` / `GEMINI_API_KEY` | `wa_settings.py`, `wa_reel_ai.py` (`KEY_NAMES`) | `[ai] secrets_file`; key names: no |

## 3. Single-user assumptions

* One WhatsApp account, one browser profile, one machine, one Instagram session.
* The extension generates its token on first run; a second browser profile cannot pair without manual re-pairing.
* Sender labels, "You", own-reel handling and the 120 s freshness window are tuned to one person's chats.
* No authentication beyond a local shared secret; no multi-user permissions. The server restricts its database and backup to `0600`, but downloaded media, summaries and the rest of the data directory use default modes.
* Only one person's ordinary day-to-day traffic has been tried; nothing was load-tested.

## 4. Legal and terms of service

* **Not affiliated with WhatsApp, Meta, Instagram or Google.** Names are trademarks of their owners.
* Hooking WhatsApp Web's internal modules and exporting messages **is against WhatsApp's Terms of Service**. Accounts can be restricted.
* Downloading Instagram media with yt-dlp, and using your logged-in session to do so, may violate Instagram's terms; automated logged-in requests can get an account challenged or rate-limited. The tool deliberately keeps volume low (one request at a time, cool-downs) but that is not a guarantee.
* The archive contains **other people's messages and contact identifiers**. You are responsible for lawful use, consent and retention where you live.
* Summaries send the video content (not chat text) to Google's Gemini API.
* No warranty. Use at your own risk.
