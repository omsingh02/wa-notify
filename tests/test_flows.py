"""
End-to-end flows through fetch_media / play_in_mpv with fake external tools and a local image server.

All tests in this module share one sandbox (DB + cached files) and rely on running in file order.
"""

from __future__ import annotations

import contextlib
import glob
import io
import json
import os
import subprocess
import sys
import threading
import time

import pytest


@pytest.fixture(scope="module")
def H(sandbox, imgserver, tools):
    """Helpers bound to the sandbox."""
    walib = tools.walib

    class Helpers:
        def mode(self, m):
            sandbox.set_mode(m)

        def reset(self):
            sandbox.reset_log()

        def calls(self, tool=None):
            return sandbox.calls(tool)

        def quiet(self, fn, *a, **k):
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()) as o, contextlib.redirect_stderr(err):
                r = fn(*a, **k)
            return r, o.getvalue(), err.getvalue()

        def add(self, rid, kind="reel"):
            with walib.get_db() as c:
                c.execute(
                    "insert or replace into reels(reel_id,sender,url,first_seen_at,is_likely_live) values(?,?,?,?,1)",
                    (
                        rid,
                        "Friend",
                        f"https://www.instagram.com/{'p' if kind == 'p' else 'reel'}/{rid}",
                        int(time.time() * 1000),
                    ),
                )

        def row(self, rid):
            with walib.get_db() as c:
                return dict(
                    c.execute("select is_opened,is_downloaded,local_path from reels where reel_id=?", (rid,)).fetchone()
                )

    return Helpers()


@pytest.mark.usefixtures("imgserver")
def test_fetch_video(tools, sandbox, check, H):
    walib, dl, auth, q = tools.walib, tools.dl, tools.auth, tools.queue
    SB = str(sandbox.home)
    T = str(sandbox.tools_dir)
    walib = tools.walib
    R = walib.REELS_DIR
    mode, reset, calls, quiet, add, row = H.mode, H.reset, H.calls, H.quiet, H.add, H.row
    os.makedirs(R, exist_ok=True)
    PORT = int(os.environ["IMG_PORT"])
    env = dict(os.environ)

    print("--- fetch_media: video ---")
    add("VIDEOANON01")
    mode("ok")
    reset()
    r, o, e = quiet(dl.fetch_media, "https://www.instagram.com/reel/VIDEOANON01", timeout=20)
    c = calls("yt-dlp")
    check("anonymous download works", r.ok and r.kind == "video" and os.path.getsize(r.path) == 20000)
    check(
        "yt-dlp args: --no-playlist, -o <dir>/<id>.%(ext)s, url last, no --cookies",
        "--no-playlist" in c[0]["argv"]
        and c[0]["argv"][-1].endswith("VIDEOANON01")
        and not c[0]["cookies"]
        and f"{R}/VIDEOANON01.%(ext)s" in c[0]["argv"],
    )
    check(
        "DB: is_downloaded=1 and local_path recorded",
        row("VIDEOANON01")["is_downloaded"] == 1 and row("VIDEOANON01")["local_path"] == r.path,
    )
    reset()
    r2, _, _ = quiet(dl.fetch_media, "VIDEOANON01")
    check("already cached -> no yt-dlp call at all", r2.ok and calls("yt-dlp") == [])
    add("LEGACY00001")
    reset()
    got = []
    ok, p = quiet(dl.download_reel, "LEGACY00001", 20, True, got.append)[0]
    check(
        "legacy download_reel(): (True, path) and on_complete(reel_id) called once", ok and p and got == ["LEGACY00001"]
    )

    check.assert_all()


