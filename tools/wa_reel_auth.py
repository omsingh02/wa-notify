#!/usr/bin/env python3
"""
wa_reel_auth.py — optional Instagram session for the reel tools (wa-notify v2)

Anonymous Instagram access gets rate-limited and redirected to the login page, after which every
reel and photo post falls back to the browser. With a session, yt-dlp talks to Instagram the way
your browser tab does, so reels download and photo posts can be fetched too.

Nothing here logs in. It re-uses the session cookies the browser already holds:
  - only instagram.com cookies are copied, to ~/.config/wa-notify/instagram-cookies.txt (mode 0600)
  - the copy is re-read from the browser every `refresh_hours`
  - without a config, or if the browser's cookies can't be read, everything stays anonymous

~/.config/wa-notify/config.toml:

    [instagram]
    browser = "brave"
    profile = "/home/you/.config/BraveSoftware/Brave-Origin/Default"
    keyring = "GNOMEKEYRING"     # Chromium-based browsers on Linux; needs the python-secretstorage package
    refresh_hours = 6

CLI:  wa_reel_auth.py --check       show the state (never prints cookie values)
      wa_reel_auth.py --refresh     re-read the browser's cookies now
"""

import os
import sys
import time
import fcntl
import hashlib
import shutil
import tempfile
import contextlib

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

TOOLS_DIR = os.path.dirname(os.path.realpath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from walib import CONFIG_DIR

CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.toml')
COOKIE_CACHE = os.path.join(CONFIG_DIR, 'instagram-cookies.txt')
LOCK_FILE = os.path.join(CONFIG_DIR, '.instagram-cookies.lock')

STALE_OK_SECONDS = 7 * 86400   # keep using an old copy if the browser can't be read
RETRY_AFTER_FAILURE = 600      # don't re-read the browser more often than this after a failure

_warned = set()
_last_failure = 0.0


def _warn_once(msg):
    if msg not in _warned:
        _warned.add(msg)
        print(f'[!] {msg}', file=sys.stderr)


def instagram_config():
    """The [instagram] table from config.toml, or None when no session is configured."""
    if tomllib is None:
        return None
    try:
        with open(CONFIG_FILE, 'rb') as f:
            cfg = tomllib.load(f).get('instagram')
    except FileNotFoundError:
        return None
    except Exception as e:
        _warn_once(f'cannot read {CONFIG_FILE}: {e}')
        return None
    return cfg if isinstance(cfg, dict) and cfg.get('browser') else None


def _cache_age():
    try:
        return time.time() - os.path.getmtime(COOKIE_CACHE)
    except OSError:
        return None


def _digest():
    try:
        with open(COOKIE_CACHE, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _stale_copy(age):
    return COOKIE_CACHE if age is not None and age < STALE_OK_SECONDS else None


class _CaptureLogger:
    """yt-dlp's cookie extractor reports problems through a logger instead of raising."""
    def __init__(self):
        self.errors = []
        self.warnings = 0

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg, only_once=False):
        self.warnings += 1

    def error(self, msg):
        self.errors.append(str(msg))


def _read_from_browser(cfg):
    """Instagram-only cookie jar read straight from the browser profile. Raises RuntimeError with a reason."""
    from yt_dlp.cookies import extract_cookies_from_browser, YoutubeDLCookieJar

    log = _CaptureLogger()
    kwargs = {}
    if cfg.get('keyring'):
        kwargs['keyring'] = str(cfg['keyring']).upper()
    jar = extract_cookies_from_browser(str(cfg['browser']), profile=cfg.get('profile') or None, logger=log, **kwargs)

    ig = [c for c in jar if (c.domain or '').lstrip('.').lower().endswith('instagram.com')]
    if not any(c.name == 'sessionid' and c.value for c in ig):
        if log.errors:
            why = log.errors[0]
        elif log.warnings:
            why = f'{log.warnings} cookie(s) could not be decrypted'
        else:
            why = 'no sessionid cookie — not logged in to Instagram in that profile?'
        raise RuntimeError(why)

    out = YoutubeDLCookieJar()
    for c in ig:
        out.set_cookie(c)
    return out


def _write_cache(jar):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.instagram-cookies.', dir=CONFIG_DIR)   # mkstemp => mode 0600
    os.close(fd)
    try:
        jar.save(tmp, ignore_discard=True, ignore_expires=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, COOKIE_CACHE)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def _locked():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)   # closing releases the lock


