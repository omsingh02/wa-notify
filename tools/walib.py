#!/usr/bin/env python3
"""
walib.py — Shared Utilities & Database Layer for wa-notify v2
Provides unified SQLite database access, path constants, reel ID extraction,
opened-state tracking, MPV playback, and notification formatting across all tools.

Paths and desktop settings come from wa_settings (defaults, config.toml, WA_NOTIFY_* env vars).
"""

from __future__ import annotations

import contextlib
import os
import re
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Iterable
from datetime import datetime

TOOLS_DIR = os.path.dirname(os.path.realpath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from wa_settings import load as load_settings  # noqa: E402

SETTINGS = load_settings()
UI = SETTINGS.ui

# Path constants (plain strings, for os.path-style callers)
DATA_DIR = str(SETTINGS.data_dir)
DB_PATH = str(SETTINGS.db_path)
REELS_DIR = str(SETTINGS.reels_dir)
SUMMARIES_DIR = str(SETTINGS.summaries_dir)
CONFIG_DIR = str(SETTINGS.config_dir)
TOKEN_FILE = str(SETTINGS.token_file)
SECRETS_FILE = str(SETTINGS.secrets_file)


def get_db() -> sqlite3.Connection:
    """
    Returns a connection to the SQLite database configured with WAL mode
    and busy_timeout to prevent lock contention between Node and Python writers.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def init_db() -> None:
    """Initializes tables and indexes if they do not exist."""
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            chat_id TEXT,
            sender TEXT,
            is_from_me INTEGER DEFAULT 0,
            message_ts INTEGER,
            captured_at INTEGER,
            is_likely_live INTEGER DEFAULT 0,
            type TEXT,
            body TEXT,
            caption TEXT,
            link_url TEXT,
            link_title TEXT,
            raw_json TEXT
        );

        CREATE TABLE IF NOT EXISTS reels (
            reel_id TEXT PRIMARY KEY,
            message_id TEXT REFERENCES messages(id),
            sender TEXT,
            chat_id TEXT,
            url TEXT,
            title TEXT,
            timestamp INTEGER,
            first_seen_at INTEGER,
            is_likely_live INTEGER DEFAULT 0,
            is_downloaded INTEGER DEFAULT 0,
            local_path TEXT,
            is_opened INTEGER DEFAULT 0,
            opened_at INTEGER,
            alerted INTEGER DEFAULT 0,
            summary TEXT,
            summary_status TEXT DEFAULT 'pending'
        );

        CREATE INDEX IF NOT EXISTS idx_reels_unopened ON reels (is_opened, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_reels_alert ON reels (alerted, is_likely_live, first_seen_at ASC);
        CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages (message_ts DESC);
        """)


def get_reel_id(url: str) -> str:
    """Extracts clean Instagram Reel/Post ID from any URL."""
    clean = url.split("?")[0].rstrip("/")
    return clean.split("/")[-1] if "/" in clean else clean


def get_local_reel_file(reel_id_or_url: str) -> str | None:
    """Returns local path to downloaded video if exists and non-empty."""
    reel_id = get_reel_id(reel_id_or_url)
    for ext in (".mp4", ".mkv", ".webm"):
        p = os.path.join(REELS_DIR, f"{reel_id}{ext}")
        if os.path.exists(p) and os.path.getsize(p) > 10000:
            return p
    return None


IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def get_local_images(reel_id_or_url: str) -> list[str]:
    """Local images of a photo post in carousel order (<id>_1.jpg, <id>_2.jpg, ...). Empty list if none."""
    reel_id = get_reel_id(reel_id_or_url)
    found = []
    n = 1
    while True:
        for ext in IMAGE_EXTS:
            p = os.path.join(REELS_DIR, f"{reel_id}_{n}{ext}")
            if os.path.exists(p) and os.path.getsize(p) > 1000:
                found.append(p)
                break
        else:
            return found
        n += 1


def reel_media_files(reel_id: str) -> list[str]:
    """Every cached file that belongs to a reel: its video, a photo post's images and download leftovers."""
    pattern = re.compile(rf"^{re.escape(reel_id)}(?:\.[^/]+|_\d+\.(?:jpe?g|png|webp))$")
    try:
        names = os.listdir(REELS_DIR)
    except OSError:
        return []
    return sorted(os.path.join(REELS_DIR, n) for n in names if pattern.match(n))


def mark_as_opened(reel_id_or_url: str) -> None:
    """Marks a single reel as opened in SQLite."""
    reel_id = get_reel_id(reel_id_or_url)
    now = int(datetime.now().timestamp())
    with get_db() as conn:
        conn.execute("UPDATE reels SET is_opened = 1, opened_at = ? WHERE reel_id = ?", (now, reel_id))


def mark_opened_many(reel_ids: Iterable[str]) -> int:
    """Marks exactly these reels as opened (not every unopened one, so reels that arrived since are untouched)."""
    ids = [get_reel_id(r) for r in reel_ids]
    if not ids:
        return 0
    now = int(datetime.now().timestamp())
    with get_db() as conn:
        conn.executemany("UPDATE reels SET is_opened = 1, opened_at = ? WHERE reel_id = ?", [(now, i) for i in ids])
    return len(ids)


def mark_all_as_opened() -> None:
    """Marks all unopened reels as opened in SQLite (prefer mark_opened_many for a listed set)."""
    now = int(datetime.now().timestamp())
    with get_db() as conn:
        conn.execute("UPDATE reels SET is_opened = 1, opened_at = ? WHERE is_opened = 0", (now,))