@pytest.mark.usefixtures("imgserver")
def test_fetch_with_session(tools, sandbox, check, H, monkeypatch):
    walib, dl, auth, q = tools.walib, tools.dl, tools.auth, tools.queue
    SB = str(sandbox.home)
    T = str(sandbox.tools_dir)
    walib = tools.walib
    R = walib.REELS_DIR
    mode, reset, calls, quiet, add, row = H.mode, H.reset, H.calls, H.quiet, H.add, H.row
    os.makedirs(R, exist_ok=True)
    PORT = int(os.environ["IMG_PORT"])
    env = dict(os.environ)
    pytest.importorskip("yt_dlp")
    import yt_dlp.cookies as _ytc

    monkeypatch.setattr(_ytc, "extract_cookies_from_browser", _ytc.extract_cookies_from_browser)

    print("--- with an Instagram session (browser read faked at the yt-dlp level) ---")
    os.makedirs(SB + "/.config/wa-notify", exist_ok=True)
    open(SB + "/.config/wa-notify/config.toml", "w").write(
        '[instagram]\nbrowser = "brave"\nprofile = "/x"\nkeyring = "GNOMEKEYRING"\nrefresh_hours = 6\n'
    )
    import http.cookiejar as cj

    import yt_dlp.cookies as ytc
    from yt_dlp.cookies import YoutubeDLCookieJar

    state = {"session": "SECRETSESSIONVALUE", "reads": 0, "on_read": None}

    def fake_extract(browser, profile=None, logger=None, **kw):
        state["reads"] += 1
        if state["on_read"]:
            state["on_read"]()
        jar = YoutubeDLCookieJar()
        exp = int(time.time()) + 86400 * 90
        jar.set_cookie(
            cj.Cookie(
                0,
                "sessionid",
                state["session"],
                None,
                False,
                ".instagram.com",
                True,
                True,
                "/",
                True,
                True,
                exp,
                False,
                None,
                None,
                {"HttpOnly": None},
            )
        )
        return jar

    ytc.extract_cookies_from_browser = fake_extract
    add("VIDEOAUTH01")
    mode("ok")
    reset()
    r, o, e = quiet(dl.fetch_media, "VIDEOAUTH01", timeout=20)
    c = calls("yt-dlp")
    check(
        "session: yt-dlp gets --cookies <private copy>",
        r.ok and c[0]["cookies"] and c[0]["cookie_info"]["mode"] == "0o600" and c[0]["cookie_info"]["has_session"],
    )
    check(
        "session: the copy handed to yt-dlp is deleted afterwards; no cookie values in argv",
        not glob.glob(SB + "/.config/wa-notify/.ytdlp-cookies.*") and "SECRETSESSIONVALUE" not in json.dumps(c),
    )
    add("BLOCKED0001")
    mode("blocked")
    reset()
    state["reads"] = 0
    r, o, e = quiet(dl.fetch_media, "BLOCKED0001", timeout=20)
    check(
        "blocked with a session whose browser copy is unchanged: reason=blocked, NO pointless retry",
        not r.ok and r.reason == "blocked" and len(calls("yt-dlp")) == 1,
        f"{len(calls('yt-dlp'))} calls",
    )
    check(
        "failure line keeps the old log format", "[!] yt-dlp download failed (BLOCKED0001): ERROR: [Instagram]" in e, e
    )
    add("BLOCKED0002")
    mode("blocked")
    reset()
    old = time.time() - 600
    os.utime(auth.COOKIE_CACHE, (old, old))

    def newer():
        state["session"] = "NEWERSESSION"
        mode("ok")

    state["on_read"] = newer
    r, o, e = quiet(dl.fetch_media, "BLOCKED0002", timeout=20)
    check(
        "blocked, but the browser has a newer session: re-read it and retry ONCE -> success",
        r.ok and len(calls("yt-dlp")) == 2 and calls("yt-dlp")[1]["mode"] == "ok",
        f"{len(calls('yt-dlp'))} calls ok={r.ok} {r.reason}",
    )
    state["on_read"] = None

    check.assert_all()


