"""`wa-reel-dl` and `wa-reel-summary` (wa_reel_ai.py): --help, usage on missing input, flag order."""

from __future__ import annotations

import subprocess
import sys


def run(sandbox, script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(sandbox.tools_dir / script), *args], capture_output=True, text=True, timeout=60
    )


def test_download_cli(sandbox, tools, check):
    sandbox.set_mode("ok")
    sandbox.reset_log()

    r = run(sandbox, "wa_reel_dl.py", "--help")
    check("dl --help: exit 0", r.returncode == 0, r.returncode)
    check(
        "dl --help: documents the flag and the argument",
        "--summary" in r.stdout and "reel_id_or_url" in r.stdout,
        r.stdout,
    )
    check(
        "dl --help: nothing is downloaded (the old code treated '--help' as a reel id)", sandbox.calls("yt-dlp") == []
    )

    r = run(sandbox, "wa_reel_dl.py")
    check(
        "dl without arguments: usage and exit 1 (unchanged)",
        r.returncode == 1 and "usage" in (r.stdout + r.stderr).lower(),
        r.stderr,
    )

    r = run(sandbox, "wa_reel_dl.py", "--summary")
    check(
        "dl with only a flag: usage and exit 1, flag is not mistaken for a reel",
        r.returncode == 1 and sandbox.calls("yt-dlp") == [],
        r.stderr,
    )

    r = run(sandbox, "wa_reel_dl.py", "--summary", "CLIFLAGORD1")
    called = sandbox.calls("yt-dlp")
    check(
        "dl: flag before the reel id works",
        r.returncode == 0 and "Successfully fetched video" in r.stdout,
        r.stdout + r.stderr,
    )
    check(
        "dl: yt-dlp was asked for the reel, not for '--summary'",
        len(called) == 1 and called[0]["argv"][-1].endswith("CLIFLAGORD1"),
        called,
    )
    check.assert_all()


def test_summary_cli(sandbox, tools, check):
    sandbox.reset_log()

    r = run(sandbox, "wa_reel_ai.py", "--help")
    check("ai --help: exit 0", r.returncode == 0, r.returncode)
    check("ai --help: documents --force", "--force" in r.stdout and "reel_id_or_url" in r.stdout, r.stdout)
    check(
        "ai --help: no 'No video file found for reel ID --help'",
        "No video file found" not in r.stdout + r.stderr,
        r.stderr,
    )

    r = run(sandbox, "wa_reel_ai.py")
    check(
        "ai without arguments: usage and exit 1 (unchanged)",
        r.returncode == 1 and "usage" in (r.stdout + r.stderr).lower(),
        r.stderr,
    )

    r = run(sandbox, "wa_reel_ai.py", "--force")
    check(
        "ai with only a flag: usage and exit 1",
        r.returncode == 1 and "usage" in (r.stdout + r.stderr).lower(),
        r.stderr,
    )
    check.assert_all()