def get_unopened_reels() -> list[dict]:
    """Returns list of unopened reels from SQLite ordered by newest first."""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM reels WHERE is_opened = 0 ORDER BY timestamp DESC").fetchall()
        return [dict(r) for r in rows]


def get_opened_reels(limit: int = 50) -> list[dict]:
    """Returns list of already opened reels from SQLite ordered by newest timestamp first."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM reels WHERE is_opened = 1 ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_reels(limit: int = 50) -> list[dict]:
    """Returns list of all reels from SQLite ordered by newest first."""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM reels ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


# One floating, pinned window for everything (compositor rules match the class/title, ui.app_id).
MPV_BASE = [
    "mpv",
    f"--wayland-app-id={UI.app_id}",
    f"--title={UI.app_id}",
    "--no-border",
    "--force-window=immediate",
    f"--autofit={UI.player_size}",
]

# Shown in the notification when a reel has to be opened in the browser after all.
FETCH_FAILURE_TEXT = {
    "blocked": "Instagram is blocking downloads right now (login / rate limit)",
    "network": "Couldn't reach Instagram (network / DNS problem)",
    "no_video": "Photo post — the images couldn't be fetched",
    "unavailable": "The post is private or no longer available",
    "timeout": "The download timed out",
    "empty": "Instagram returned no media",
    "no_formats": "Instagram returned no playable video",
    "no_ytdlp": "yt-dlp is not installed",
}


def _notify(summary: str, body: str = "", timeout_ms: int = 6000) -> None:
    """Best-effort low-urgency desktop notification; never raises."""
    with contextlib.suppress(Exception):
        subprocess.run(
            [
                "notify-send",
                "-a",
                UI.notify_app_name,
                "-i",
                "instagram",
                "-u",
                "low",
                "-t",
                str(timeout_ms),
                summary,
                body,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )


def play_in_mpv(reel_id_or_url: str, fallback_url: str | None = None) -> subprocess.Popen | None:
    """
    Shows a reel in the floating, pinned mpv window: a video plays on loop, a photo post shows its
    images (< and > switch between them). Anything not cached yet is fetched first; the browser is
    only a last resort, and a notification says why.
    """
    reel_id = get_reel_id(reel_id_or_url)
    url = fallback_url or f"https://www.instagram.com/reel/{reel_id}"

    video = get_local_reel_file(reel_id)
    images = [] if video else get_local_images(reel_id)
    failure = None

    if not video and not images:
        from wa_reel_dl import fetch_media

        print(f"[*] Reel {reel_id} not cached locally — fetching...")
        # only bother the user if the fetch turns out to take a while
        slow = threading.Timer(
            2.0, _notify, args=("Fetching reel…", "It opens in the floating player as soon as it's downloaded")
        )
        slow.daemon = True
        slow.start()
        try:
            res = fetch_media(url, timeout=60, quiet=True, interactive=True)
        finally:
            slow.cancel()
        if res.ok:
            video, images = (res.path, []) if res.kind == "video" else (None, list(res.paths))
            print(f"[✓] Fetched {res.kind}: {', '.join(res.paths)}")
        else:
            failure = res

    # Now we know what is about to be shown, so this is when it counts as opened.
    mark_as_opened(reel_id)

    # Terminate previous floating reel MPV window to avoid stacking (after the fetch, so it keeps playing meanwhile)
    with contextlib.suppress(Exception):
        subprocess.run(["pkill", "-f", f"title={UI.app_id}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if not video and not images:
        why = FETCH_FAILURE_TEXT.get(getattr(failure, "reason", None), "The download failed")
        detail = f" ({failure.detail})" if failure is not None and failure.detail else ""
        print(f"[!] Couldn't fetch {reel_id}: {why}{detail}")
        print(f"[*] Opening in browser: {url}")
        _notify("Opening in browser", why, timeout_ms=8000)
        try:
            subprocess.Popen(
                ["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
            )
        except Exception as e:
            print(f"[!] Failed to open browser: {e}")
        return None

    if video:
        targets = [video]
        extra = ["--loop-file=inf"]
    else:
        targets = images
        extra = ["--image-display-duration=inf", "--loop-playlist=inf"]

    print(f"[*] Spawning MPV for: {', '.join(targets)}")
    try:
        proc = subprocess.Popen(
            MPV_BASE + extra + targets, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
        return proc
    except Exception as e:
        print(f"[!] Failed to spawn MPV: {e}", file=sys.stderr)
        return None


def copy_to_clipboard(text: str) -> bool:
    """Copies text to Wayland clipboard via wl-copy."""
    try:
        proc = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
        proc.communicate(input=text.encode("utf-8"))
        return True
    except Exception as e:
        print(f"[!] wl-copy failed: {e}", file=sys.stderr)
        return False


def format_time(ts: float | None) -> tuple[str, str]:
    """Formats epoch timestamp into concise string + relative time."""
    if not ts:
        return "recently", ""
    try:
        dt = datetime.fromtimestamp(ts)
        now = datetime.now()
        diff = int((now - dt).total_seconds())
        if diff < 60:
            rel = "just now"
        elif diff < 3600:
            rel = f"{diff // 60}m ago"
        elif diff < 86400:
            rel = f"{diff // 3600}h ago"
        else:
            rel = f"{diff // 86400}d ago"
        time_str = dt.strftime("%H:%M") if dt.date() == now.date() else dt.strftime("%b %d %H:%M")
        return time_str, rel
    except Exception:
        return "recently", ""


if __name__ == "__main__":
    init_db()
    print(f"[✓] Initialized database at {DB_PATH}")
