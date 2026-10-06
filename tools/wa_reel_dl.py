#!/usr/bin/env python3
"""
wa_reel_dl.py — yt-dlp download & media fetch manager for WhatsApp Reels (v2)

Provides:
- fetch_media(): the main entry point. Returns a FetchResult: the reel's video or — for photo posts
  and carousels, which yt-dlp can't download — their images. One download per reel at a time (a
  cross-process lock, so a click while the daemon is still pre-downloading waits for that download
  instead of racing it), the Instagram session from wa_reel_auth when one is configured, and a
  classified failure reason.
- download_reel(): video-only wrapper that keeps the old (success, path) signature.
- predownload_async(): background daemon thread wrapper for non-blocking alerts.
- get_reel_metadata(): metadata extraction (title, description, duration).
- Standalone CLI execution: wa-reel-dl <reel_id_or_url> [--summary]
"""

import argparse
import contextlib
import fcntl
import http.client
import json
import os
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

TOOLS_DIR = os.path.dirname(os.path.realpath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import wa_reel_auth as auth
from walib import REELS_DIR, get_db, get_local_images, get_local_reel_file, get_reel_id

_BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"


@dataclass
class FetchResult:
    ok: bool
    kind: str | None = None  # 'video' | 'images'
    paths: list[str] = field(default_factory=list)
    reason: str | None = (
        None  # blocked | network | no_video | no_formats | empty | unavailable | timeout | no_ytdlp | error
    )
    detail: str = ""

    @property
    def path(self) -> str | None:
        return self.paths[0] if self.paths else None


# Wording of "I could not reach the server at all" (DNS, connect, TLS, timeouts) from yt-dlp, curl and urllib.
_NETWORK_HINTS = (
    "resolving timed out",
    "connection timed out",
    "read timed out",
    "timed out",
    "name resolution",
    "name or service not known",
    "nodename nor servname",
    "getaddrinfo",
    "temporary failure",
    "network is unreachable",
    "no route to host",
    "connection refused",
    "connection reset",
    "connection aborted",
    "failed to establish a new connection",
    "transporterror",
    "curl: (6)",
    "curl: (7)",
    "curl: (28)",
    "curl: (35)",
    "curl: (56)",
)


def classify_error(text: str, url: str = "", authed: bool = False) -> tuple[str, str]:
    """(reason, one-line detail) for yt-dlp's stderr."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    errors = [line for line in lines if line.startswith("ERROR")]
    first = errors[0] if errors else (lines[0] if lines else "")
    full = (text or "").lower()

    if "no video in this post" in full:
        return "no_video", first
    if "no video formats found" in full:
        # on a /p/ post that is a carousel of images; on a reel it would be a real extraction problem
        return ("no_video" if "/p/" in url else "no_formats"), first
    if (
        "rate-limit" in full
        or "rate limit" in full
        or "redirected to the login page" in full
        or "login required" in full
        or "http error 429" in full
        or "too many requests" in full
    ):
        return "blocked", first
    if "empty media response" in full:
        return ("empty" if authed else "blocked"), first  # anonymously this just means "log in"
    if any(h in full for h in _NETWORK_HINTS):
        return "network", first
    if (
        "isn't available to everyone" in full
        or "registered users who follow" in full
        or "private" in full
        or "not available" in full
        or "unavailable" in full
        or "removed" in full
        or "http error 404" in full
    ):
        return "unavailable", first
    return "error", first


# ------------------------------------------------------------------ per-reel lock


def _lock_dir() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR")
    d = os.path.join(base, "wa-notify", "locks") if base and os.path.isdir(base) else os.path.join(REELS_DIR, ".locks")
    os.makedirs(d, exist_ok=True)
    return d


@contextlib.contextmanager
def reel_lock(reel_id: str, timeout: float = 120.0):
    """Cross-process (and cross-thread) lock for one reel. Yields True when held, False if it timed out."""
    fd = os.open(os.path.join(_lock_dir(), f"{reel_id}.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    got = False
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.25)
        yield got
    finally:
        os.close(fd)  # closing releases the lock


# ------------------------------------------------------------------ helpers


def _record(reel_id: str, path: str) -> None:
    try:
        with get_db() as conn:
            conn.execute("UPDATE reels SET is_downloaded = 1, local_path = ? WHERE reel_id = ?", (path, reel_id))
    except Exception as e:
        print(f"[!] could not record download of {reel_id}: {e}", file=sys.stderr)


def _cached(reel_id: str) -> FetchResult | None:
    video = get_local_reel_file(reel_id)
    if video:
        _record(reel_id, video)
        return FetchResult(True, "video", [video])
    images = get_local_images(reel_id)
    if images:
        _record(reel_id, images[0])
        return FetchResult(True, "images", images)
    return None


def _run_ytdlp(cmd: list[str], timeout: float, quiet: bool) -> tuple[int, str]:
    """Run yt-dlp in its own process group so a timeout also takes ffmpeg down with it."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL if quiet else None, stderr=subprocess.PIPE, text=True, start_new_session=True
    )
    try:
        _, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise
    if err and not quiet:
        sys.stderr.write(err)
    return proc.returncode, err or ""


