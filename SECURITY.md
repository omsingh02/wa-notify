# Security policy

wa-notify is a **hobby project**: single maintainer, no warranty, no response-time guarantee. Reports are still appreciated and will be handled on a best-effort basis.

## Supported versions

Only the latest release on `main`. There are no backports.

## Reporting a vulnerability

Please use **GitHub's private vulnerability reporting**: *Security → Report a vulnerability* on the repository
(`https://github.com/omsingh02/wa-notify/security/advisories/new`). Do **not** open a public issue.
Include what you found, how to reproduce it and the impact. Never include real messages, phone numbers/JIDs, tokens, cookies or keys.

## What is sensitive

| Asset | Where | Why it matters |
|---|---|---|
| Message archive | `wa-notify.db`, `chat_archive.jsonl` | other people's messages, JIDs/phone numbers, link previews; `raw_json` also holds WhatsApp media keys and CDN paths |
| Pairing token | `~/.config/wa-notify/token.txt`, extension storage | lets a local client write to the archive |
| Instagram cookies | `~/.config/wa-notify/instagram-cookies.txt` (`0600`) | a logged-in session |
| Gemini API key | `GEMINI_API_KEY_REELS` / `~/.config/.secrets` | billable credential |

All are plain, unencrypted files protected only by file modes, your user account and home-directory permissions. Encrypt backups and keep them out of cloud sync.

## Threat model (what the design does and does not defend against)

In scope:
* Other **web pages in your browser** trying to write to or read from the loopback server: loopback bind, shared token, CORS allow-list, Host allow-list (DNS rebinding).
* Malformed or oversized requests: body size cap, payload validation, `5xx` on failure instead of silent loss.
* Credential handling: Instagram-only cookie cache with `0600`, per-run private copies, no cookie values in logs or argv.

Out of scope / known gaps (see [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md)):
* Anything running as your user (malware, other local processes) — it can read the database and the cookie cache directly.
* An **unpaired** server accepts the first token it sees (trust on first use) and unauthenticated writes until then.
* The extension relays page events without authentication; a script in the WhatsApp page context can forge entries.
* WhatsApp's and Instagram's own security and terms; this tool deliberately hooks private internals.
* Multi-user machines: the server creates its directories `0700` and keeps the database, the JSONL backup and `token.txt` `0600` (best effort, at startup), and the cookie cache is `0600`; but downloaded media, summaries and any directory you created yourself use default modes and rely on your home directory's permissions.

## Disclosure

Fixes are released on `main` and noted in [CHANGELOG.md](CHANGELOG.md). Credit is given unless you prefer otherwise.
