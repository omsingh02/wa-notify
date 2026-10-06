"""Start the real wa-reel-alert.py in the sandbox and watch it alert, queue and fetch (fake yt-dlp)."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest


@pytest.mark.usefixtures("imgserver")
def test_daemon_alerts_queues_and_fetches(tools, sandbox):
    now = int(time.time() * 1000)
    rows = [  # (reel_id, sender, live, alerted, opened, first_seen)
        ("FRIENDLIVE01", "Friend", 1, 0, 0, now),
        ("MYOWNLIVE001", "You", 1, 0, 0, now - 1000),
        ("HISTORIC0001", "Friend", 0, 0, 0, now - 5000),
        ("BACKLOG00001", "Friend", 1, 1, 0, now - 2 * 3600 * 1000),
        ("BACKLOG00002", "Friend", 1, 1, 1, now - 2 * 3600 * 1000),
    ]
    with tools.walib.get_db() as c:
        for rid, sender, live, alerted, opened, first_seen in rows:
            c.execute(
                "insert into reels(reel_id,sender,url,timestamp,first_seen_at,is_likely_live,alerted,is_opened) values(?,?,?,?,?,?,?,?)",
                (
                    rid,
                    sender,
                    f"https://www.instagram.com/reel/{rid}",
                    first_seen // 1000,
                    first_seen,
                    live,
                    alerted,
                    opened,
                ),
            )
    sandbox.set_mode("ok")
    sandbox.reset_log()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GEMINI")}
    proc = subprocess.Popen(
        [sys.executable, str(sandbox.tools_dir / "wa-reel-alert.py"), "--download"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    reels_dir = tools.walib.REELS_DIR
    deadline = time.time() + 30
    try:
        while time.time() < deadline:
            time.sleep(0.5)
            if all(os.path.exists(os.path.join(reels_dir, f"{r}.mp4")) for r in ("FRIENDLIVE01", "BACKLOG00001")):
                break
    finally:
        proc.terminate()
        output = proc.communicate(timeout=10)[0]

    fetched = [c["argv"][-1].rsplit("/", 1)[-1] for c in sandbox.calls("yt-dlp")]
    assert sorted(fetched) == ["BACKLOG00001", "FRIENDLIVE01"], f"fetched {fetched}\n{output}"
    assert fetched[0] == "FRIENDLIVE01", "newest reel first"
    assert "LIVE REEL DETECTED" in output and "Archived historical reel without alert: HISTORIC0001" in output
    assert "Queued" in output and "MYOWNLIVE001" not in fetched  # your own reel is not prefetched
    with tools.walib.get_db() as c:
        alerted = dict(c.execute("select reel_id, alerted from reels").fetchall())
    assert all(alerted[r] == 1 for r in ("FRIENDLIVE01", "MYOWNLIVE001", "HISTORIC0001"))
