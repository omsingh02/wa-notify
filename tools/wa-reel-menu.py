#!/usr/bin/env python3
"""
wa-reel-menu — Interactive dmenu-style picker for unopened WhatsApp Instagram Reels (v2)
Lists all unopened reels from SQLite (<data dir>/wa-notify.db) with sender name, arrival time, and
substantive AI summary preview. Selecting an item opens it immediately in the floating MPV player and
marks it opened.

The launcher (default: fuzzel), its font/width/height and the terminal used for the full digest come
from wa_settings ([ui] in config.toml). The launcher flags used are fuzzel's (--dmenu --index ...).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

TOOLS_DIR = os.path.dirname(os.path.realpath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from walib import (  # noqa: E402
    UI,
    format_time,
    get_all_reels,
    get_local_reel_file,
    get_opened_reels,
    get_unopened_reels,
    mark_opened_many,
    play_in_mpv,
)

HISTORY_LIMIT = 25  # rows shown for --history / --all
FALLBACK_HISTORY_LIMIT = 20  # rows shown when there is nothing unopened
MIN_SENDER_WIDTH = 22
DIGEST_SCRIPT = os.path.join(TOOLS_DIR, "wa-reel-digest.py")

LABEL_MARK_ALL = "Mark all as opened"
LABEL_SHOW_HISTORY = "View watched reels history"
LABEL_SHOW_UNOPENED = "View unopened reels only"
LABEL_DIGEST = "Open full digest in terminal (wa-reel-digest)"


def _notify(body: str) -> None:
    """Low-urgency desktop notification (best effort)."""
    subprocess.run(
        ["notify-send", "-a", UI.notify_app_name, "-i", "instagram", "-u", "low", "Instagram Reels", body],
        check=False,
    )


def choose_reels(args: argparse.Namespace) -> tuple[list[dict], str, bool]:
    """(reels, prompt, is_history_mode) for the requested view."""
    if args.opened:
        return get_opened_reels(limit=HISTORY_LIMIT), "Reels History: ", True
    if args.all:
        return get_all_reels(limit=HISTORY_LIMIT), "All Reels: ", True
    reels = get_unopened_reels()
    if reels:
        return reels, "Reels: ", False
    # Nothing unopened: fall back to history
    return get_opened_reels(limit=FALLBACK_HISTORY_LIMIT), "Reels (History): ", True


def reel_line(reel: dict, sender_width: int) -> str:
    """One aligned menu line: sender | age + cached marker | summary (or title / placeholder)."""
    sender = reel.get("sender") or "Unknown"
    reel_id = reel.get("reel_id") or ""
    ts = reel.get("timestamp") or ((reel.get("first_seen_at") or 0) // 1000)
    _, rel = format_time(ts)

    local_file = get_local_reel_file(reel_id)
    cached = "cached" if local_file else "      "

    summary = (reel.get("summary") or "").replace("\n", " ").strip()
    if not summary and reel.get("title"):
        summary = reel["title"].replace("\n", " ").strip()
    if not summary:
        summary = "(Downloaded | Ready to play)" if local_file else "(Stream only | Click to play)"

    return f"{sender:<{sender_width}} | {rel[:7]:<7} {cached} | {summary}"


def build_menu(reels: list[dict], is_history_mode: bool) -> tuple[list[str], dict[int, dict], dict[str, int]]:
    """(menu lines, row-index -> reel, action label -> row index)."""
    sender_width = max(max(len(r.get("sender") or "Unknown") for r in reels), MIN_SENDER_WIDTH) + 2
    lines = [reel_line(r, sender_width) for r in reels]
    lookup = dict(enumerate(reels))
    actions: dict[str, int] = {}

    def add_action(label: str) -> None:
        actions[label] = len(lines)
        lines.append(label)

    if not is_history_mode:
        add_action(LABEL_MARK_ALL)
        add_action(LABEL_SHOW_HISTORY)
    else:
        add_action(LABEL_SHOW_UNOPENED)
    add_action(LABEL_DIGEST)
    return lines, lookup, actions


def launcher_command(prompt: str, line_count: int) -> list[str]:
    """dmenu-style picker invocation (fuzzel flags)."""
    return [
        UI.launcher,
        "--dmenu",
        "--index",
        "-p",
        prompt,
        "-f",
        UI.launcher_font,
        "-w",
        str(UI.launcher_width),
        "-l",
        str(min(line_count, UI.launcher_max_lines)),
        "--match-mode=fzf",
    ]


def spawn_self(*extra: str) -> None:
    """Relaunch this script (e.g. with --history) without relying on PATH."""
    subprocess.Popen([sys.executable, os.path.realpath(__file__), *extra])


def open_digest(show_all: bool) -> None:
    """Open wa-reel-digest in the floating terminal, falling back to a plain terminal if that can't start."""
    tail = ["-e", sys.executable, DIGEST_SCRIPT, *(["--all"] if show_all else [])]
    try:
        subprocess.Popen([*UI.terminal, *tail])
    except OSError:
        subprocess.Popen([*UI.terminal_fallback, *tail])  # e.g. footclient exists but no foot server is reachable


def handle_selection(
    index: int, reels: list[dict], lookup: dict[int, dict], actions: dict[str, int], is_history_mode: bool
) -> None:
    if index == actions.get(LABEL_MARK_ALL):
        # Only what was listed: reels that arrived while the menu was open stay unopened.
        count = mark_opened_many(r["reel_id"] for r in reels)
        _notify(f"Marked {count} reel(s) as opened")
    elif index == actions.get(LABEL_SHOW_HISTORY):
        spawn_self("--history")
    elif index == actions.get(LABEL_SHOW_UNOPENED):
        spawn_self()
    elif index == actions.get(LABEL_DIGEST):
        open_digest(show_all=is_history_mode)
    elif index in lookup:
        reel = lookup[index]
        play_in_mpv(reel["reel_id"], reel["url"])


def main() -> None:
    parser = argparse.ArgumentParser(description="WhatsApp Reels Interactive Fuzzel Picker")
    parser.add_argument("-a", "--all", action="store_true", help="Show all reels (opened and unopened)")
    parser.add_argument(
        "-o", "--opened", "--history", dest="opened", action="store_true", help="Show opened/watched reels history"
    )
    args = parser.parse_args()

    reels, prompt, is_history_mode = choose_reels(args)
    if not reels:
        _notify("No reels in database")
        return

    lines, lookup, actions = build_menu(reels, is_history_mode)
    try:
        proc = subprocess.Popen(
            launcher_command(prompt, len(lines)),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        out, _ = proc.communicate(input="\n".join(lines))
        if proc.returncode != 0 or not out.strip():
            return
        handle_selection(int(out.strip()), reels, lookup, actions, is_history_mode)
    except Exception as e:  # top-level guard: this runs from a keybinding, where a traceback goes nowhere
        print(f"[!] Error running {UI.launcher} menu: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