def _fetch_video(
    reel_id: str, url: str, timeout: float, quiet: bool, expect_images: bool = False, socket_timeout: float = 30
) -> FetchResult:
    target_base = os.path.join(REELS_DIR, reel_id)
    retried_with_fresh_cookies = False

    while True:
        with auth.cookie_copy() as cookies:
            cmd = [
                "yt-dlp",
                "--no-playlist",
                "--no-warnings",
                "--socket-timeout",
                str(int(socket_timeout)),
                "-o",
                f"{target_base}.%(ext)s",
            ]
            if cookies:
                cmd += ["--cookies", cookies]
            if quiet:
                cmd.append("--quiet")
            cmd.append(url)

            try:
                rc, err = _run_ytdlp(cmd, timeout, quiet)
            except subprocess.TimeoutExpired:
                print(f"[!] yt-dlp download timed out ({reel_id}) after {timeout}s", file=sys.stderr)
                return FetchResult(False, reason="timeout", detail=f"timed out after {timeout}s")
            except FileNotFoundError:
                print("[!] yt-dlp is not installed", file=sys.stderr)
                return FetchResult(False, reason="no_ytdlp", detail="yt-dlp not found")
            except Exception as e:
                print(f"[!] yt-dlp exception ({reel_id}): {e}", file=sys.stderr)
                return FetchResult(False, reason="error", detail=str(e))
            authed = bool(cookies)

        path = get_local_reel_file(reel_id)
        if rc == 0 and path:
            _record(reel_id, path)
            return FetchResult(True, "video", [path])

        reason, first = classify_error(err, url, authed)
        if rc == 0 and not first:
            first = "yt-dlp finished but produced no video file"

        # An expired session looks like "blocked" too: if the browser has a newer one, try once more with it.
        if reason == "blocked" and authed and not retried_with_fresh_cookies:
            retried_with_fresh_cookies = True
            if auth.refresh_cookies():
                continue

        # For a photo post the image route runs next, so that isn't a failure worth reporting yet.
        if first and not (expect_images and reason == "no_video"):
            print(f"[!] yt-dlp download failed ({reel_id}): {first}", file=sys.stderr)
        return FetchResult(False, reason=reason, detail=first)