@pytest.mark.usefixtures("imgserver")
def test_photo_posts_and_carousels(tools, sandbox, check, H):
    walib, dl, auth, q = tools.walib, tools.dl, tools.auth, tools.queue
    SB = str(sandbox.home)
    T = str(sandbox.tools_dir)
    walib = tools.walib
    R = walib.REELS_DIR
    mode, reset, calls, quiet, add, row = H.mode, H.reset, H.calls, H.quiet, H.add, H.row
    os.makedirs(R, exist_ok=True)
    PORT = int(os.environ["IMG_PORT"])
    env = dict(os.environ)

    print("--- fetch_media: photo posts and carousels ---")
    for kind, rid, m, n in (
        ("single photo", "PHOTO000001", "photo", 1),
        ('carousel ("No video formats found!")', "CAROUSEL001", "carousel", 3),
    ):
        add(rid, "p")
        mode(m)
        reset()
        r, o, e = quiet(dl.fetch_media, f"https://www.instagram.com/p/{rid}", timeout=20)
        c = calls("yt-dlp")
        check(
            f"{kind}: falls through video -> image route ({n} image(s))",
            r.ok and r.kind == "images" and len(r.paths) == n,
            f"{r.reason} {r.detail}",
        )
        check(f"{kind}: picks the LARGEST version of each image", all(os.path.getsize(p) == 3000 for p in r.paths))
        check(
            f"{kind}: files are <id>_1.jpg.. in order, no leftovers",
            [os.path.basename(p) for p in r.paths] == [f"{rid}_{i}.jpg" for i in range(1, n + 1)]
            and not glob.glob(f"{R}/.{rid}*"),
        )
        check(
            f"{kind}: second yt-dlp call is the metadata listing with --ignore-no-formats-error",
            c[1]["json"] and "--ignore-no-formats-error" in c[1]["argv"],
        )
        check(f"{kind}: DB local_path = first image", row(rid)["local_path"] == r.paths[0])
        check(
            f'{kind}: success is NOT logged as a failure (no "download failed" line)',
            "download failed" not in e and "could not list" not in e,
            e,
        )
    add("CAROUSELBAD", "p")
    mode("carousel_bad")
    reset()
    r, o, e = quiet(dl.fetch_media, "https://www.instagram.com/p/CAROUSELBAD", timeout=20)
    check(
        "carousel where image 2 fails: nothing half-saved (no _1.jpg, no .part)",
        not r.ok and not glob.glob(f"{R}/*CAROUSELBAD*") and not glob.glob(f"{R}/.*CAROUSELBAD*"),
        str(glob.glob(f"{R}/*CAROUSELBAD*")),
    )
    add("PHOTOEMPTY0", "p")
    mode("photo_empty")
    reset()
    r, o, e = quiet(dl.fetch_media, "https://www.instagram.com/p/PHOTOEMPTY0", timeout=20)
    check(
        "photo post with nothing to fetch: fails with reason=no_video AND says so in the log",
        not r.ok and r.reason == "no_video" and "no downloadable video or images found in PHOTOEMPTY0" in e,
        e,
    )
    add("PHOTOWANT00", "p")
    mode("photo")
    reset()
    (ok, p), o_, e_ = quiet(dl.download_reel, "https://www.instagram.com/p/PHOTOWANT00")
    check(
        "download_reel() (video only) does not take the image route",
        ok is False and not any(c["json"] for c in calls("yt-dlp")),
    )
    check(
        "download_reel() (video only) keeps the legacy failure line",
        "[!] yt-dlp download failed (PHOTOWANT00): ERROR" in e_,
        e_,
    )
    add("PRIVATE0001")
    mode("private")
    reset()
    r, _, _ = quiet(dl.fetch_media, "PRIVATE0001", timeout=20)
    check("private -> reason=unavailable", r.reason == "unavailable")

    check.assert_all()


