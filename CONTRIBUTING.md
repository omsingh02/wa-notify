# Contributing

wa-notify is a **hobby project** with a single maintainer, built for one person's Arch Linux + Hyprland desktop.
Issues and pull requests are welcome, but there is **no SLA** and they may sit unanswered for weeks. Please
read [docs/LIMITATIONS.md](docs/LIMITATIONS.md) first: most "doesn't work on my system" reports are known platform limits.

## Ground rules

* Be kind — see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
* Do not post real chat content, phone numbers/JIDs, tokens, cookies or API keys in issues. Scrub logs (sender names appear in the alert log).
* Security problems: use the private channel in [SECURITY.md](SECURITY.md), not a public issue.
* Scope: capture, archive, reel pipeline. Features that need a different platform (X11, macOS, other browsers) must live **behind an adapter/config option** and must not break the Hyprland default.

## Development setup

```bash
git clone https://github.com/omsingh02/wa-notify.git && cd wa-notify
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt        # pytest, ruff, yt-dlp (see the file for the exact set)
```

Node ≥ 22.13 is needed for the server and its tests (`node:sqlite`).

## Tests and lint

```bash
pytest                                     # Python: unit + flow tests with fake yt-dlp / mpv / notify-send; no network, no real desktop
node --test                                # server (sandbox port + temp dirs) and extension queue/helper tests
ruff check . && ruff format --check .      # lint + format
node --check extension/src/*.js local-server/server.js
```

Tests must never touch your real `~/.config/wa-notify`, `~/.local/share/wa-notify`, the live server on `:8765`, or real `mpv`/`pkill`. Use temp dirs, random ports and stubs on `PATH`.

## Pull requests

1. Open an issue first for anything larger than a small fix.
2. Branch from `main`; keep PRs focused (one concern each).
3. Add or update tests; update the docs and `CHANGELOG.md` (*Unreleased*).
4. Use [Conventional Commits](https://www.conventionalcommits.org/): `feat: …`, `fix: …`, `docs: …`, `refactor: …`, `test: …`, `ci: …`, `chore: …`.
5. CI must pass. Squash-merge is the default.

## Code style

* Python: type hints on public functions, docstrings that explain *why*, no new hard-coded paths/values without a named constant (and, if user-facing, a `config.toml`/env override), no silent `except Exception: pass`.
* JavaScript: no dependencies; keep the extension service worker free of module-level state that cannot survive a restart.
* Every new hard-coded value or platform dependency goes into [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

## Licensing

By contributing you agree that your contribution is licensed under the project's [MIT license](LICENSE).
