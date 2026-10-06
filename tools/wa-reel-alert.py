#!/usr/bin/env python3
"""
wa-reel-alert — WhatsApp Reel Live Notifier & Background Worker (v2 Architecture)
Monitors SQLite database (~/.local/share/wa-notify/wa-notify.db) for new reels.

Key Features:
- Respects freshness filtering: live reels trigger desktop alerts and downloads;
  historical / chat-scroll reels are archived silently without notification floods.
- Background pre-downloading on unmetered / fast networks.
- Triggers asynchronous multimodal AI summarization on download completion.
- Interactive Mako notifications with instant MPV playback.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import threading
import time

TOOLS_DIR = os.path.dirname(os.path.realpath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from walib import (
    UI, get_db, init_db, get_local_reel_file, get_local_images,
    play_in_mpv, copy_to_clipboard,
)
from wa_reel_dl import fetch_media
from wa_reel_queue import Prefetcher

MIN_WIFI_MBIT = 25  # on Wi-Fi, links slower than this don't get background pre-downloads
HELPER_TIMEOUT = 1.5  # seconds allowed for nmcli / ping


def _run_text(cmd: list[str], timeout: float = HELPER_TIMEOUT) -> str | None:
    """stdout of a short helper command, or None if it can't run or times out."""
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def is_metered() -> bool:
    """True if NetworkManager says a connection is metered (hotspot, tethering, ...)."""
    out = _run_text(["nmcli", "-t", "-f", "GENERAL.METERED", "dev", "show"])
    return bool(out) and "yes" in out.lower()


def wifi_rate_mbit() -> int | None:
    """Link rate of the active Wi-Fi connection in Mbit/s, or None when not on Wi-Fi / unknown."""
    out = _run_text(["nmcli", "-t", "-f", "IN-USE,RATE", "dev", "wifi"])
    for line in (out or "").splitlines():
        if line.startswith("*"):
            m = re.search(r"(\d+)", line.split(":")[-1])
            if m:
                return int(m.group(1))
    return None


def internet_reachable() -> bool:
    """One ping to a public resolver; if ping itself is unavailable we assume we're online."""
    try:
        return subprocess.run(["ping", "-c", "1", "-W", "1", "1.1.1.1"], stdout=subprocess.PIPE, text=True,
                              timeout=HELPER_TIMEOUT).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True


def is_fast_network() -> bool:
    """Whether background pre-downloading is sensible: not metered, and on Wi-Fi fast enough (else just online)."""
    if is_metered():
        return False
    rate = wifi_rate_mbit()
    if rate is not None:
        return rate >= MIN_WIFI_MBIT
    return internet_reachable()


def trigger_summary(reel_id):
    """Invokes multimodal summarizer in background and updates DB."""
    try:
        from wa_reel_ai import summarize_reel
        summary = summarize_reel(reel_id)
        if summary:
            first_line = summary.replace('\n', ' ')
            print(f"[✓] AI Summary ({reel_id}): {first_line[:75]}...")
    except Exception as e:
        print(f"[!] AI Summary error for {reel_id}: {e}", file=sys.stderr)

_summary_slots = threading.Semaphore(2)

def _summarize_in_background(reel_id, res):
    """Fetch-queue callback: summarize a downloaded video without holding up the next download."""
    if res.kind != 'video':
        return
    def worker():
        with _summary_slots:
            trigger_summary(reel_id)
    threading.Thread(target=worker, daemon=True).start()

# One worker fetches reel media one at a time, pauses between requests, cools down when Instagram
# blocks and retries failures later (see wa_reel_queue.py).
prefetcher = Prefetcher(
    fetch=lambda url, reel_id: fetch_media(url, timeout=120, quiet=True, socket_timeout=30),
    on_success=_summarize_in_background,
)