@pytest.mark.usefixtures("imgserver")
def test_network_trouble(tools, sandbox, check, H):
    walib, dl, auth, q = tools.walib, tools.dl, tools.auth, tools.queue
    SB = str(sandbox.home)
    T = str(sandbox.tools_dir)
    walib = tools.walib
    R = walib.REELS_DIR
    mode, reset, calls, quiet, add, row = H.mode, H.reset, H.calls, H.quiet, H.add, H.row
    os.makedirs(R, exist_ok=True)
    PORT = int(os.environ["IMG_PORT"])
    env = dict(os.environ)

    print("--- network trouble ---")
    add("NETDOWN0001")
    mode("netdown")
    reset()
    r, o, e = quiet(dl.fetch_media, "NETDOWN0001", timeout=20)
    check(
        "yt-dlp DNS timeout -> reason=network (not a generic error)", not r.ok and r.reason == "network", f"{r.reason}"
    )
    add("PHOTODEAD01", "p")
    mode("photo_dead")
    reset()
    r, o, e = quiet(dl.fetch_media, "https://www.instagram.com/p/PHOTODEAD01", timeout=20)
    check(
        "photo post whose image server is unreachable -> reason=network, nothing left behind",
        not r.ok
        and r.reason == "network"
        and not glob.glob(f"{R}/*PHOTODEAD01*")
        and not glob.glob(f"{R}/.*PHOTODEAD01*"),
        f"{r.reason} {r.detail}",
    )
    add("SOCKT000001")
    mode("ok")
    reset()
    quiet(dl.fetch_media, "SOCKT000001", timeout=20)
    check(
        "default yt-dlp --socket-timeout is 30 (rides out a 20 s DNS stall)",
        calls("yt-dlp")[0]["argv"][calls("yt-dlp")[0]["argv"].index("--socket-timeout") + 1] == "30",
    )
    add("SOCKT000002")
    reset()
    quiet(dl.fetch_media, "SOCKT000002", timeout=20, socket_timeout=20)
    check(
        "an explicit value is passed through (20)",
        calls("yt-dlp")[0]["argv"][calls("yt-dlp")[0]["argv"].index("--socket-timeout") + 1] == "20",
    )
    add("RETRY000001")
    mode("netdown,ok")
    reset()
    r, o, e = quiet(dl.fetch_media, "RETRY000001", timeout=20, interactive=True)
    check(
        "click + one DNS blip: retried once and succeeds",
        r.ok and len(calls("yt-dlp")) == 2 and "trying once more" in e,
        f"{r.reason} calls={len(calls('yt-dlp'))}",
    )
    add("RETRY000002")
    mode("netdown,ok")
    reset()
    r, o, e = quiet(dl.fetch_media, "RETRY000002", timeout=20, interactive=False)
    check(
        "background fetch: NOT retried inline (the queue paces retries)",
        (not r.ok) and r.reason == "network" and len(calls("yt-dlp")) == 1,
        f"{r.reason} calls={len(calls('yt-dlp'))}",
    )
    add("RETRY000003")
    mode("netdown")
    reset()
    r, o, e = quiet(dl.fetch_media, "RETRY000003", timeout=20, interactive=True)
    check(
        "click + outage: exactly two attempts, then reason=network (no loop)",
        (not r.ok) and r.reason == "network" and len(calls("yt-dlp")) == 2,
        f"{r.reason} calls={len(calls('yt-dlp'))}",
    )
    add("RETRY000004", "p")
    mode("photo_dead,photo")
    reset()
    r, o, e = quiet(dl.fetch_media, "https://www.instagram.com/p/RETRY000004", timeout=20, interactive=True)
    check(
        "photo post + image-server blip: retried and shows the images",
        r.ok and r.kind == "images",
        f"{r.reason} {r.detail}",
    )

    check.assert_all()


