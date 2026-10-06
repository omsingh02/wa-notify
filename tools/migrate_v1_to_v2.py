#!/usr/bin/env python3
"""
migrate_v1_to_v2.py — One-time Migration from flat files to SQLite
Reads reels.jsonl, opened_reels.json, cached summaries, and local mp4s,
inserting them into wa-notify.db with alerted=1, is_likely_live=0 so past reels
do not trigger spurious notification floods.
"""

import os
import sys
import json

TOOLS_DIR = os.path.dirname(os.path.realpath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from walib import (
    get_db, init_db, get_reel_id, get_local_reel_file,
    SUMMARIES_DIR, DATA_DIR
)

REELS_JSONL = os.path.join(DATA_DIR, 'reels.jsonl')
OPENED_JSON = os.path.join(DATA_DIR, 'opened_reels.json')

def migrate():
    init_db()
    conn = get_db()

    opened_ids = set()
    if os.path.exists(OPENED_JSON):
        try:
            with open(OPENED_JSON, 'r', encoding='utf-8') as f:
                opened_ids = set(json.load(f))
        except Exception as e:
            print(f"[!] Error reading {OPENED_JSON}: {e}")

    imported_count = 0
    reels_seen = {}

    if os.path.exists(REELS_JSONL):
        with open(REELS_JSONL, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    url = data.get('url', '')
                    if not url:
                        continue
                    reel_id = get_reel_id(url)
                    reels_seen[reel_id] = data
                except Exception:
                    continue

    for reel_id, data in reels_seen.items():
        url = data.get('url', f"https://www.instagram.com/reel/{reel_id}")
        sender = data.get('sender', 'Unknown')
        title = data.get('title', '')
        ts = data.get('timestamp') or (data.get('detectedAt', 0) // 1000)
        first_seen = data.get('detectedAt') or (ts * 1000)
        is_opened = 1 if reel_id in opened_ids else 0
        
        # Check local mp4
        local_path = get_local_reel_file(reel_id)
        is_downloaded = 1 if local_path else 0

        # Check existing summary
        summary_text = None
        summary_status = 'pending'
        sum_path = os.path.join(SUMMARIES_DIR, f"{reel_id}.txt")
        if os.path.exists(sum_path):
            try:
                with open(sum_path, 'r', encoding='utf-8') as sf:
                    content = sf.read().strip()
                    if content:
                        summary_text = content
                        summary_status = 'done'
            except Exception:
                pass

        conn.execute("""
            INSERT OR IGNORE INTO reels (
                reel_id, sender, url, title, timestamp, first_seen_at,
                is_likely_live, is_downloaded, local_path,
                is_opened, alerted, summary, summary_status
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 1, ?, ?)
        """, (
            reel_id, sender, url, title, ts, first_seen,
            is_downloaded, local_path, is_opened, summary_text, summary_status
        ))
        imported_count += 1

    conn.commit()

    # Also check if any IDs in opened_reels.json weren't in reels.jsonl
    for oid in opened_ids:
        conn.execute("UPDATE reels SET is_opened = 1 WHERE reel_id = ?", (oid,))
    conn.commit()

    total_reels = conn.execute("SELECT COUNT(*) FROM reels").fetchone()[0]
    total_opened = conn.execute("SELECT COUNT(*) FROM reels WHERE is_opened = 1").fetchone()[0]
    total_summarized = conn.execute("SELECT COUNT(*) FROM reels WHERE summary_status = 'done'").fetchone()[0]
    conn.close()

    print("\n[✓] Migration Complete!")
    print(f"    - Imported: {imported_count} unique reels from {REELS_JSONL}")
    print(f"    - Total in DB: {total_reels}")
    print(f"    - Marked Opened: {total_opened} (from {len(opened_ids)} known opened IDs)")
    print(f"    - With AI Summaries: {total_summarized}")
    print("    - All marked with alerted=1, is_likely_live=0 to prevent notification storms.")

if __name__ == '__main__':
    migrate()
