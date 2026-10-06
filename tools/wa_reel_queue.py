#!/usr/bin/env python3
"""
wa_reel_queue.py — polite background fetch queue for the reel alert daemon (wa-notify v2)

One worker thread fetches reel media one at a time, with a pause between requests, so Instagram
sees something closer to a person scrolling than a scraper:

  - blocked (login redirect / rate-limit): the whole queue pauses with a growing cool-down instead
    of hammering the site; the same reels are tried again afterwards
  - transient failures (timeout, empty answer, ...): retried later with backoff, a few times
  - photo posts that can't be fetched, or private/removed posts: not retried (or only once)
  - newest reel first, because that's the one you're most likely to open next

The fetch itself (yt-dlp, cookies, per-reel lock) lives in wa_reel_dl.fetch_media; this module only
decides *when* to call it.
"""

import random
import threading
import time
from dataclasses import dataclass

COOLDOWNS = (900, 1800, 3600, 7200)  # pause after consecutive "blocked" results (seconds)
NET_PAUSES = (60, 120, 300, 600)  # pause after consecutive network failures (seconds); no retry attempt is used up
BACKOFF = (300, 1200, 3600, 10800)  # retry delays for transient failures (seconds)
PACE = (3.0, 7.0)  # random pause between two fetches (seconds)
MAX_AGE = 24 * 3600  # stop retrying reels older than this (seconds)


@dataclass
class Job:
    reel_id: str
    url: str
    first_seen: float
    attempts: int = 0
    not_before: float = 0.0