def seed_backlog(limit=10, hours=24):
    """After a restart, queue recent unopened reels that never got their media (e.g. while Instagram was blocking)."""
    since_ms = (time.time() - hours * 3600) * 1000
    with get_db() as conn:
        rows = conn.execute(
            "SELECT reel_id, url, first_seen_at FROM reels "
            "WHERE is_likely_live = 1 AND is_opened = 0 AND COALESCE(sender, '') != 'You' AND first_seen_at > ? "
            "ORDER BY first_seen_at DESC LIMIT 60",
            (since_ms,)
        ).fetchall()
    queued = 0
    for r in rows:
        if queued >= limit:
            break
        if get_local_reel_file(r['reel_id']) or get_local_images(r['reel_id']):
            continue
        if prefetcher.submit(r['reel_id'], r['url'], (r['first_seen_at'] or 0) / 1000 or None):
            queued += 1
    if queued:
        print(f"[*] Queued {queued} recent reel(s) that never got downloaded")

def format_notification(sender: str, reel_url: str) -> tuple[str, str]:
    clean_url = reel_url.split("?")[0].replace("https://", "").replace("http://", "").rstrip("/")
    return sender, clean_url

def notify_reel(reel, args):
    reel_id = reel['reel_id']
    sender = reel['sender'] or 'Unknown'
    url = reel['url']

    print(f"\n[!] >>> LIVE REEL DETECTED <<<")
    print(f"    From: {sender}")
    print(f"    URL:  {url}")
    print(f"    ID:   {reel_id}")

    # Reels you sent yourself aren't worth the requests; they still fetch on demand when opened.
    if sender != 'You' and (args.download or is_fast_network()):
        prefetcher.submit(reel_id, url, (reel.get('first_seen_at') or 0) / 1000 or None)

    if args.autoplay:
        play_in_mpv(reel_id, url)
        summary, body = format_notification(sender, url)
        subprocess.run([
            "notify-send", "-a", UI.notify_app_name, "-i", "instagram", summary, body
        ], check=False)
        return

    def notify_worker():
        summary, body = format_notification(sender, url)
        cmd = [
            "notify-send",
            "-a", UI.notify_app_name,
            "-i", "instagram",
            "-u", "normal",
            "-A", "default=Play in MPV",
            "-A", "play=Play in MPV",
            "-A", "copy=Copy Link",
            summary,
            body
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, check=False)
            action = res.stdout.strip()
            if action in ("default", "play"):
                play_in_mpv(reel_id, url)
            elif action == "copy":
                copy_to_clipboard(url)
        except Exception as e:
            print(f"[!] Notification error: {e}", file=sys.stderr)

    threading.Thread(target=notify_worker, daemon=True).start()

def poll_reels(args):
    init_db()
    print("[*] wa-reel-alert v2 active — monitoring SQLite database")
    print(f"[*] Fast network pre-downloading: enabled | Autoplay: {args.autoplay}")

    prefetcher.start()
    seed_backlog()

    while True:
        try:
            with get_db() as conn:
                unhandled = conn.execute(
                    "SELECT * FROM reels WHERE alerted = 0 ORDER BY first_seen_at ASC LIMIT 20"
                ).fetchall()

            if unhandled:
                for row in unhandled:
                    reel = dict(row)
                    reel_id = reel['reel_id']
                    is_live = bool(reel.get('is_likely_live', 0))

                    if is_live:
                        notify_reel(reel, args)
                    else:
                        print(f"[*] Archived historical reel without alert: {reel_id} (from {reel.get('sender')})")

                    # Mark alerted in DB
                    with get_db() as conn:
                        conn.execute("UPDATE reels SET alerted = 1 WHERE reel_id = ?", (reel_id,))

            time.sleep(1.5)

        except KeyboardInterrupt:
            print("\n[*] Exiting wa-reel-alert.")
            break
        except Exception as e:
            print(f"[!] Alert loop error: {e}", file=sys.stderr)
            time.sleep(2.0)

def main():
    parser = argparse.ArgumentParser(description="WhatsApp Instagram Reel Live Monitor & PiP Player (v2)")
    parser.add_argument("--autoplay", action="store_true", help="Immediately spawn MPV without waiting for notification click")
    parser.add_argument("--download", action="store_true", help="Always download video regardless of network link rate")
    args = parser.parse_args()

    poll_reels(args)

if __name__ == '__main__':
    main()
