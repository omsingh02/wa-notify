## What and why

<!-- One or two sentences. Link the issue if there is one: Fixes #123 -->

## Checklist

- [ ] Tests added or updated (`pytest -q` and `node --test` pass locally)
- [ ] `ruff check .` and `ruff format --check .` are clean
- [ ] Docs updated (README / `docs/`) if behaviour or configuration changed
- [ ] No chat content, JIDs, tokens, cookies or personal paths in code, tests, fixtures or logs
- [ ] Commit messages follow Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:` ...)

## Platform and live-pipeline notes

- [ ] This change is platform-neutral, **or** the Linux/Wayland/Hyprland (or other) assumption is documented in `docs/LIMITATIONS.md`
- [ ] Does this touch the live pipeline (extension protocol, `POST /log` payload, SQLite schema, systemd units)? <!-- yes/no -->
  - If yes: describe the migration or reload steps users need (reload the extension, restart `wa-notify-server`, ...)
