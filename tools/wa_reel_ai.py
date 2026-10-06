#!/usr/bin/env python3
"""
wa_reel_ai.py — Universal Multimodal Summarizer for Instagram Reels (v2)
Uses Gemini (models from [ai] models, default gemini-3.5-flash then gemini-3.5-flash-lite) to process
raw video frames and audio together. Employs a substance-focused, universal prompt without hardcoded
cases, capturing names, dialogue, lower-thirds, and punchlines.

Stores summaries in SQLite (wa-notify.db) and caches to disk.
Videos larger than [ai] max_inline_mb are downscaled and trimmed with ffmpeg first.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

TOOLS_DIR = os.path.dirname(os.path.realpath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from walib import (  # noqa: E402
    REELS_DIR,
    SECRETS_FILE,
    SETTINGS,
    SUMMARIES_DIR,
    get_db,
    get_local_reel_file,
    get_reel_id,
)

UNIVERSAL_SYSTEM_PROMPT = """You are an expert video reel summarizer. Provide a direct, concise, and substance-focused summary of the reel in 1-3 natural sentences.

Instructions:
- Prioritize factual substance: Tell the user exactly who is in the video, what is being said or shown, and the takeaway, punchline, or core message.
- Read on-screen text, lower-thirds, name tags, and captions: Explicitly name all participants, interviewers, speakers, or figures shown.
- Synthesize both visual and audio content: Capture spoken dialogue, arguments, prominent on-screen quotes/text, and any notable audio/music context.
- No AI fluff: Never use cliché filler like "This video showcases", "The camera pans", "In this reel", or describe irrelevant aesthetics/lighting unless the reel is explicitly about visual art. Jump straight into the content.
"""

AI = SETTINGS.ai
MAX_INLINE_BYTES = AI.max_inline_mb * 1024 * 1024
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
KEY_NAMES = ("GEMINI_API_KEY_REELS", "GEMINI_API_KEY")  # looked up in this order, env first then the secrets file
REQUEST_TIMEOUT = 45  # seconds per API call
FFMPEG_TIMEOUT = 25  # seconds for the downscale pass
RETRY_STATUS = (429, 503)  # temporary overload / rate limit: worth one more try
DEFAULT_RETRY_DELAY = 2.5
MAX_RETRY_DELAY = 10.0
ERROR_BODY_LIMIT = 300  # bytes of an HTTP error body worth logging


# ------------------------------------------------------------------ API key


def _clean_value(raw: str) -> str:
    """Value part of a KEY=VALUE line: quotes removed, a trailing ' # comment' dropped."""
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        return raw[1:end] if end != -1 else raw[1:]
    for marker in (" #", "\t#"):
        raw = raw.split(marker, 1)[0]
    return raw.strip()


def parse_secrets(text: str) -> dict[str, str]:
    """Gemini key names found in a shell-style secrets file. Commented-out lines are ignored; the first live one wins."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        name, sep, value = line.partition("=")
        name = name.strip()
        if sep and name in KEY_NAMES:
            found.setdefault(name, _clean_value(value))
    return found


def get_api_key() -> str | None:
    """Gemini API key from the environment, else the secrets file; None if there isn't one."""
    for name in KEY_NAMES:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip().strip("\"' ")
    try:
        text = Path(SECRETS_FILE).read_text(encoding="utf-8")
    except OSError:
        return None
    keys = parse_secrets(text)
    return next((keys[n] for n in KEY_NAMES if keys.get(n)), None)


# ------------------------------------------------------------------ summary cache


def get_cached_summary(reel_id: str) -> str | None:
    """Summary from SQLite, else from the disk cache."""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT summary FROM reels WHERE reel_id = ? AND summary IS NOT NULL", (reel_id,)
            ).fetchone()
            if row and row["summary"]:
                return row["summary"].strip()
    except sqlite3.Error:
        pass

    path = Path(SUMMARIES_DIR) / f"{reel_id}.txt"
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return content or None


def save_summary(reel_id: str, summary_text: str) -> None:
    """Store a summary in SQLite and on disk."""
    text = summary_text.strip()
    try:
        with get_db() as conn:
            conn.execute("UPDATE reels SET summary = ?, summary_status = 'done' WHERE reel_id = ?", (text, reel_id))
    except sqlite3.Error as e:
        print(f"[!] Failed to save summary to SQLite for {reel_id}: {e}", file=sys.stderr)

    try:
        os.makedirs(SUMMARIES_DIR, exist_ok=True)
        (Path(SUMMARIES_DIR) / f"{reel_id}.txt").write_text(text + "\n", encoding="utf-8")
    except OSError as e:
        print(f"[!] Failed to save summary to disk for {reel_id}: {e}", file=sys.stderr)


# ------------------------------------------------------------------ video preparation


def prepare_video_bytes(video_path: str, reel_id: str) -> bytes:
    """
    Video bytes for the request. Anything over [ai] max_inline_mb is first downscaled to 720p and
    cut to [ai] clip_seconds with ffmpeg (in a private temp dir); if that fails the original is used.
    """
    size = os.path.getsize(video_path)
    if size <= MAX_INLINE_BYTES:
        return Path(video_path).read_bytes()

    print(
        f"[*] Video for {reel_id} is {size / 1024 / 1024:.1f}MB (>{AI.max_inline_mb}MB limit) — downscaling with ffmpeg..."
    )
    with tempfile.TemporaryDirectory(prefix="wa_reel_opt_") as tmp:
        target = os.path.join(tmp, f"{reel_id}.mp4")
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-vf",
            "scale='min(720,iw)':-2",
            "-c:v",
            "libx264",
            "-crf",
            "28",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-t",
            str(AI.clip_seconds),
            target,
        ]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=FFMPEG_TIMEOUT)
            if proc.returncode == 0 and os.path.exists(target):
                data = Path(target).read_bytes()
                print(f"[✓] Downscaled {reel_id} to {len(data) / 1024 / 1024:.1f}MB for API payload")
                return data
        except (OSError, subprocess.SubprocessError) as e:
            print(f"[!] ffmpeg compression failed: {e}", file=sys.stderr)

    return Path(video_path).read_bytes()  # fall back to the original


