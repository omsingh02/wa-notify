"""wa-reel-menu with a fake launcher: marking only what was listed, playing a selection, opening the digest."""

from __future__ import annotations

import os
import sys
import time

import pytest
from conftest import load_script


@pytest.fixture(scope="module")
def menu(tools):
    return load_script("wa-reel-menu.py")


def add(tools, rid, sender="Friend", opened=0):
    with tools.walib.get_db() as c:
        c.execute("insert or replace into reels(reel_id,sender,url,timestamp,first_seen_at,is_opened) values(?,?,?,?,?,?)",
                  (rid, sender, f"https://www.instagram.com/reel/{rid}", int(time.time()), int(time.time() * 1000), opened))


def opened(tools, rid):
    with tools.walib.get_db() as c:
        return c.execute("select is_opened from reels where reel_id=?", (rid,)).fetchone()[0]


def run_menu(menu, sandbox, monkeypatch, choice, *argv, insert=None):
    sandbox.reset_log()
    monkeypatch.setenv("FAKE_CHOICE", choice)
    monkeypatch.setenv("WA_NOTIFY_DATA_DIR", str(sandbox.data_dir))  # for the fake launcher's DB write
    if insert:
        monkeypatch.setenv("FAKE_INSERT_REEL", insert)
    else:
        monkeypatch.delenv("FAKE_INSERT_REEL", raising=False)
    monkeypatch.setattr(sys, "argv", ["wa-reel-menu", *argv])
    menu.main()
    time.sleep(0.4)  # detached stubs log asynchronously


def test_mark_all_marks_only_the_listed_reels(tools, menu, sandbox, monkeypatch):
    for rid in ("MENUA000001", "MENUB000001"):
        add(tools, rid)
    run_menu(menu, sandbox, monkeypatch, "Mark all as opened", insert="LATE0000001")
    assert opened(tools, "MENUA000001") == 1 and opened(tools, "MENUB000001") == 1
    assert opened(tools, "LATE0000001") == 0, "a reel that arrived while the menu was open must stay unopened"
    note = [c for c in sandbox.calls("notify-send")][-1]["argv"]
    assert "Marked 2 reel(s) as opened" in note[-1]


def test_mark_opened_many_is_exact(tools):
    add(tools, "MANY0000001")
    add(tools, "MANY0000002")
    assert tools.walib.mark_opened_many(["MANY0000001", "https://www.instagram.com/reel/MANY0000002/?igsh=x"]) == 2
    assert opened(tools, "MANY0000001") == opened(tools, "MANY0000002") == 1
    assert tools.walib.mark_opened_many([]) == 0


def test_launcher_gets_fuzzel_flags_from_settings(tools, menu, sandbox, monkeypatch):
    add(tools, "FLAGS000001")
    run_menu(menu, sandbox, monkeypatch, "nothing-matches")  # the fake exits 1: just inspect how it was called
    argv = sandbox.calls("fuzzel")[0]["argv"]
    assert argv[:3] == ["--dmenu", "--index", "-p"]
    ui = tools.walib.UI
    assert ui.launcher_font in argv and str(ui.launcher_width) in argv and "--match-mode=fzf" in argv


def test_selecting_a_reel_plays_it_and_marks_it(tools, menu, sandbox, monkeypatch):
    rid = "PLAY0000001"
    add(tools, rid, sender="Zed")
    os.makedirs(tools.walib.REELS_DIR, exist_ok=True)
    open(os.path.join(tools.walib.REELS_DIR, f"{rid}.mp4"), "wb").write(b"0" * 20000)
    run_menu(menu, sandbox, monkeypatch, "Zed")
    mpv = sandbox.calls("mpv")
    assert len(mpv) == 1 and mpv[0]["argv"][-1].endswith(f"{rid}.mp4")
    assert opened(tools, rid) == 1


def test_digest_opens_in_the_configured_terminal(tools, menu, sandbox, monkeypatch):
    add(tools, "DIGEST00001")
    run_menu(menu, sandbox, monkeypatch, "Open full digest")
    term = sandbox.calls("footclient")
    assert len(term) == 1
    assert term[0]["argv"][:2] == ["-a", "wa-reel-digest"] and term[0]["argv"][-1].endswith("wa-reel-digest.py")


def test_history_flag_lists_opened_reels(tools, menu, sandbox, monkeypatch):
    add(tools, "HIST0000001", sender="Hist", opened=1)
    run_menu(menu, sandbox, monkeypatch, "nothing-matches", "--history")
    lines = sandbox.calls("fuzzel")[0]["lines"]
    assert any(line.startswith("Hist") for line in lines) and "View unopened reels only" in lines
