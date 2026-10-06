# Configuration

> Hobby project: configuration grew organically. Anything not listed here is hard-coded — see [LIMITATIONS.md](LIMITATIONS.md).

## Environment variables

| Variable | Default | Used by | Meaning |
|---|---|---|---|
| `WA_NOTIFY_PORT` | `8765` | server | Loopback port. **The extension has it hard-coded** (`ENDPOINT` in `extension/src/background.js` and `host_permissions` in `manifest.json`): change those too. |
| `WA_NOTIFY_DATA_DIR` | `~/.local/share/wa-notify` | server, tools | `wa-notify.db`, `chat_archive.jsonl`, `reels/`, `summaries/`. |
| `WA_NOTIFY_CONFIG_DIR` | `~/.config/wa-notify` | server, tools | `token.txt`, `config.toml`, `instagram-cookies.txt`. |
| `WA_NOTIFY_EXTENSION_ID` | unset | server | Chrome extension id allowed in CORS (`chrome-extension://<id>`). Unpacked ids depend on the folder path. Optional — the extension works without it. |
| `WA_NOTIFY_JSONL_BACKUP` | `1` | server | `0` disables the flat-file backup. The backup only appends newly inserted messages. |
| `WA_NOTIFY_BUSY_TIMEOUT_MS` | `5000` | server | How long SQLite waits on a lock held by another process (the Python tools write to the same database). |
| `WA_NOTIFY_DEBUG` | unset | server | `1` logs every request (off by default: one line per batch is noisy in the journal). |
| `GEMINI_API_KEY_REELS` / `GEMINI_API_KEY` | unset | `wa_reel_ai.py` | API key for summaries (first one set wins; `*_REELS` preferred). |
| `XDG_RUNTIME_DIR` | set by the session | `wa_reel_dl.py` | Where per-reel lock files live (`$XDG_RUNTIME_DIR/wa-notify/locks`), falling back to `reels/.locks`. |
| `PYTHONUNBUFFERED=1` | — | systemd unit | Makes the daemon's log lines appear in the journal immediately. |

## `config.toml`

Location: `$WA_NOTIFY_CONFIG_DIR/config.toml` (default `~/.config/wa-notify/config.toml`). Read with `tomllib` (Python ≥ 3.11). Every key is optional.

```toml
[instagram]                     # optional logged-in session, see "Instagram session" in the README
browser = "brave"               # any browser yt-dlp knows: brave, chrome, chromium, firefox, ...
profile = "~/.config/BraveSoftware/Brave-Origin/Default"   # profile directory (or name); omit for the browser default
keyring = "GNOMEKEYRING"        # Chromium on Linux: GNOMEKEYRING | KWALLET | KWALLET5 | KWALLET6 | BASICTEXT
refresh_hours = 6               # how often the Instagram-only cookie copy is re-read

[ui]
app_id = "wa-reel"              # mpv Wayland app_id AND window title; your compositor rules match it
player_size = "420x750"         # mpv --autofit
launcher = "fuzzel"             # dmenu-style picker used by wa-reel-menu
launcher_font = "JetBrains Mono NF:size=12"
launcher_width = 125            # characters
launcher_max_lines = 14
terminal = ["footclient", "-a", "wa-reel-digest"]            # argv prefix that opens the digest in a floating terminal
terminal_fallback = ["foot", "-a", "wa-reel-digest"]
notify_app_name = "wa-reels"    # notify-send --app-name (match it in your mako config)

[ai]
models = ["gemini-3.5-flash", "gemini-3.5-flash-lite"]   # tried in order
secrets_file = "~/.config/.secrets"                       # file with GEMINI_API_KEY_REELS=...
max_inline_mb = 18              # larger videos are downscaled with ffmpeg before upload
clip_seconds = 90               # downscaled clips are cut to this length
```

Notes:

* The `[instagram]` section is the only one that *changes behaviour*; the others only replace values that used to be hard-coded.
* `profile` and `secrets_file` accept `~`.
* Model names are examples that were valid for the author; check Google's current list.
* Delete `config.toml` (and `instagram-cookies.txt`) to return to anonymous access.

## Files and paths

| Path | Created by | Mode | Contents |
|---|---|---|---|
| `$DATA/wa-notify.db` (+ `-wal`, `-shm`) | server | default | SQLite database (all captured messages — **sensitive**) |
| `$DATA/chat_archive.jsonl` | server | default | optional flat backup (**sensitive**) |
| `$DATA/reels/<id>.mp4` / `<id>_<n>.jpg\|webp\|png` | `wa_reel_dl.py` | default | cached media |
| `$DATA/summaries/<id>.txt` | `wa_reel_ai.py` | default | cached summaries |
| `$CONFIG/token.txt` | server (TOFU) | `0600` | shared secret between extension and server |
| `$CONFIG/config.toml` | you | your choice | see above |
| `$CONFIG/instagram-cookies.txt` | `wa_reel_auth.py` | `0600` | Instagram-only cookies (**credential**) |
| `$CONFIG/.instagram-cookies.lock` | `wa_reel_auth.py` | `0600` | refresh lock |
| `~/.config/.secrets` | you | `0600` recommended | `GEMINI_API_KEY_REELS=...` |
| `$XDG_RUNTIME_DIR/wa-notify/locks/` | `wa_reel_dl.py` | default | per-reel lock files (tmpfs) |
| `~/.config/systemd/user/wa-*.service` / `.timer` | you (copy from `deploy/systemd/`) | | units |

`$DATA` = `WA_NOTIFY_DATA_DIR`, `$CONFIG` = `WA_NOTIFY_CONFIG_DIR`.

## Command-line options

| Command | Options |
|---|---|
| `wa-reel-alert` | `--autoplay` (open the player immediately), `--download` (prefetch regardless of network heuristics) |
| `wa-reel-menu` | `-a/--all`, `-o/--opened/--history` |
| `wa-reel-digest` | `-a/--all`, `-o/--opened`, `-f/--float`, `-t/--table`, `-n/--notify`, `-l/--limit N` |
| `wa-reel-cleanup` | `--days N` (default 7), `--dry-run` |
| `wa_reel_dl.py <id\|url>` | `--summary` |
| `wa_reel_ai.py <id\|url>` | `--force` |
| `wa_reel_auth.py` | `--check`, `--refresh` |

## systemd user units

Shipped in `deploy/systemd/` (copy to `~/.config/systemd/user/`):

| Unit | Notes |
|---|---|
| `wa-notify-server.service` | `node server.js`, `Restart=always`, `WorkingDirectory=%h/<path-to-clone>/local-server`. Edit the path. Pass env vars with `Environment=` lines. |
| `wa-reel-alert.service` | `After=wa-notify-server.service graphical-session.target`, `Restart=on-failure`, `PYTHONUNBUFFERED=1`. Needs the session's `WAYLAND_DISPLAY` and D-Bus (normally imported by your compositor). Add `ExecStart=… --download` / `--autoplay` as desired. |
| `wa-reel-cleanup.service` + `.timer` | daily one-shot: `wa-reel-cleanup --days 7`. |

`ExecStart` lines assume the tools are symlinked into `~/.local/bin/` — see [OPERATIONS.md](OPERATIONS.md).