def ensure_cookie_cache(force=False):
    """Path of an up-to-date, Instagram-only cookie file — or None, meaning "go anonymous"."""
    global _last_failure
    cfg = instagram_config()
    if not cfg:
        return None

    max_age = float(cfg.get('refresh_hours', 6)) * 3600
    age = _cache_age()
    if not force and age is not None and age < max_age:
        return COOKIE_CACHE
    if not force and time.time() - _last_failure < RETRY_AFTER_FAILURE:
        return _stale_copy(age)

    with _locked():
        age = _cache_age()
        if not force and age is not None and age < max_age:
            return COOKIE_CACHE   # another process refreshed it while we waited for the lock
        try:
            _write_cache(_read_from_browser(cfg))
            return COOKIE_CACHE
        except Exception as e:
            _last_failure = time.time()
            usable = _stale_copy(_cache_age())
            _warn_once(f"Instagram session: couldn't read cookies from {cfg.get('browser')} ({e}) — "
                       + ('using the previous copy' if usable else 'continuing anonymously'))
            return usable


def refresh_cookies(min_age=120):
    """Re-read the browser's cookies now. True only if the stored session actually changed."""
    age = _cache_age()
    if age is not None and age < min_age:
        return False
    before = _digest()
    ensure_cookie_cache(force=True)
    after = _digest()
    return after is not None and after != before


@contextlib.contextmanager
def cookie_copy():
    """
    Yield a private throw-away copy of the cookie file for one yt-dlp run (yt-dlp rewrites its
    --cookies file on exit, so concurrent runs must not share one), or None to go anonymous.
    """
    src = ensure_cookie_cache()
    tmp = None
    if src:
        try:
            fd, tmp = tempfile.mkstemp(prefix='.ytdlp-cookies.', dir=CONFIG_DIR)
            with os.fdopen(fd, 'wb') as out, open(src, 'rb') as f:
                shutil.copyfileobj(f, out)
        except OSError:
            if tmp:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
            tmp = None   # couldn't copy it: run anonymously rather than fail
    try:
        yield tmp
    finally:
        if tmp:
            with contextlib.suppress(OSError):
                os.unlink(tmp)


def _expiry_unix(raw):
    """
    A cookie expiry as Unix seconds, or None for a session cookie. yt-dlp keeps Chromium's native value
    in its cookie jar (microseconds since 1601-01-01), so convert whatever form turns up.
    """
    try:
        raw = float(raw)
    except (TypeError, ValueError):
        return None
    if raw <= 0:
        return None
    if raw > 1e14:    # Chromium: microseconds since 1601-01-01
        return raw / 1e6 - 11644473600
    if raw > 1e11:    # milliseconds since 1970
        return raw / 1000
    return raw


def describe():
    """Human-readable state for --check. Never includes cookie values."""
    import datetime
    lines = []
    cfg = instagram_config()
    if not cfg:
        return [f'not configured — add an [instagram] table to {CONFIG_FILE} to enable the session (anonymous access only for now)']
    lines.append(f"config : {cfg.get('browser')} profile={cfg.get('profile') or '(default)'} keyring={cfg.get('keyring') or '(auto)'} "
                 f"refresh every {cfg.get('refresh_hours', 6)}h")
    age = _cache_age()
    if age is None:
        lines.append(f'cache  : {COOKIE_CACHE} does not exist yet')
        return lines
    mode = oct(os.stat(COOKIE_CACHE).st_mode & 0o777)
    lines.append(f'cache  : {COOKIE_CACHE}  age {int(age // 60)} min  mode {mode}')
    found = False
    expiry = None
    try:
        with open(COOKIE_CACHE, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.rstrip('\n')
                if line.startswith('#HttpOnly_'):
                    line = line[len('#HttpOnly_'):]
                elif line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) >= 7 and parts[5] == 'sessionid' and parts[6]:
                    found = True
                    expiry = _expiry_unix(parts[4])
    except OSError:
        pass
    if not found:
        lines.append('session: NO sessionid cookie in the cache')
    elif expiry is not None and expiry < time.time():
        lines.append('session: sessionid present but EXPIRED')
    else:
        try:
            when = datetime.datetime.fromtimestamp(expiry).strftime('%Y-%m-%d') if expiry is not None else 'end of browser session'
        except (OverflowError, OSError, ValueError):
            when = 'an unknown date'
        lines.append(f'session: sessionid present, expires {when}')
    return lines


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ('--check', '--refresh'):
        print('Usage: wa_reel_auth.py --check | --refresh')
        sys.exit(1)
    if args[0] == '--refresh':
        path = ensure_cookie_cache(force=True)
        print('[✓] cookies refreshed from the browser' if path and _cache_age() is not None and _cache_age() < 60
              else '[!] refresh did not produce a fresh copy (see message above)')
    print('\n'.join(describe()))


if __name__ == '__main__':
    main()
