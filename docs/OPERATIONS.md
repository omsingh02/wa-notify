# Operations

> Hobby project, written for one Arch Linux + Hyprland machine. Treat these steps as a worked example, not a supported installer.

## Install (summary)

1. Install the dependencies (Arch package names; adapt for your distro):
   `nodejs` (≥ 22.13), `python` (≥ 3.11), `yt-dlp`, `ffmpeg`, `mpv`, `fuzzel`, `fzf`, `foot`, `wl-clipboard`, `libnotify` + a notification daemon (`mako`), `python-rich` (optional), `python-secretstorage` (only for the Instagram session).
2. Clone the repository anywhere. Symlink the entry points into `~/.local/bin` (names are conventions; the unit files expect them):
   ```bash
   cd tools
   for t in alert:wa-reel-alert.py menu:wa-reel-menu.py digest:wa-reel-digest.py cleanup:wa-reel-cleanup.py dl:wa_reel_dl.py summary:wa_reel_ai.py auth:wa_reel_auth.py; do
     ln -sf "$PWD/${t#*:}" ~/.local/bin/wa-reel-${t%%:*}
   done
   ```
3. Copy the units from `deploy/systemd/` to `~/.config/systemd/user/`, **edit the `WorkingDirectory=` path**, then:
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now wa-notify-server.service wa-reel-alert.service wa-reel-cleanup.timer
   ```
4. Load the extension: `chrome://extensions` → Developer mode → *Load unpacked* → `extension/`. Open WhatsApp Web and check the devtools console for `[wa-notify] message store located`.
5. Compositor/notification glue: `deploy/hyprland/` and `deploy/mako/` (see below).
6. Optional: `~/.config/wa-notify/config.toml` ([CONFIGURATION.md](CONFIGURATION.md)) and a Gemini key.

## Upgrade

```bash
git pull
systemctl --user restart wa-notify-server.service wa-reel-alert.service
```

* **Reload the extension** in `chrome://extensions`. Open WhatsApp Web tabs then get a fresh relay automatically (the extension re-attaches on install/update), so capture continues without a refresh. **The first upgrade from a version before that change still leaves the old script running in the tab: refresh the tab once.** `injected.js` (the page hook) is only replaced when the tab is refreshed. An orphaned copy logs `The extension was reloaded or updated …` once and stops; older versions threw `Extension context invalidated` on every message and captured nothing until the tab was refreshed.
* The Python menu/digest are started fresh on each use and pick up changes immediately.
* After installing `python-secretstorage` (or any Python package yt-dlp probes at import time) **restart the alert daemon**; yt-dlp caches the "module missing" result per process.

## Rollback

Keep a copy before upgrading (`cp -a tools extension local-server /somewhere`), or use `git checkout <tag>` and repeat the restart/reload steps. The database schema has no migrations, so a rollback across schema-changing releases is **not** guaranteed to work.

## Hyprland (window rules and keybinding)

