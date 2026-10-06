# Deployment reference files

Reference configuration for running wa-notify as background services on **Linux with systemd, Wayland and Hyprland**.
Nothing here is installed automatically: copy what you need and adapt the marked paths. This is a hobby project
tuned to one machine, so treat these files as a working example, not a portable installer.

| Path | Purpose | Platform dependence |
| --- | --- | --- |
| `systemd/wa-notify-server.service` | Local ingest server (Node) | systemd (user instance) |
| `systemd/wa-reel-alert.service` | Alert daemon and fetch queue | systemd, Wayland session in the user manager |
| `systemd/wa-reel-cleanup.{service,timer}` | Daily disk cleanup | systemd |
| `hyprland/wa-reel.conf` | Floating, pinned player window + keybinds | Hyprland only (block syntax needs >= 0.53) |
| `mako/wa-reels.conf` | Notification styling | mako only (optional) |

## Install order

```bash
# 1. Make the tools available on your PATH (adapt the clone location)
mkdir -p ~/.local/bin
for t in alert menu digest cleanup; do ln -sf "$PWD/tools/wa-reel-$t.py" ~/.local/bin/wa-reel-$t; done
ln -sf "$PWD/tools/wa_reel_dl.py"  ~/.local/bin/wa-reel-dl
ln -sf "$PWD/tools/wa_reel_ai.py"  ~/.local/bin/wa-reel-summary
ln -sf "$PWD/tools/wa_reel_auth.py" ~/.local/bin/wa-reel-auth

# 2. systemd user units: edit WorkingDirectory in wa-notify-server.service first
mkdir -p ~/.config/systemd/user
cp deploy/systemd/* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now wa-notify-server.service
systemctl --user enable --now wa-reel-alert.service
systemctl --user enable --now wa-reel-cleanup.timer

# 3. Desktop integration (optional)
cp deploy/hyprland/wa-reel.conf ~/.config/hypr/conf/   # then add: source = ~/.config/hypr/conf/wa-reel.conf
cat deploy/mako/wa-reels.conf >> ~/.config/mako/config && makoctl reload
```

The symlinks only work because the tools resolve their own location with `os.path.realpath`; do not copy the
scripts out of the repository.

## Checking it works

```bash
systemctl --user status wa-notify-server wa-reel-alert
systemctl --user list-timers wa-reel-cleanup.timer
journalctl --user -u wa-reel-alert -f          # look for "[✓] prefetch ... ready" lines
curl -s http://127.0.0.1:8765/health           # server answers with {"status":"ok", ...}
```

## Things to know

- **Wayland environment.** The alert daemon starts `mpv`, `notify-send` and `wl-copy`. If the systemd user manager
  does not know `WAYLAND_DISPLAY`, nothing appears. Export it once per login:
  `dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP`.
- **Matching values.** The window rules hard-code `wa-reel`, `wa-reel-digest` and `420 750`. They must equal the
  `[ui]` values in `~/.config/wa-notify/config.toml` if you change those.
- **Environment overrides.** `WA_NOTIFY_PORT`, `WA_NOTIFY_DATA_DIR`, `WA_NOTIFY_CONFIG_DIR`, `WA_NOTIFY_EXTENSION_ID`
  and `WA_NOTIFY_JSONL_BACKUP` are documented in `docs/CONFIGURATION.md`. Set the same data/config directories on
  the server and the alert units, otherwise they will look at different databases.
- **Removal.** `systemctl --user disable --now wa-reel-cleanup.timer wa-reel-alert wa-notify-server`, delete the
  unit files and run `systemctl --user daemon-reload`. Your archive stays in `~/.local/share/wa-notify` until you delete it.