class Prefetcher:
    """fetch(url, reel_id) -> object with .ok, .reason, .detail, .kind, .paths (see wa_reel_dl.FetchResult)."""

    def __init__(
        self,
        fetch,
        on_success=None,
        *,
        clock=time.time,
        rng=random.uniform,
        log=None,
        pace=PACE,
        cooldowns=COOLDOWNS,
        net_pauses=NET_PAUSES,
        backoff=BACKOFF,
        max_age=MAX_AGE,
    ):
        self._fetch = fetch
        self._on_success = on_success
        self._clock = clock
        self._rng = rng
        self._log = log or (lambda m: print(m, flush=True))
        self._pace = pace
        self._cooldowns = cooldowns
        self._net_pauses = net_pauses
        self._backoff = backoff
        self._max_age = max_age

        self._jobs = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._cooldown_until = 0.0
        self._next_allowed = 0.0
        self._strikes = 0
        self._net_strikes = 0
        self._thread = None

    # ------------------------------------------------------------------ public

    def submit(self, reel_id, url, first_seen=None):
        """Queue a reel. False if it is already queued."""
        with self._lock:
            if reel_id in self._jobs:
                return False
            self._jobs[reel_id] = Job(reel_id, url, first_seen or self._clock())
        self._wake.set()
        return True

    def pending(self):
        with self._lock:
            return sorted(self._jobs)

    def status(self):
        now = self._clock()
        with self._lock:
            return {
                "queued": len(self._jobs),
                "paused_for": max(0, int(self._cooldown_until - now)),
                "blocked_strikes": self._strikes,
                "network_strikes": self._net_strikes,
            }

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="reel-prefetch", daemon=True)
            self._thread.start()
        return self

    # --------------------------------------------------------------- scheduling

    def step(self, now=None):
        """
        Run at most one fetch. Returns how many seconds to wait before calling again,
        or None when there is nothing to do until submit() is called.
        """
        now = self._clock() if now is None else now
        with self._lock:
            for rid in [r for r, j in self._jobs.items() if now - j.first_seen > self._max_age]:
                del self._jobs[rid]
                self._log(f"[*] prefetch {rid}: older than {self._max_age // 3600} h, giving up")
            if not self._jobs:
                return None
            if now < self._cooldown_until:
                return self._cooldown_until - now
            if now < self._next_allowed:
                return self._next_allowed - now
            due = [j for j in self._jobs.values() if j.not_before <= now]
            if not due:
                return max(1.0, min(j.not_before for j in self._jobs.values()) - now)
            job = max(due, key=lambda j: j.first_seen)

        self._log(f"[*] Pre-downloading reel in background: {job.reel_id}...")
        try:
            res = self._fetch(job.url, job.reel_id)
        except Exception as e:  # a bug in the fetcher must not kill the worker
            self._log(f"[!] prefetch {job.reel_id}: unexpected error: {e}")
            res = _Failure("error", str(e))

        now = self._clock()
        self._handle(job, res, now)
        gap = self._rng(*self._pace)
        self._next_allowed = now + gap
        return gap

    def _handle(self, job, res, now):
        ok = bool(getattr(res, "ok", False))
        reason = getattr(res, "reason", None) or "error"
        detail = getattr(res, "detail", "") or ""
        with self._lock:
            if ok:
                self._jobs.pop(job.reel_id, None)
                self._strikes = 0
                self._net_strikes = 0
                self._log(f"[✓] prefetch {job.reel_id}: {getattr(res, 'kind', None) or 'media'} ready")
            elif reason == "network":
                # Not the reel's fault and not Instagram's: don't use up one of its attempts, just wait for the network.
                self._net_strikes += 1
                wait = self._net_pauses[min(self._net_strikes, len(self._net_pauses)) - 1]
                self._cooldown_until = max(self._cooldown_until, now + wait)
                short = (detail[:90] + "…") if len(detail) > 90 else detail
                self._log(
                    f"[!] Can't reach Instagram ({short or 'network problem'}) — pausing the fetch queue for {wait} s "
                    f"({len(self._jobs)} queued)"
                )
            elif reason == "blocked":
                self._strikes += 1
                wait = self._cooldowns[min(self._strikes, len(self._cooldowns)) - 1]
                self._cooldown_until = now + wait
                job.not_before = self._cooldown_until
                self._log(
                    f"[!] Instagram is blocking downloads — pausing the fetch queue for {wait // 60} min "
                    f"({len(self._jobs)} queued)"
                )
            elif reason in ("no_video", "no_ytdlp"):
                self._jobs.pop(job.reel_id, None)
                self._log(f"[*] prefetch {job.reel_id}: {detail or reason} — not retrying")
            elif reason == "unavailable":
                job.attempts += 1
                if job.attempts >= 2:
                    self._jobs.pop(job.reel_id, None)
                    self._log(f"[*] prefetch {job.reel_id}: unavailable ({detail}) — giving up")
                else:
                    job.not_before = now + 3600
                    self._log(f"[*] prefetch {job.reel_id}: unavailable ({detail}) — one more try in 60 min")
            else:
                job.attempts += 1
                if job.attempts > len(self._backoff):
                    self._jobs.pop(job.reel_id, None)
                    self._log(
                        f"[*] prefetch {job.reel_id}: {reason} ({detail}) — giving up after {job.attempts} attempts"
                    )
                else:
                    delay = self._backoff[job.attempts - 1]
                    job.not_before = now + delay
                    self._log(f"[*] prefetch {job.reel_id}: {reason} ({detail}) — retry in {delay // 60} min")
        if ok and self._on_success:
            try:
                self._on_success(job.reel_id, res)
            except Exception as e:
                self._log(f"[!] prefetch {job.reel_id}: on_success failed: {e}")

    def _loop(self):
        while True:
            try:
                wait = self.step()
            except Exception as e:
                self._log(f"[!] prefetch loop error: {e}")
                wait = 30.0
            if wait is None:
                self._wake.wait()
            else:
                self._wake.wait(min(wait, 60.0))  # re-check at least once a minute
            self._wake.clear()


class _Failure:
    ok = False
    kind = None
    paths = ()

    def __init__(self, reason, detail=""):
        self.reason = reason
        self.detail = detail