The player window is matched by **class and title** `wa-reel`; the digest terminal by class `wa-reel-digest`. Example (the block syntax the author's Hyprland accepts; older releases use `windowrulev2` lines — check the wiki for your version):

```ini
windowrule {
    name = wa-reel-player
    match:class = ^(wa-reel)$
    float = 1
    size = 420 750
    pin = 1
    move = 72% 15%
}
windowrule {
    name = wa-reel-digest-rules
    match:class = ^(wa-reel-digest)$
    float = 1
    size = 1000 600
    center = 1
}
bind = $mainMod SHIFT, R, exec, ~/.local/bin/wa-reel-menu
```

Photo posts use the same window, so the same rules apply. **Pitfall:** if you generate this file from a shell heredoc without quoting the delimiter (`<<'EOF'`), `$mainMod` expands to nothing and you bind plain Shift+R — every capital R you type opens the menu.

## mako (notification theme)

`wa-reel-alert` and the menu use `notify-send -a wa-reels` (`notify_app_name`). Example section:

```ini
[app-name=wa-reels]
border-color=#E1306C
border-size=2
default-timeout=12000
format=<b>%s</b>\n%b
```

mako config values are single-line: write the newline inside `format=` as the two characters `\n` (the version in `deploy/mako/wa-reels.conf` also writes a literal `%` as `%%`). Clicking a notification to play needs `on-button-left=invoke-default-action`; `deploy/mako/wa-reels.conf` sets it explicitly (it is also mako's built-in default, per mako(5), so the line only documents the dependency). mako(5) also confirms the single-line `key=value` format, `\n` as the newline escape inside `format`, and `%%` as a literal percent.

## Logs

```bash
journalctl --user -u wa-notify-server.service -f
journalctl --user -u wa-reel-alert.service -f      # fetch queue, summaries, pauses
journalctl --user -u wa-reel-cleanup.service
```

Useful lines in the alert log: `[✓] prefetch <id>: video ready` / `images ready`, `Instagram is blocking downloads — pausing the fetch queue for N min`, `Can't reach Instagram (…) — pausing …`, `AI Summary (<id>): …`.
Sender names appear in the log under `LIVE REEL DETECTED`; scrub before sharing a log.

## Cleanup and backups

* `wa-reel-cleanup` (daily timer) deletes cached media of reels **opened** more than 7 days ago (videos, photo-post images, leftovers) and abandoned partial downloads older than 3 days. Unopened reels' media is never deleted. `wa-reel-cleanup --dry-run` shows what it would do.
* Back up the database with the SQLite online backup, not a plain copy, while the server runs:
  `sqlite3 "${WA_NOTIFY_DATA_DIR:-$HOME/.local/share/wa-notify}/wa-notify.db" ".backup 'wa-notify-backup.db'"`.
* The archive is sensitive: encrypt backups, and remember that cloud-synced home directories copy other people's messages.

## Landing page (Vercel)

`site/` is a static, dependency-free page (no build step, no scripts). It is hosted on Vercel.

**Git deploys.** The Vercel project is connected to this repository with the production branch `main` and *Root Directory*
`site`. A push to `main` deploys the page to production; other branches and pull requests get preview deployments, which
Vercel Authentication protects while the repository is private. Nothing in the extension, server or tools talks to the page.

**CLI deploys** work too, but because the Root Directory is `site` they must run from the **repository root**. From `site/`
Vercel fails with `The specified Root Directory "site" does not exist`.

```bash
vercel link --project <name>     # once, at the repository root; writes .vercel/ and .env.local, and may append to
                                 # .gitignore (the patterns are already there: discard that change and delete .env.local)
vercel deploy --prod --yes       # drop --prod for a preview; retry if the network is flaky
```

**Connecting a repository** (what was done here): authorize Vercel's GitHub app for the repository (a private repository is
invisible to `vercel git connect` until you do), then in the dashboard open Project → Settings → Git and Settings → Build
& Deployment, and set Root Directory to `site` **and save**. Check the result instead of trusting the form:

```bash
vercel api /v9/projects/<name> | grep -E '"rootDirectory"|"productionBranch"'
vercel api /v9/projects/<name> -X PATCH -f rootDirectory=site     # if rootDirectory is null
```

A Git build with the Root Directory unset builds the repository root, which has no `index.html`, so the next push would replace
a working page with a broken one.

## Troubleshooting playbook

| Symptom | Likely cause | Fix |
|---|---|---|
| Console / extension Errors page: `Extension context invalidated` or `The extension was reloaded or updated` | the tab still runs a script from before the last extension reload; nothing is captured from it | refresh that WhatsApp Web tab (once) |
| No capture, console says it gave up after ~120 s | WhatsApp changed its internal module names | `window.__waNotifyDebug()` in the WhatsApp tab; adjust `WELL_KNOWN_MODULES` in `injected.js`; reload the extension |
| Server returns `401` / extension queue grows | token mismatch (reinstalled extension, second browser profile) | delete `~/.config/wa-notify/token.txt`, restart nothing, reload the extension; the next request re-pairs |
| Reels open in the browser, journal shows `blocking downloads` | Instagram rate-limits anonymous access (`login page … exceeded the rate-limit`) | wait (the queue retries itself), or configure `[instagram]`; see README |
| Journal shows `Resolving timed out`, `Temporary failure in name resolution`, or lookups take 10–20 s | local DNS flapping (e.g. `systemd-resolved` switching between DNS-over-TLS and UDP: `journalctl -u systemd-resolved` shows "Using degraded feature set …") | fix the resolver; wa-notify only pauses and retries. `resolvectl query www.instagram.com` shows the stall |
| `database is locked` at server start | another process held a lock before the busy timeout was set (fixed in 0.2.0: `busy_timeout` now comes first and startup retries; covered by the server test "startup waits for a lock held by another process…") | upgrade; on older versions systemd restarts the unit |
| `secretstorage not available` warning | `python-secretstorage` not installed | install it, restart `wa-reel-alert.service` |
| Menu does nothing from a keybinding | `~/.local/bin` not in the compositor's `PATH`, or `fuzzel` not installed | use absolute paths in the bind |
| Floating window not floating | window rules missing or wrong class | `hyprctl clients -j` and check `class` is `wa-reel` |
| Gemini `429` / `503` in the log | free-tier quota / overload | summaries fall back to the second model and may stay `pending`; no retry sweep yet |