# ------------------------------------------------------------------ Gemini call


def extract_text(data: dict) -> str:
    """Concatenated text parts of the first candidate ('' if there are none)."""
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return " ".join(p.get("text", "").strip() for p in parts if "text" in p).strip()


def describe_empty(data: dict) -> str:
    """Why a 200 response carried no text (safety block, truncation, ...)."""
    reasons = []
    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        reasons.append(f"blockReason={block}")
    candidates = data.get("candidates") or []
    if candidates and candidates[0].get("finishReason"):
        reasons.append(f"finishReason={candidates[0]['finishReason']}")
    return ", ".join(reasons) or "no candidates"


def _retry_delay(err: urllib.error.HTTPError) -> float:
    """Server-suggested wait (Retry-After seconds), capped; default otherwise."""
    try:
        return min(float(err.headers.get("Retry-After", "")), MAX_RETRY_DELAY)
    except (TypeError, ValueError, AttributeError):
        return DEFAULT_RETRY_DELAY


def _error_body(err: urllib.error.HTTPError) -> str:
    """Bounded, single-line excerpt of an API error body (it says WHY: quota, bad key, bad request...)."""
    try:
        raw = err.read(ERROR_BODY_LIMIT).decode("utf-8", "replace")
    except (OSError, AttributeError):
        return ""
    return "— " + " ".join(raw.split()) if raw.strip() else ""


def generate_summary(model: str, body: bytes, api_key: str) -> str | None:
    """One model, at most two attempts. Retries only what can change: overload/rate limit and network errors."""
    req = urllib.request.Request(
        API_URL.format(model=model),
        data=body,
        headers={"Content-Type": "application/json", "X-goog-api-key": api_key},
    )
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in RETRY_STATUS and attempt == 0:
                time.sleep(_retry_delay(e))
                continue
            print(f"[!] Gemini {model} HTTP {e.code}: {e.reason} {_error_body(e)}".rstrip(), file=sys.stderr)
            return None
        except (OSError, ValueError) as e:  # URLError / timeouts are OSError; unreadable JSON is ValueError
            print(f"[!] Gemini {model} attempt {attempt + 1} error: {e}", file=sys.stderr)
            if attempt == 0:
                time.sleep(1.5)
                continue
            return None

        text = extract_text(data)
        if text:
            return text
        # A 200 without text (safety block, ...) would come back identical: don't spend a second call on it.
        print(f"[!] Gemini {model} returned no summary ({describe_empty(data)})", file=sys.stderr)
        return None
    return None


def summarize_reel(reel_id: str, force: bool = False) -> str | None:
    """
    Summarizes a downloaded reel using the Gemini multimodal API.
    Returns the summary text or None on failure.
    """
    if not force:
        cached = get_cached_summary(reel_id)
        if cached:
            return cached

    video_path = get_local_reel_file(reel_id)
    if not video_path:
        print(f"[!] No video file found for reel ID {reel_id} in {REELS_DIR}", file=sys.stderr)
        return None

    api_key = get_api_key()
    if not api_key:
        print(f"[!] No {KEY_NAMES[0]} found in {SECRETS_FILE} or env", file=sys.stderr)
        return None

    try:
        b64_video = base64.b64encode(prepare_video_bytes(video_path, reel_id)).decode("utf-8")
    except OSError as e:
        print(f"[!] Failed reading video file {video_path}: {e}", file=sys.stderr)
        return None

    payload = {
        "contents": [
            {
                "parts": [
                    {"inlineData": {"mimeType": "video/mp4", "data": b64_video}},
                    {"text": "Summarize this reel directly."},
                ]
            }
        ],
        "systemInstruction": {"parts": [{"text": UNIVERSAL_SYSTEM_PROMPT}]},
        "generationConfig": {"temperature": 0.2},
    }
    body = json.dumps(payload).encode("utf-8")

    for model in AI.models:  # fallback cascade, in the configured order
        summary = generate_summary(model, body, api_key)
        if summary:
            save_summary(reel_id, summary)
            return summary
    return None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Summarize a downloaded reel with Gemini (cached in SQLite and in the summaries/ directory).",
    )
    parser.add_argument("reel", nargs="?", metavar="reel_id_or_url", help="Instagram shortcode, or a full /reel/ URL")
    parser.add_argument("--force", action="store_true", help="summarize again even if a cached summary exists")
    args = parser.parse_args(argv)
    if not args.reel:
        parser.print_usage()
        sys.exit(1)

    reel_id = get_reel_id(args.reel)
    summary = summarize_reel(reel_id, force=args.force)
    if summary:
        print(f"\n[Summary for {reel_id}]:\n{summary}")
    else:
        print(f"\n[!] Failed to generate summary for {reel_id}")
        sys.exit(1)


if __name__ == "__main__":
    main()