def _image_urls(info: dict[str, Any]) -> list[str]:
    """Largest image of every picture item in a yt-dlp info dict (single photo post or carousel)."""
    items = info.get("entries") or [info]
    urls = []
    for item in items:
        if not isinstance(item, dict) or item.get("formats"):
            continue  # a video item — the normal video download deals with those
        thumbs = [t for t in (item.get("thumbnails") or []) if isinstance(t, dict) and t.get("url")]
        if thumbs:
            best = max(thumbs, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
            urls.append(best["url"])
    return urls


def _download_images(reel_id: str, urls: list[str], timeout: float = 30) -> list[str]:
    """Download every image, then move them into place together so a half-fetched carousel is never shown."""
    partials: list[str] = []
    staged: list[tuple[str, str]] = []
    try:
        for n, u in enumerate(urls, 1):
            req = urllib.request.Request(
                u, headers={"User-Agent": _BROWSER_UA, "Referer": "https://www.instagram.com/"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(
                    resp.headers.get_content_type(), ".jpg"
                )
                tmp = os.path.join(REELS_DIR, f".{reel_id}_{n}{ext}.part")
                partials.append(tmp)
                with open(tmp, "wb") as f:
                    shutil.copyfileobj(resp, f)
            if os.path.getsize(tmp) < 1000:
                raise OSError(f"image {n} is too small ({os.path.getsize(tmp)} bytes)")
            staged.append((tmp, os.path.join(REELS_DIR, f"{reel_id}_{n}{ext}")))
        for tmp, final in staged:
            os.replace(tmp, final)
        return [final for _, final in staged]
    except BaseException:
        for tmp in partials:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        raise


def _classify_exception(e: BaseException) -> str:
    """Failure reason for an exception raised while downloading an image."""
    if isinstance(e, urllib.error.HTTPError):
        return "error"  # e.g. an expired signed URL — listing the post again next time gives a fresh one
    if isinstance(
        e,
        (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            ssl.SSLError,
            http.client.HTTPException,
            socket.gaierror,
        ),
    ):
        return "network"
    return "error"


def _fetch_images(reel_id: str, url: str, timeout: float, socket_timeout: float = 30) -> FetchResult:
    """Photo post or carousel: have yt-dlp list the post's media (it knows Instagram's API) and save the pictures."""
    with auth.cookie_copy() as cookies:
        cmd = [
            "yt-dlp",
            "-J",
            "--ignore-no-formats-error",
            "--no-warnings",
            "--no-playlist",
            "--socket-timeout",
            str(int(socket_timeout)),
        ]
        if cookies:
            cmd += ["--cookies", cookies]
        cmd.append(url)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return FetchResult(False, reason="timeout", detail=f"listing the post timed out after {timeout}s")
        except FileNotFoundError:
            return FetchResult(False, reason="no_ytdlp", detail="yt-dlp not found")
        authed = bool(cookies)

    if proc.returncode != 0:
        reason, first = classify_error(proc.stderr, url, authed)
        print(f"[!] yt-dlp could not list {reel_id}: {first}", file=sys.stderr)
        return FetchResult(False, reason="no_video" if reason == "no_formats" else reason, detail=first)

    try:
        urls = _image_urls(json.loads(proc.stdout))
    except ValueError:
        return FetchResult(False, reason="error", detail="unreadable yt-dlp output")
    if not urls:
        print(f"[!] no downloadable video or images found in {reel_id}", file=sys.stderr)
        return FetchResult(False, reason="no_video", detail="the post has no downloadable video or images")

    try:
        paths = _download_images(reel_id, urls, timeout=max(30, socket_timeout))
    except Exception as e:
        print(f"[!] image download failed ({reel_id}): {e}", file=sys.stderr)
        return FetchResult(False, reason=_classify_exception(e), detail=str(e))

    _record(reel_id, paths[0])
    return FetchResult(True, "images", paths)


# ------------------------------------------------------------------ public API


def fetch_media(
    reel_id_or_url: str,
    timeout: float = 60,
    quiet: bool = True,
    interactive: bool = False,
    want_images: bool = True,
    socket_timeout: float = 30,
) -> FetchResult:
    """
    Make sure a reel's media is cached locally and say what it is.

    interactive=True means somebody is waiting for it (menu / notification click), which only
    changes how long to wait for a download of the same reel that is already running elsewhere.
    socket_timeout is yt-dlp's per-connection timeout. It is generous on purpose: a DNS lookup that
    stalls for 10-20 s shouldn't count as a failure. If somebody is waiting and the network
    failed anyway, the fetch is tried once more (the resolver usually has the answer by then).
    """
    reel_id = get_reel_id(reel_id_or_url)
    url = reel_id_or_url if reel_id_or_url.startswith("http") else f"https://www.instagram.com/reel/{reel_id}"

    cached = _cached(reel_id)
    if cached:
        return cached

    os.makedirs(REELS_DIR, exist_ok=True)
    with reel_lock(reel_id, timeout=100 if interactive else 300) as got:
        cached = _cached(reel_id)  # the download we waited for may have finished it
        if cached:
            return cached
        if not got:
            return FetchResult(False, reason="timeout", detail="another download of this reel is still running")

        def attempt() -> FetchResult:
            res = _fetch_video(reel_id, url, timeout, quiet, expect_images=want_images, socket_timeout=socket_timeout)
            if not res.ok and res.reason == "no_video" and want_images:
                res = _fetch_images(reel_id, url, timeout, socket_timeout=socket_timeout)
            return res

        result = attempt()
        if interactive and result.reason == "network":
            print(f"[*] {reel_id}: network trouble, trying once more...", file=sys.stderr)
            result = attempt()
        return result


def download_reel(
    reel_id_or_url: str, timeout: int = 45, quiet: bool = True, on_complete=None
) -> tuple[bool, str | None]:
    """
    Downloads an Instagram reel video using yt-dlp into ~/.local/share/wa-notify/reels/.

    Returns:
        (success: bool, local_path: Optional[str])
    """
    reel_id = get_reel_id(reel_id_or_url)
    res = fetch_media(reel_id_or_url, timeout=timeout, quiet=quiet, want_images=False)
    if res.ok and res.kind == "video":
        if on_complete:
            try:
                on_complete(reel_id)
            except Exception as e:
                print(f"[!] on_complete callback error for {reel_id}: {e}", file=sys.stderr)
        return True, res.path
    return False, None


def predownload_async(reel_id_or_url: str, timeout: int = 45, on_complete=None) -> threading.Thread:
    """Spawns download_reel in a detached background daemon thread."""
    reel_id = get_reel_id(reel_id_or_url)
    print(f"[*] Pre-downloading reel in background: {reel_id}...")
    t = threading.Thread(
        target=download_reel,
        args=(reel_id_or_url,),
        kwargs={"timeout": timeout, "quiet": True, "on_complete": on_complete},
        daemon=True,
    )
    t.start()
    return t


def get_reel_metadata(reel_id_or_url: str, timeout: int = 15) -> dict[str, Any] | None:
    """Extracts reel metadata (title, description, duration) without downloading."""
    reel_id = get_reel_id(reel_id_or_url)
    reel_url = reel_id_or_url if reel_id_or_url.startswith("http") else f"https://www.instagram.com/reel/{reel_id}"
    cmd = ["yt-dlp", "--dump-json", "--skip-download", "--socket-timeout", "10", reel_url]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, text=True)
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout)
    except Exception:
        pass
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download an Instagram reel (video) or photo post (images) into the local media cache.",
    )
    parser.add_argument(
        "reel", nargs="?", metavar="reel_id_or_url", help="Instagram shortcode, or a full /reel/ or /p/ URL"
    )
    parser.add_argument("--summary", action="store_true", help="also summarize the downloaded video with Gemini")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.reel:
        parser.print_usage()
        sys.exit(1)

    print(f"[*] Downloading {args.reel}...")
    res = fetch_media(args.reel, timeout=120, quiet=False, interactive=True)
    if res.ok:
        print(f"\n[✓] Successfully fetched {res.kind}: {', '.join(res.paths)}")
        if args.summary and res.kind == "video":
            from wa_reel_ai import summarize_reel

            summarize_reel(get_reel_id(args.reel))
    else:
        print(f"\n[!] Failed to download {args.reel} ({res.reason}: {res.detail})", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