@pytest.mark.usefixtures("imgserver")
def test_timeout_kills_process_group(tools, sandbox, check, H):
    walib, dl, auth, q = tools.walib, tools.dl, tools.auth, tools.queue
    SB = str(sandbox.home)
    T = str(sandbox.tools_dir)
    walib = tools.walib
    R = walib.REELS_DIR
    mode, reset, calls, quiet, add, row = H.mode, H.reset, H.calls, H.quiet, H.add, H.row
    os.makedirs(R, exist_ok=True)
    PORT = int(os.environ["IMG_PORT"])
    env = dict(os.environ)

    print("--- timeout kills the whole process group ---")
    add("HANG0000001")
    mode("hang")
    reset()
    t0 = time.time()
    r, o, e = quiet(dl.fetch_media, "HANG0000001", timeout=1.5)
    time.sleep(0.3)
    stub = str(sandbox.bin_dir / "yt-dlp")

    def stub_processes() -> list[str]:
        """Live processes that are the fake yt-dlp (interpreter + stub path at the start of the command line)."""
        rows = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout.splitlines()
        return [
            row.strip()
            for row in rows
            if f" {stub} " in " " + (row.split(None, 1)[1] if len(row.split(None, 1)) > 1 else "") + " "
            and row.split(None, 2)[1].endswith("python3")
        ]

    # positive control: the detector must be able to see a live stub, otherwise "none left" proves nothing
    probe = subprocess.Popen([stub, "--probe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    detector_works = bool(stub_processes())
    probe.kill()
    probe.wait()
    check("the process detector can see a running fake yt-dlp (positive control)", detector_works)
    leftover = stub_processes()
    check(
        'timeout after ~1.5 s -> reason=timeout, legacy "timed out" line',
        not r.ok and r.reason == "timeout" and 1.3 < time.time() - t0 < 3 and "timed out (HANG0000001)" in e,
    )
    check("no yt-dlp process left running", leftover == [], str(leftover))

    check.assert_all()


@pytest.mark.usefixtures("imgserver")
def test_click_during_prefetch_waits(tools, sandbox, check, H):
    walib, dl, auth, q = tools.walib, tools.dl, tools.auth, tools.queue
    SB = str(sandbox.home)
    T = str(sandbox.tools_dir)
    walib = tools.walib
    R = walib.REELS_DIR
    mode, reset, calls, quiet, add, row = H.mode, H.reset, H.calls, H.quiet, H.add, H.row
    os.makedirs(R, exist_ok=True)
    PORT = int(os.environ["IMG_PORT"])
    env = dict(os.environ)

    print("--- concurrency: a click during a prefetch must wait for it, not race it ---")
    add("RACE0000001")
    mode("ok")
    reset()
    os.environ["FAKE_DELAY"] = "2"
    out = {}

    def prefetch():
        out["pre"] = dl.fetch_media("RACE0000001", timeout=20)

    th = threading.Thread(target=prefetch)
    th.start()
    time.sleep(0.5)
    t0 = time.time()
    out["click"] = dl.fetch_media("RACE0000001", timeout=20, interactive=True)
    waited = time.time() - t0
    th.join()
    check("both callers succeed", out["pre"].ok and out["click"].ok)
    check(
        "EXACTLY ONE yt-dlp ran (no duplicate download of the same reel)",
        len(calls("yt-dlp")) == 1,
        f"{len(calls('yt-dlp'))}",
    )
    check("the click waited for the in-flight download (~1.5 s)", 1.0 < waited < 3.0, f"{waited:.1f}s")
    os.environ["FAKE_DELAY"] = "0"

    check.assert_all()


@pytest.mark.usefixtures("imgserver")
def test_play_in_mpv_launches(tools, sandbox, check, H):
    walib, dl, auth, q = tools.walib, tools.dl, tools.auth, tools.queue
    SB = str(sandbox.home)
    T = str(sandbox.tools_dir)
    walib = tools.walib
    R = walib.REELS_DIR
    mode, reset, calls, quiet, add, row = H.mode, H.reset, H.calls, H.quiet, H.add, H.row
    os.makedirs(R, exist_ok=True)
    PORT = int(os.environ["IMG_PORT"])
    env = dict(os.environ)

    print("--- play_in_mpv: what actually gets launched ---")
    MPV = [
        "mpv",
        "--wayland-app-id=wa-reel",
        "--title=wa-reel",
        "--no-border",
        "--force-window=immediate",
        "--autofit=420x750",
    ]

    def play(rid, url=None):
        time.sleep(0.7)
        reset()
        out = quiet(walib.play_in_mpv, rid, url or f"https://www.instagram.com/reel/{rid}")
        time.sleep(0.7)  # mpv / xdg-open are launched detached; let the stubs finish logging
        return out

    p, o, e = play("VIDEOANON01")
    c = calls()
    mp = [x for x in c if x["tool"] == "mpv"]
    check(
        "cached video: floating-window flags unchanged + --loop-file=inf + the file",
        len(mp) == 1 and mp[0]["argv"] == MPV[1:] + ["--loop-file=inf", f"{R}/VIDEOANON01.mp4"],
        str(mp),
    )
    check(
        "cached video: previous window killed, no fetch, no notification, marked opened",
        any(x["tool"] == "pkill" and x["argv"] == ["-f", "title=wa-reel"] for x in c)
        and not [x for x in c if x["tool"] in ("yt-dlp", "notify-send", "xdg-open")]
        and row("VIDEOANON01")["is_opened"] == 1,
    )
    p, o, e = play("CAROUSEL001", "https://www.instagram.com/p/CAROUSEL001")
    mp = [x for x in calls() if x["tool"] == "mpv"]
    check(
        "cached photo post: image flags + all 3 images in order, no --loop-file",
        mp[0]["argv"]
        == MPV[1:]
        + ["--image-display-duration=inf", "--loop-playlist=inf"]
        + [f"{R}/CAROUSEL001_{i}.jpg" for i in (1, 2, 3)],
        str(mp),
    )
    add("PLAYFAST001")
    mode("ok")
    p, o, e = play("PLAYFAST001")
    c = calls()
    check(
        'uncached + fast fetch: opens in mpv, NO "Fetching" nag, no browser',
        [x["tool"] for x in c].count("mpv") == 1
        and not [x for x in c if x["tool"] in ("notify-send", "xdg-open")]
        and row("PLAYFAST001")["is_opened"] == 1,
    )
    tools = [x["tool"] for x in c]
    check(
        "order: fetch finishes BEFORE the old window is killed and the new one opens",
        tools.index("yt-dlp") < tools.index("pkill") < tools.index("mpv"),
        str(tools),
    )
    add("PLAYSLOW001")
    os.environ["FAKE_DELAY"] = "3"
    p, o, e = play("PLAYSLOW001")
    c = calls()
    os.environ["FAKE_DELAY"] = "0"
    nots = [x for x in c if x["tool"] == "notify-send"]
    check(
        'slow fetch (3 s): exactly one "Fetching reel…" notification, then mpv',
        len(nots) == 1 and "Fetching reel" in nots[0]["argv"][-2] and [x["tool"] for x in c].count("mpv") == 1,
        str(nots),
    )
    add("PLAYPHOTO01", "p")
    mode("photo")
    p, o, e = play("PLAYPHOTO01", "https://www.instagram.com/p/PLAYPHOTO01")
    mp = [x for x in calls() if x["tool"] == "mpv"]
    check(
        "uncached photo post: fetched and shown in mpv (not the browser)",
        len(mp) == 1
        and "--image-display-duration=inf" in mp[0]["argv"]
        and mp[0]["argv"][-1] == f"{R}/PLAYPHOTO01_1.jpg"
        and not [x for x in calls() if x["tool"] == "xdg-open"],
    )
    add("PLAYBLOCK01")
    mode("blocked")
    p, o, e = play("PLAYBLOCK01")
    c = calls()
    xo = [x for x in c if x["tool"] == "xdg-open"]
    nt = [x for x in c if x["tool"] == "notify-send"]
    check(
        "blocked: last resort = browser with the reel URL, mpv NOT launched",
        len(xo) == 1
        and xo[0]["argv"] == ["https://www.instagram.com/reel/PLAYBLOCK01"]
        and not [x for x in c if x["tool"] == "mpv"]
        and p is None,
    )
    check(
        "blocked: notification says WHY it opened the browser",
        any(
            "Opening in browser" in x["argv"] and "Instagram is blocking downloads right now" in x["argv"][-1]
            for x in nt
        ),
        str(nt),
    )
    check("blocked: still marked opened", row("PLAYBLOCK01")["is_opened"] == 1)
    check("failure is explained on stdout too", "blocking downloads" in o, o)

    add("PLAYNET0001")
    mode("netdown")
    p, o, e = play("PLAYNET0001")
    c = calls()
    check(
        'click during a DNS outage: browser fallback says "network / DNS problem", not a vague failure',
        any(
            "Couldn't reach Instagram (network / DNS problem)" in x["argv"][-1] for x in c if x["tool"] == "notify-send"
        ),
        str([x for x in c if x["tool"] == "notify-send"]),
    )

    check.assert_all()


@pytest.mark.usefixtures("imgserver")
def test_cli(tools, sandbox, check, H):
    walib, dl, auth, q = tools.walib, tools.dl, tools.auth, tools.queue
    SB = str(sandbox.home)
    T = str(sandbox.tools_dir)
    walib = tools.walib
    R = walib.REELS_DIR
    mode, reset, calls, quiet, add, row = H.mode, H.reset, H.calls, H.quiet, H.add, H.row
    os.makedirs(R, exist_ok=True)
    PORT = int(os.environ["IMG_PORT"])
    env = dict(os.environ)

    print("--- CLI ---")
    add("CLIREEL0001")
    mode("ok")
    env = dict(os.environ)
    r = subprocess.run([sys.executable, T + "/wa_reel_dl.py", "CLIREEL0001"], env=env, capture_output=True, text=True)
    check(
        "wa_reel_dl.py <id>: exit 0 and prints the saved path",
        r.returncode == 0 and "Successfully fetched video" in r.stdout,
        r.stdout + r.stderr,
    )
    add("CLIREEL0002")
    mode("blocked")
    r = subprocess.run([sys.executable, T + "/wa_reel_dl.py", "CLIREEL0002"], env=env, capture_output=True, text=True)
    check(
        "wa_reel_dl.py <id> when blocked: exit 1 with the reason",
        r.returncode == 1 and "(blocked:" in r.stderr,
        r.stderr,
    )

    sys.stdout = sys.__stdout__

    check.assert_all()
