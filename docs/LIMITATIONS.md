# Limitations: platform dependence, hard-coded values, assumptions

> **This is a hobby project written for one person's Arch Linux / Hyprland desktop.**
> It is *not* portable. This page lists, as honestly as the author can, everything that is tied to that setup
> or hard-coded. File/constant names are given so you can grep; "see source" means the value was being refactored
> when this page was written.

## 1. Strict platform dependence

| Dependency | Where | Why | How to port |
|---|---|---|---|
| **Linux only** | everything in `tools/` (`fcntl.flock`, `os.killpg`, `pkill`, `xdg-open`, `/tmp`, `$XDG_RUNTIME_DIR`), systemd units | POSIX/Linux APIs and desktop conventions | macOS: replace `pkill`/`xdg-open`/`wl-copy`/`notify-send`; Windows: not feasible without a rewrite (no `fcntl`) |
| **Wayland only** | `mpv --wayland-app-id`, `wl-copy`, `fuzzel`, `foot` | the author runs Wayland | X11: `--x11-name`/`--class`, `xclip`, `rofi`/`dmenu` |
| **Hyprland-specific window rules** | `deploy/hyprland/`, docs; `walib.MPV_BASE` (`app_id`/title `wa-reel`, `--autofit=420x750`) | the "floating, pinned, top-right reel window" is a compositor rule, not mpv behaviour | any compositor: write an equivalent rule for class/title `wa-reel`; sway: `for_window [app_id="wa-reel"] floating enable, sticky enable` |
| **mako-style notifications with actions** | `wa-reel-alert.py` (`notify-send -A …`), `walib._notify` | action buttons and "default action on click" need a daemon that supports them | dunst/swaync work with different config; no daemon → no click-to-play |
| **fuzzel** picker | `wa-reel-menu.py` (`--dmenu --index`, font, width) | the author's launcher | any dmenu-compatible tool that prints an index |
| **foot / footclient** terminal | `wa-reel-menu.py`, `wa-reel-digest.py --float` | floating digest window | `[ui] terminal` / `terminal_fallback` |
| **fzf ≥ 0.58** | `wa-reel-digest.py` (`--footer`, `change-preview-window`) | TUI | older fzf silently exits (stderr is discarded) |
| **wl-clipboard** | `walib.copy_to_clipboard` | Wayland clipboard | `xclip`/`xsel` |
| **NetworkManager (`nmcli`)** | `wa-reel-alert.py:is_fast_network` | metered-connection check | other managers: replace the function; it fails open if `nmcli` is missing |
| **`pkill -f`** | `walib.play_in_mpv` | kills the previous player window by command-line match | track the PID instead (see [KNOWN_ISSUES.md](KNOWN_ISSUES.md)) |
| **`xdg-open`** | `walib.play_in_mpv`, digest | last-resort browser fallback | any URL opener |
| **systemd `--user`** | `deploy/systemd/`, docs | process supervision, timer | cron/launchd/runit; the daemons are plain scripts |
| **gnome-keyring + Brave Origin profile path** | `[instagram]` config, `wa_reel_auth.py` | Chromium encrypts cookies with a keyring key; yt-dlp needs `secretstorage` to read it (`v11` cookies) | Firefox needs no keyring; other keyrings via `keyring = …`; or export a cookies file and point yt-dlp at it |
| **Chromium-based browser ≥ 111** | `extension/manifest.json` (`"world": "MAIN"`) | MAIN-world content scripts | Firefox/Safari: different extension APIs, not attempted |
| **WhatsApp Web internals** | `extension/src/injected.js` (`WELL_KNOWN_MODULES`, `Msg` collection, `.on('add')`, `window.__d` / `window.require`) | no public API; hooks Meta's Haste module registry | **breaks on any WhatsApp Web deploy**; no version pinning is possible; expect periodic fixes |
| **Instagram + yt-dlp behaviour** | `wa_reel_dl.py` (`classify_error` matches yt-dlp's English error text; `thumbnails` field for images) | scraping an undocumented site through a third-party tool | yt-dlp wording changes silently degrade classification; Instagram can block at any time |
| **Gemini API shape and model names** | `wa_reel_ai.py` (REST `generateContent`, inline base64 video) | the only summariser implemented | `[ai] models`; other providers need new code |
| **`node:sqlite` (experimental)** | `local-server/server.js` | zero npm dependencies | Node ≥ 22.13 prints an `ExperimentalWarning`; API may change; fallback is `better-sqlite3` |
| **Python ≥ 3.11** | `tomllib` in `wa_reel_auth.py` | config parsing | add `tomli` for older Pythons |
| **English locale assumptions** | `classify_error`, `walib.format_time` | matches English yt-dlp text | n/a |

## 2. Hard-coded values

"Configurable" refers to **0.2.0**; verify against the source if in doubt.

### Server and extension

| Value | Where | Configurable? |
|---|---|---|
| Port `8765` | `local-server/server.js` `PORT`; `extension/src/background.js` `ENDPOINT`; `manifest.json` `host_permissions` | server: `WA_NOTIFY_PORT`; **extension: no** (edit source) |
| Data dir `~/.local/share/wa-notify`, config dir `~/.config/wa-notify` | `server.js`, `tools/walib.py` | `WA_NOTIFY_DATA_DIR`, `WA_NOTIFY_CONFIG_DIR` |
| Extension id (CORS) | `server.js` `EXTENSION_ID` | `WA_NOTIFY_EXTENSION_ID` (the old default was derived from the author's folder path) |
| Local-origin allow-list `localhost`, `127.0.0.1`, `[::1]` | `server.js` `LOCAL_HOSTS` | no |
| Freshness window **120 s** (`is_likely_live`) | `server.js` `processMessageEntry` | no |
| Max request body **10 MB** | `server.js` `MAX_BODY_BYTES` | no |
| TOFU token minimum length **8** | `server.js` `verifyOrPairToken` | no |
| SQLite pragmas (`WAL`, `busy_timeout 5000`, `synchronous NORMAL`) | `server.js`, `walib.get_db` | no |
| Reel regex `instagram.com/(reel\|reels\|p)/<id>` | `server.js` `REEL_REGEX`, `walib.REEL_PATTERN` | no — misses `/share/…` and `/<user>/reel/…` forms |
| Sender-label heuristics (`Group`, `Contact`, `Channel: …`, title regexes) | `server.js` `cleanSender` | no |
| Queue cap **10 000**, batch size **25**, alarm period **1 min**, storage keys | `background.js` (`MAX_QUEUE`, `BATCH_SIZE`, `QUEUE_KEY`, `TOKEN_KEY`) | no |
| Store-detection polling (1 s interval, 2 s start delay, 10 s before the fallback regex, **120 s** give-up) and module names | `injected.js` (`POLL_INTERVAL_MS`, `MAX_TOTAL_MS`, `WELL_KNOWN_MODULES`) | no |
| Custom event name `__wa_notify_event__`, window globals `__waNotify*` | `injected.js`, `content.js` | no |

### Python tools

| Value | Where | Configurable? |
|---|---|---|
| Player class/title `wa-reel`, size `420x750`, mpv flags | `walib.MPV_BASE` | `[ui] app_id`, `player_size`; flags: no |
| Launcher `fuzzel`, font `JetBrains Mono NF:size=12`, width 125, max 14 lines; history sizes 25/20 | `wa-reel-menu.py` | `[ui] launcher*` (history sizes: no) |
| Terminals `footclient -a wa-reel-digest`, fallback `foot` | `wa-reel-menu.py`, `wa-reel-digest.py` | `[ui] terminal`, `terminal_fallback` |
| Notification app name `wa-reels`, icon `instagram` | `walib._notify`, `wa-reel-alert.py`, menu, digest | `[ui] notify_app_name` (icon: no) |
| Alert poll **1.5 s**, batch `LIMIT 20`; backlog seeding **10 reels / 24 h**; summary concurrency **2** | `wa-reel-alert.py` | no |
| Network heuristic: Wi-Fi ≥ **25 Mbit/s**, ping `1.1.1.1` | `wa-reel-alert.py` `is_fast_network` | no (rule reviewed in [KNOWN_ISSUES.md](KNOWN_ISSUES.md)) |
| Queue ladders: `PACE` 3–7 s, `COOLDOWNS` 15/30/60/120 min, `NET_PAUSES` 1/2/5/10 min, `BACKOFF` 5 min/20 min/1 h/3 h, `MAX_AGE` 24 h | `wa_reel_queue.py` | no |
| yt-dlp `--socket-timeout 30`; timeouts 60 s (interactive) / 120 s (queue); lock waits 100 s / 300 s; one network retry | `wa_reel_dl.py`, `wa-reel-alert.py` | no |
| Minimum media size (video 10 kB, image 1 kB); image extensions; browser user-agent string | `walib.py`, `wa_reel_dl.py` | no |
| Cookie cache: stale copy allowed **7 days**, failure back-off **10 min** | `wa_reel_auth.py` | `refresh_hours` only |
| Gemini model list, **18 MB** inline limit, `-t 90` clip, 720 px / CRF 28 downscale, 25 s ffmpeg timeout, temperature 0.2, system prompt, API URL | `wa_reel_ai.py` | `[ai] models`, `max_inline_mb`, `clip_seconds`; the rest: no |
| ffmpeg temp file `/tmp/wa_reel_opt_<id>.mp4` (predictable path) | `wa_reel_ai.py` | no |
| Retention **7 days** (opened reels), stale partials **3 days** | `wa-reel-cleanup.py` | `--days`; partials: no |
| SQLite DDL (duplicated in `server.js` and `walib.py`) | both | no migrations |
| Secrets file `~/.config/.secrets`, key names `GEMINI_API_KEY_REELS`/`GEMINI_API_KEY` | `wa_reel_ai.py` | `[ai] secrets_file` |

## 3. Single-user assumptions

* One WhatsApp account, one browser profile, one machine, one Instagram session.
* The extension generates its token on first run; a second browser profile cannot pair without manual re-pairing.
* Sender labels, "You", own-reel handling and the 120 s freshness window are tuned to one person's chats.
* No authentication beyond a local shared secret; no multi-user permissions; the database and cookie cache are plain files in your home directory.
* Only one person's ordinary day-to-day traffic has been tried; nothing was load-tested.

## 4. Legal and terms of service

* **Not affiliated with WhatsApp, Meta, Instagram or Google.** Names are trademarks of their owners.
* Hooking WhatsApp Web's internal modules and exporting messages **is against WhatsApp's Terms of Service**. Accounts can be restricted.
* Downloading Instagram media with yt-dlp, and using your logged-in session to do so, may violate Instagram's terms; automated logged-in requests can get an account challenged or rate-limited. The tool deliberately keeps volume low (one request at a time, cool-downs) but that is not a guarantee.
* The archive contains **other people's messages and contact identifiers**. You are responsible for lawful use, consent and retention where you live.
* Summaries send the video content (not chat text) to Google's Gemini API.
* No warranty. Use at your own risk.
