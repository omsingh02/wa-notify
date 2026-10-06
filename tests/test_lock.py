"""Per-reel lock: a second downloader waits for the first, across processes and across threads."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time

HOLDER = textwrap.dedent(
    """
    import sys, time
    sys.path.insert(0, {tools!r})
    sys.dont_write_bytecode = True
    import wa_reel_dl as dl
    with dl.reel_lock("LOCKTEST", timeout=5) as got:
        print("holder got", got, flush=True)
        time.sleep(2.0)
    """
)


def start_holder(sandbox) -> subprocess.Popen:
    """A separate process that holds the lock on LOCKTEST for ~2 s."""
    proc = subprocess.Popen([sys.executable, "-c", HOLDER.format(tools=str(sandbox.tools_dir))], stdout=subprocess.PIPE, text=True)
    proc.stdout.readline()  # wait until it really holds the lock
    return proc


def test_waiter_blocks_until_the_holder_releases(tools, sandbox):
    holder = start_holder(sandbox)
    t0 = time.time()
    with tools.dl.reel_lock("LOCKTEST", timeout=10) as got:
        waited = time.time() - t0
    holder.wait()
    assert got
    assert 1.3 < waited < 3.5, f"waited {waited:.1f}s"


def test_waiter_gives_up_when_the_holder_outlasts_the_timeout(tools, sandbox):
    holder = start_holder(sandbox)
    with tools.dl.reel_lock("LOCKTEST", timeout=0.6) as got:
        pass
    holder.wait()
    assert got is False


def test_threads_in_one_process_exclude_each_other(tools):
    with tools.dl.reel_lock("LOCKTEST2") as first, tools.dl.reel_lock("LOCKTEST2", timeout=0.3) as second:
        assert first and not second


def test_different_reels_never_block_each_other(tools):
    with tools.dl.reel_lock("LOCK_A") as a, tools.dl.reel_lock("LOCK_B", timeout=0.3) as b:
        assert a and b
