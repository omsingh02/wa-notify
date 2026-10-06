"""Prefetcher scheduling with a fake clock and a fake fetcher."""

from __future__ import annotations


def test_prefetcher_scheduling_and_network_pauses(tools, sandbox, check):
    walib, dl, auth, q = tools.walib, tools.dl, tools.auth, tools.queue

    print("--- Prefetcher scheduling (fake clock, fake fetcher) ---")

    class R:
        def __init__(s, ok=False, reason=None, detail="", kind="video"):
            s.ok, s.reason, s.detail, s.kind, s.paths = ok, reason, detail, kind, ["/x"]

    def mkq(script, **kw):
        t = [1000.0]
        calls = []
        ok = []
        logs = []

        def fetch(url, rid):
            calls.append((rid, t[0]))
            r = script.get(rid, [R(True)])
            return r.pop(0) if len(r) > 1 else r[0]

        p = q.Prefetcher(
            fetch,
            on_success=lambda rid, r: ok.append(rid),
            clock=lambda: t[0],
            rng=lambda a, b: 5.0,
            log=logs.append,
            **kw,
        )
        return p, t, calls, ok, logs

    p, t, calls, ok, logs = mkq({})
    p.submit("old", "u", 900.0)
    p.submit("new", "u", 990.0)
    p.submit("mid", "u", 950.0)
    w = p.step()
    check("runs the NEWEST reel first", calls[0][0] == "new")
    check("pauses ~pace between fetches (returns the gap)", w == 5.0)
    check("no second fetch before the pace gap elapses", (p.step(t[0] + 1) or 0) > 0 and len(calls) == 1)
    t[0] += 6
    p.step()
    t[0] += 6
    p.step()
    check(
        "drains oldest last, then idles (step -> None)",
        [c[0] for c in calls] == ["new", "mid", "old"] and p.step() is None,
    )
    check("on_success fired for each success", ok == ["new", "mid", "old"])

    p, t, calls, ok, logs = mkq({"a": [R(False, "blocked", "login"), R(True)], "b": [R(True)]})
    p.submit("a", "u", 990.0)
    p.submit("b", "u", 980.0)
    p.step()
    check(
        "blocked -> whole queue pauses 15 min (b NOT tried)",
        len(calls) == 1 and p.status()["paused_for"] == 900 - 0 or p.status()["paused_for"] > 890,
        str(p.status()),
    )
    t[0] += 100
    w = p.step()
    check("still paused after 100 s, reports remaining", len(calls) == 1 and 790 < w < 810, str(w))
    t[0] += 900
    p.step()
    t[0] += 6
    p.step()
    check("after the pause it resumes: a succeeds, then b", ok == ["a", "b"], f"{calls} {ok}")

    p, t, calls, ok, logs = mkq(
        {
            "a": [
                R(False, "blocked"),
                R(False, "blocked"),
                R(False, "blocked"),
                R(False, "blocked"),
                R(False, "blocked"),
                R(True),
            ]
        }
    )
    p.submit("a", "u", 999.0)
    waits = []
    for _ in range(5):
        p.step()
        waits.append(p.status()["paused_for"])
        t[0] += waits[-1] + 1
    check("cool-down ladder 15 -> 30 -> 60 -> 120 -> 120 min", waits == [900, 1800, 3600, 7200, 7200], str(waits))
    p.step()
    check("success resets the strike counter", p.status()["blocked_strikes"] == 0 and ok == ["a"])

    p, t, calls, ok, logs = mkq({"a": [R(False, "no_video", "photo")]})
    p.submit("a", "u", 999.0)
    p.step()
    check("no_video is dropped, never retried", p.pending() == [] and len(calls) == 1)
    p, t, calls, ok, logs = mkq({"a": [R(False, "timeout", "t")] * 9})
    p.submit("a", "u", 999.0)
    tries = 0
    for _ in range(40):
        w = p.step()
        if w is None:
            break
        t[0] += (w if w else 1) + 1
    check(
        "transient failure: 1 try + 4 backoff retries (5 min..3 h) then gives up",
        len(calls) == 5 and p.pending() == [],
        f"{len(calls)} calls",
    )
    p, t, calls, ok, logs = mkq({"a": [R(False, "unavailable", "private")] * 5})
    p.submit("a", "u", 999.0)
    for _ in range(10):
        w = p.step()
        if w is None:
            break
        t[0] += (w if w else 1) + 1
    check("private/unavailable: one retry an hour later, then gives up", len(calls) == 2 and p.pending() == [])
    p, t, calls, ok, logs = mkq({})
    p.submit("a", "u", 0.0)
    t[0] = 1000 + 25 * 3600
    check("reels older than 24 h are dropped, not fetched", p.step() is None and calls == [] and p.pending() == [])
    p, t, calls, ok, logs = mkq({})
    check("submit twice is ignored", p.submit("a", "u") and not p.submit("a", "u"))

    def boom(url, rid):
        raise RuntimeError("bug")

    p = q.Prefetcher(boom, clock=lambda: 1000.0, rng=lambda a, b: 1.0, log=lambda m: None)
    p.submit("a", "u", 999.0)
    try:
        p.step()
        check("fetcher exception does not kill the worker", True)
    except Exception as e:
        check("fetcher exception does not kill the worker", False, str(e))

    print("--- Prefetcher: network trouble pauses briefly and does NOT use up attempts ---")
    p, t, calls, ok, logs = mkq({"a": [R(False, "network", "Resolving timed out")] * 6 + [R(True)]})
    p.submit("a", "u", 999.0)
    pauses = []
    for _ in range(7):
        p.step()
        pauses.append(p.status()["paused_for"])
        t[0] += pauses[-1] + 1
    check("network pause ladder 1 -> 2 -> 5 -> 10 -> 10 min", pauses[:5] == [60, 120, 300, 600, 600], str(pauses))
    check(
        "6 network failures in a row, and the reel still gets fetched afterwards (no attempt consumed)",
        ok == ["a"] and len(calls) == 7,
        f"{ok} {len(calls)} calls",
    )
    check("success resets the network strikes", p.status()["network_strikes"] == 0)
    check(
        "the log says why it paused",
        any("Can't reach Instagram" in line and "Resolving timed out" in line for line in logs),
        str(logs[:2]),
    )
    p, t, calls, ok, logs = mkq({"a": [R(False, "network")]})
    p.submit("a", "u", 999.0)
    p._cooldown_until = t[0] + 900
    job = p._jobs["a"]
    p._handle(job, R(False, "network"), t[0])
    check("a network pause never SHORTENS an Instagram block cool-down", p._cooldown_until == t[0] + 900)

    check.assert_all()
