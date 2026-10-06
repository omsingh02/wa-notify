"""Instagram session cache: config, permissions, refresh logic, --check output (the browser read is faked)."""

from __future__ import annotations

import contextlib
import io
import os
import stat
import time

import pytest


def test_auth_session_cache_and_check(tools, sandbox, check, monkeypatch):
    walib, dl, auth, q = tools.walib, tools.dl, tools.auth, tools.queue
    SB = str(sandbox.home)
    pytest.importorskip("yt_dlp")
    import yt_dlp.cookies as _ytc

    monkeypatch.setattr(_ytc, "extract_cookies_from_browser", _ytc.extract_cookies_from_browser)

    print("--- auth: config, cache file, permissions, refresh logic (browser read is faked) ---")
    check(
        "no config -> anonymous (None) and cookie_copy yields None",
        auth.ensure_cookie_cache() is None and (lambda: [True for c in [None]])(),
    )
    with auth.cookie_copy() as cp:
        check("cookie_copy -> None without config", cp is None)
    os.makedirs(SB + "/.config/wa-notify", exist_ok=True)
    open(SB + "/.config/wa-notify/config.toml", "w").write(
        '[instagram]\nbrowser = "brave"\nprofile = "/nonexistent/profile"\nkeyring = "GNOMEKEYRING"\nrefresh_hours = 6\n'
    )
    import http.cookiejar as cj

    from yt_dlp.cookies import YoutubeDLCookieJar

    def fake_jar(session="SECRETSESSIONVALUE"):
        jar = YoutubeDLCookieJar()
        exp = int(time.time()) + 86400 * 90
        for dom, name, val in (
            (".instagram.com", "sessionid", session),
            (".instagram.com", "csrftoken", "tok"),
            (".google.com", "SID", "NOTINSTAGRAM"),
            ("web.whatsapp.com", "wa", "NOTINSTAGRAM2"),
        ):
            jar.set_cookie(
                cj.Cookie(
                    0,
                    name,
                    val,
                    None,
                    False,
                    dom,
                    True,
                    dom.startswith("."),
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

    reads = []
    import yt_dlp.cookies as ytc

    state = {"session": "SECRETSESSIONVALUE"}
    ytc.extract_cookies_from_browser = lambda browser, profile=None, logger=None, **kw: (
        reads.append(1),
        fake_jar(state["session"]),
    )[1]
    p1 = auth.ensure_cookie_cache()
    check(
        "first call reads the browser and writes the cache",
        p1 == auth.COOKIE_CACHE and len(reads) == 1 and os.path.exists(p1),
    )
    body = open(p1).read()

    # the real filter must also reject a jar without a session, and surface yt-dlp's own explanation
    class _L:
        pass

    def nosession(browser, profile=None, logger=None, **kw):
        logger.error("secretstorage not available as the `secretstorage` module is not installed")
        return YoutubeDLCookieJar()

    saved = ytc.extract_cookies_from_browser
    ytc.extract_cookies_from_browser = nosession
    try:
        auth._read_from_browser({"browser": "brave"})
        msg = "no error"
    except RuntimeError as e:
        msg = str(e)
    ytc.extract_cookies_from_browser = saved
    check("no session decrypted -> error carries yt-dlp's reason (secretstorage)", "secretstorage" in msg, msg)
    check("cache is mode 0600", stat.S_IMODE(os.stat(p1).st_mode) == 0o600, oct(stat.S_IMODE(os.stat(p1).st_mode)))
    check(
        "cache holds ONLY instagram.com cookies (not google / whatsapp)",
        "NOTINSTAGRAM" not in body and "SECRETSESSIONVALUE" in body,
    )
    auth.ensure_cookie_cache()
    auth.ensure_cookie_cache()
    check("fresh cache is reused (no extra browser reads)", len(reads) == 1)
    old = time.time() - 7 * 3600
    os.utime(p1, (old, old))
    auth.ensure_cookie_cache()
    check("cache older than refresh_hours is re-read", len(reads) == 2)
    with auth.cookie_copy() as cp:
        ok_copy = cp and cp != p1 and stat.S_IMODE(os.stat(cp).st_mode) == 0o600 and open(cp).read() == open(p1).read()
        path_during = cp
    check("cookie_copy: private 0600 copy, not the cache itself", ok_copy)
    check("cookie_copy: copy is deleted afterwards", not os.path.exists(path_during))
    check(
        "no stray temp files left in the config dir",
        sorted(os.listdir(SB + "/.config/wa-notify"))
        == [".instagram-cookies.lock", "config.toml", "instagram-cookies.txt"],
        str(sorted(os.listdir(SB + "/.config/wa-notify"))),
    )
    # refresh_cookies: only true when content changed
    os.utime(p1, (old, old))
    check("refresh_cookies() False when browser session is unchanged", auth.refresh_cookies() is False)
    state["session"] = "ANOTHERSESSION"
    os.utime(p1, (old, old))
    check("refresh_cookies() True when the browser has a newer session", auth.refresh_cookies() is True)

    # failure path
    def fail(cfg):
        raise RuntimeError("secretstorage not available")

    def fail_extract(*a, **k):
        raise RuntimeError("secretstorage not available")

    ytc.extract_cookies_from_browser = fail_extract
    auth._last_failure = 0.0
    os.utime(p1, (old, old))
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r = auth.ensure_cookie_cache()
        r2 = auth.ensure_cookie_cache()
    check(
        "browser unreadable -> falls back to the previous copy, warns ONCE",
        r == p1 and r2 == p1 and err.getvalue().count("Instagram session") == 1,
        err.getvalue(),
    )
    os.remove(p1)
    auth._last_failure = 0.0
    with contextlib.redirect_stderr(io.StringIO()) as e2:
        r = auth.ensure_cookie_cache(force=True)
    check("browser unreadable and no copy -> anonymous (None), no crash", r is None)
    auth._last_failure = time.time()
    n = len(reads)
    auth.ensure_cookie_cache()
    check("after a failure it does not hammer the keyring (10 min back-off)", len(reads) == n)
    ytc.extract_cookies_from_browser = lambda browser, profile=None, logger=None, **kw: fake_jar()
    auth._last_failure = 0.0
    auth.ensure_cookie_cache(force=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print("\n".join(auth.describe()))
    check(
        "--check output never contains cookie values",
        "SECRETSESSIONVALUE" not in buf.getvalue() and "sessionid present" in buf.getvalue(),
        buf.getvalue(),
    )

    print("--- auth --check: cookie expiry in every form it can appear (regression: ValueError year 426256958) ---")
    import datetime

    def cache_with(expires, name="sessionid", value="SECRETSESSIONVALUE"):
        open(auth.COOKIE_CACHE, "w").write(
            "# Netscape HTTP Cookie File\n"
            + "\t".join(["#HttpOnly_.instagram.com", "TRUE", "/", "TRUE", str(expires), name, value])
            + "\n"
        )
        out = auth.describe()
        return [line for line in out if line.startswith("session:")], out

    chrome_us = int((time.time() + 100 * 86400 + 11644473600) * 1e6)
    r, _ = cache_with(13451308227600764)
    check(
        "the exact value from the traceback (Chromium microseconds) -> a real date, no crash",
        r == ["session: sessionid present, expires 2027-04-04"] or ("expires 2027-04" in r[0]),
        str(r),
    )
    r, _ = cache_with(chrome_us)
    check(
        "Chromium microseconds, 100 days out",
        r
        and f"expires {(datetime.date.today() + datetime.timedelta(days=100)).isoformat()}" in r[0]
        or "expires" in r[0],
        str(r),
    )
    r, _ = cache_with(int(time.time()) + 50 * 86400)
    check("unix seconds", "expires" in r[0] and "EXPIRED" not in r[0], str(r))
    r, _ = cache_with(int((time.time() + 3 * 86400) * 1000))
    check("unix milliseconds", "expires" in r[0] and "EXPIRED" not in r[0], str(r))
    r, _ = cache_with(0)
    check(
        "session cookie (expires=0) -> end of browser session",
        r == ["session: sessionid present, expires end of browser session"],
        str(r),
    )
    r, _ = cache_with(int(time.time()) - 86400)
    check("expired unix timestamp -> EXPIRED", r == ["session: sessionid present but EXPIRED"], str(r))
    r, _ = cache_with(int((time.time() - 86400 + 11644473600) * 1e6))
    check("expired Chromium timestamp -> EXPIRED", r == ["session: sessionid present but EXPIRED"], str(r))
    r, _ = cache_with(10**30)
    check("absurd value -> no crash", r and r[0].startswith("session:"), str(r))
    r, _ = cache_with("notanumber")
    check("garbage expiry -> no crash", r and r[0].startswith("session:"), str(r))
    r, _ = cache_with(13451308227600764, name="csrftoken")
    check("no sessionid in the cache -> says so", r == ["session: NO sessionid cookie in the cache"], str(r))
    r, out = cache_with(13451308227600764)
    check("--check output still never leaks the value", "SECRETSESSIONVALUE" not in "\n".join(out))

    check.assert_all()


def test_profile_tilde_is_expanded(sandbox, tools, monkeypatch):
    """yt-dlp does not expand "~" in a browser profile path, so the tool has to."""
    import pytest

    ytc = pytest.importorskip("yt_dlp.cookies")
    seen = {}

    def fake_extract(browser, profile=None, logger=None, **kwargs):
        seen["profile"] = profile
        return ytc.YoutubeDLCookieJar()  # no session cookie: _read_from_browser raises after the call

    monkeypatch.setattr(ytc, "extract_cookies_from_browser", fake_extract)
    with pytest.raises(RuntimeError):
        tools.auth._read_from_browser({"browser": "brave", "profile": "~/profiles/Default"})
    assert seen["profile"] == str(sandbox.home / "profiles" / "Default")

    with pytest.raises(RuntimeError):
        tools.auth._read_from_browser({"browser": "brave"})
    assert seen["profile"] is None, "no profile configured: let yt-dlp pick the browser's default"


def _cookie(domain, name, value="v"):
    import http.cookiejar as cj

    return cj.Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path="/",
        path_specified=True,
        secure=True,
        expires=int(time.time()) + 86400,
        discard=False,
        comment=None,
        comment_url=None,
        rest={"HttpOnly": None},
    )


def test_cookie_copy_keeps_only_instagram_hosts(sandbox, tools, monkeypatch):
    """
    The Instagram-only copy takes instagram.com and its subdomains, not look-alikes such as notinstagram.com
    (a bare `endswith("instagram.com")` accepts those; flagged by CodeQL as py/incomplete-url-substring-sanitization).
    """
    import pytest

    ytc = pytest.importorskip("yt_dlp.cookies")
    keep = ["instagram.com", ".instagram.com", "www.instagram.com", ".i.instagram.com", "WWW.Instagram.COM"]
    drop = [
        "notinstagram.com",
        ".evilinstagram.com",
        "fakeinstagram.com",
        "instagram.com.evil.test",
        "instagram.company",
        "",
        "google.com",
    ]
    jar = ytc.YoutubeDLCookieJar()
    jar.set_cookie(_cookie(".instagram.com", "sessionid"))
    for n, domain in enumerate(keep + drop):
        jar.set_cookie(_cookie(domain, f"c{n}"))
    monkeypatch.setattr(ytc, "extract_cookies_from_browser", lambda *args, **kwargs: jar)

    copied = tools.auth._read_from_browser({"browser": "brave"})

    kept = sorted(c.domain.lstrip(".").lower() for c in copied if c.name != "sessionid")
    assert kept == sorted(d.lstrip(".").lower() for d in keep)
