#!/usr/bin/env python3
"""
wa-reel-cleanup.py — Automatic Disk Retention Cleaner for wa-notify
Prunes cached reel media (videos, and the images of photo posts) for reels that were opened
more than 7 days ago, preventing unbounded disk growth. Also removes abandoned download
leftovers (.part / .ytdl / yt-dlp's .fdash-* intermediates) a few days after their last write.
"""

import os
import sys
import time
import argparse

TOOLS_DIR = os.path.dirname(os.path.realpath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from walib import get_db, reel_media_files, REELS_DIR

DEFAULT_RETENTION_DAYS = 7
STALE_PARTIAL_DAYS = 3

def cleanup(retention_days=DEFAULT_RETENTION_DAYS, dry_run=False):
    cutoff_ts = int(time.time()) - (retention_days * 86400)
    print(f"[*] Checking for opened reels older than {retention_days} days (cutoff: {time.ctime(cutoff_ts)})...")

    reclaimed_bytes = 0
    deleted_count = 0
    reset_ids = []
    handled = set()   # files already dealt with (or listed, in a dry run) as part of an opened reel

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT reel_id, local_path, opened_at
            FROM reels
            WHERE is_opened = 1 AND opened_at IS NOT NULL AND opened_at < ?
            """,
            (cutoff_ts,)
        ).fetchall()

    # File work happens outside any database transaction so the Node server's inserts are never blocked by it.
    for row in rows:
        reel_id = row['reel_id']
        files = reel_media_files(reel_id)   # the video, a photo post's images, download leftovers
        path = row['local_path']
        if path and os.path.exists(path) and path not in files:
            files.append(path)
        if not files:
            continue

        all_removed = True
        for path in files:
            handled.add(path)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if dry_run:
                print(f"  [DRY-RUN] Would delete {path} ({size / 1024 / 1024:.1f} MB)")
                continue
            try:
                os.remove(path)
                print(f"  [✓] Removed: {path} ({size / 1024 / 1024:.1f} MB)")
                deleted_count += 1
                reclaimed_bytes += size
            except OSError as e:
                all_removed = False
                print(f"  [!] Failed removing {path}: {e}")
        if all_removed and not dry_run:
            reset_ids.append((reel_id,))

    if reset_ids:
        with get_db() as conn:
            conn.executemany(
                "UPDATE reels SET is_downloaded = 0, local_path = NULL WHERE reel_id = ?",
                reset_ids
            )

    stale_count, stale_bytes = sweep_stale_partials(dry_run=dry_run, skip=handled)
    deleted_count += stale_count
    reclaimed_bytes += stale_bytes

    if dry_run:
        print("[*] Dry run complete.")
    else:
        print(f"[✓] Cleanup complete: Removed {deleted_count} file(s), reclaimed {reclaimed_bytes / 1024 / 1024:.1f} MB.")

def sweep_stale_partials(dry_run=False, skip=()):
    """Remove abandoned download leftovers that haven't been written to for STALE_PARTIAL_DAYS days."""
    cutoff = time.time() - STALE_PARTIAL_DAYS * 86400
    count = freed = 0
    try:
        names = os.listdir(REELS_DIR)
    except OSError:
        return 0, 0
    for name in names:
        if not (name.endswith(('.part', '.ytdl')) or '.fdash-' in name or '.temp.' in name):
            continue
        path = os.path.join(REELS_DIR, name)
        if path in skip:
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path) or st.st_mtime > cutoff:
            continue
        if dry_run:
            print(f"  [DRY-RUN] Would delete leftover {path} ({st.st_size / 1024 / 1024:.1f} MB)")
            continue
        try:
            os.remove(path)
            print(f"  [✓] Removed leftover: {path} ({st.st_size / 1024 / 1024:.1f} MB)")
            count += 1
            freed += st.st_size
        except OSError as e:
            print(f"  [!] Failed removing {path}: {e}")
    return count, freed

def main():
    parser = argparse.ArgumentParser(description="wa-notify Video Cache Retention Cleaner")
    parser.add_argument("--days", type=int, default=DEFAULT_RETENTION_DAYS, help="Delete opened videos older than N days (default 7)")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without deleting files")
    args = parser.parse_args()

    cleanup(retention_days=args.days, dry_run=args.dry_run)

if __name__ == '__main__':
    main()
