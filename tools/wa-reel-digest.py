#!/usr/bin/env python3
"""
wa-reel-digest — Minimalist Interactive TUI & Digest Viewer for WhatsApp Reels (v2)

Features:
- Clean 2-pane FZF layout: Left list shows Channel/Sender + Time; Right pane displays the AI summary.
- Zero clutter: No redundant database IDs, raw file paths, or duplicated text snippets.
- Responsive layout adapting cleanly to centered floating terminals (1000x600).
- Instant shortcuts: Enter to play in MPV, Ctrl-O browser, Ctrl-Y copy URL, Ctrl-D download.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys

TOOLS_DIR = os.path.dirname(os.path.realpath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from wa_reel_dl import download_reel  # noqa: E402
from walib import (  # noqa: E402
    UI,
    copy_to_clipboard,
    format_time,
    get_all_reels,
    get_db,
    get_local_reel_file,
    get_opened_reels,
    get_unopened_reels,
    play_in_mpv,
)

try:
    from rich.console import Console
    from rich.markup import escape
    from rich.table import Table

    HAS_RICH = True
except ImportError:
    HAS_RICH = False

DEFAULT_LIMIT = 50

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_CYAN = "\033[1;36m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_WHITE = "\033[37m"


def render_preview(reel_id: str):
    """Renders clean, substance-focused preview for the preview pane."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM reels WHERE reel_id = ?", (reel_id,)).fetchone()
        if not row:
            print(f"Reel {reel_id} not found.")
            return
        r = dict(row)

    sender = r.get("sender") or "Unknown"
    ts = r.get("timestamp") or (r.get("first_seen_at", 0) // 1000)
    _, rel = format_time(ts)
    local_path = get_local_reel_file(reel_id)

    status = "[yellow]cached[/]" if local_path else "[dim]stream[/]"
    summary = (r.get("summary") or r.get("title") or "(No summary available)").strip()

    if HAS_RICH:
        console = Console(highlight=False)
        # sender/summary/title are untrusted text (display names, LLM output): escape them so "[/x]" can't break the markup
        console.print(f"\n[bold cyan]{escape(sender)}[/]  [dim]|[/]  [green]{rel}[/]  [dim]|[/]  {status}\n")
        console.print(f"[white]{escape(summary)}[/]\n")
    else:
        print(f"\n{sender} | {rel} | {'cached' if local_path else 'stream'}\n")
        print(f"{summary}\n")


def print_rich_table(reels, title: str):
    """Prints a styled terminal table using Rich for non-interactive output."""
    console = Console()
    max_sender_len = max(len(r.get("sender") or "Unknown") for r in reels)
    sender_width = max(max_sender_len, 20)

    table = Table(title=title, show_header=True, header_style="bold cyan", border_style="dim")
    table.add_column("#", style="dim", justify="right", width=3)
    table.add_column("Channel / Sender", style="bold cyan", width=sender_width)
    table.add_column("Time", style="green", width=10)
    table.add_column("Status", justify="center", width=10)
    table.add_column("Summary / Title", style="white")

    for idx, r in enumerate(reels, 1):
        sender = r.get("sender") or "Unknown"
        reel_id = r.get("reel_id") or ""
        ts = r.get("timestamp") or (r.get("first_seen_at", 0) // 1000)
        _, rel = format_time(ts)
        local_path = get_local_reel_file(reel_id)

        status = "[yellow]cached[/]" if local_path else "[dim]stream[/]"
        summary = (r.get("summary") or r.get("title") or "(No summary)").replace("\n", " ")
        if len(summary) > 75:
            summary = summary[:72] + "..."

        table.add_row(str(idx), escape(sender), rel, status, escape(summary))

    console.print(table)


def run_fzf_ui(reels, prompt_title: str):
    """Runs clean, fully responsive FZF interface with adaptive layout and zero icon clutter."""
    import shutil

    lines = []
    lookup = {}

    term_cols, term_lines = shutil.get_terminal_size((100, 24))

    # Adaptive preview: In floating windows (1000x600) or zoomed terminals (<135 cols),
    # place preview at bottom so BOTH list and preview get 100% horizontal width (zero '..' cutoffs).
    # On wide/maximized screens (>=135 cols), place preview on right side.
    preview_layout = "down:50%:border-top:wrap" if term_cols < 135 else "right:52%:border-left:wrap"

    max_sender_len = max(len(r.get("sender") or "Unknown") for r in reels)
    sender_col_width = max(max_sender_len, 20) + 1

    for r in reels:
        reel_id = r.get("reel_id") or ""
        sender = (r.get("sender") or "Unknown").strip()
        ts = r.get("timestamp") or (r.get("first_seen_at", 0) // 1000)
        _, rel = format_time(ts)
        local_path = get_local_reel_file(reel_id)

        status_txt = "cached" if local_path else ""

        col_sender = f"{C_CYAN}{sender:<{sender_col_width}}{C_RESET}"
        col_rel = f"{C_GREEN}{rel[:7]:<7}{C_RESET}"
        col_status = f"{C_YELLOW}{status_txt:<6}{C_RESET}" if local_path else f"{'':<6}"

        # Clean row: Sender (cyan) + Rel Time (green) + Status (yellow). Zero emojis, zero icons.
        line = f"{reel_id}\t{col_sender}  {col_rel}  {col_status}"
        lines.append(line)
        lookup[reel_id] = r

    fzf_input = "\n".join(lines)
    script_path = os.path.realpath(__file__)

    fzf_cmd = [
        "fzf",
        "--ansi",
        "--delimiter=\t",
        "--with-nth=2..",
        "--preview",
        f"{shlex.quote(sys.executable)} {shlex.quote(script_path)} --preview {{1}}",
        f"--preview-window={preview_layout}",
        "--bind=ctrl-w:change-preview-window(down,50%|right,52%|hidden)",
        "--bind=ctrl-/:toggle-preview",
        "--footer=enter: play | ^o: browser | ^y: copy | ^d: download | ^w: layout | esc: exit",
        "--footer-border=none",
        "--expect=ctrl-o,ctrl-y,ctrl-d,enter",
        f"--prompt={prompt_title} > ",
        "--pointer=> ",
        "--marker=*",
        "--gutter= ",
        "--border=none",
        "--layout=reverse",
        "--no-info",
        "--no-separator",
        "--no-scrollbar",
        "--color=footer:dim,prompt:bold:cyan,pointer:bold:cyan,hl:yellow,hl+:bright-yellow,bg+:-1,fg+:bold:white",
    ]

    while True:
        try:
            try:
                proc = subprocess.Popen(
                    fzf_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
                )
            except FileNotFoundError:
                print("[!] fzf is not installed — use --table for plain output", file=sys.stderr)
                break
            out, _ = proc.communicate(input=fzf_input)
            if proc.returncode != 0 or not out.strip():
                break

            out_lines = out.strip().split("\n")
            if not out_lines:
                break

            key_pressed = out_lines[0].strip() if len(out_lines) > 1 else "enter"
            selected_line = out_lines[1] if len(out_lines) > 1 else out_lines[0]
            selected_reel_id = selected_line.split("\t")[0].strip()

            if selected_reel_id not in lookup:
                break

            selected_reel = lookup[selected_reel_id]
            url = selected_reel.get("url") or f"https://www.instagram.com/reel/{selected_reel_id}"

            if key_pressed == "ctrl-o":
                print(f"[*] Opening in browser: {url}")
                subprocess.Popen(
                    ["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
                )
            elif key_pressed == "ctrl-y":
                copy_to_clipboard(url)
                print(f"[*] Copied to clipboard: {url}")
                subprocess.run(
                    ["notify-send", "-a", UI.notify_app_name, "-i", "instagram", "-u", "low", "Link Copied", url],
                    check=False,
                )
            elif key_pressed == "ctrl-d":
                print(f"[*] Downloading {selected_reel_id}...")
                success, path = download_reel(selected_reel_id, quiet=False)
                if success:
                    subprocess.run(
                        [
                            "notify-send",
                            "-a",
                            UI.notify_app_name,
                            "-i",
                            "instagram",
                            "Download Complete",
                            f"Saved {selected_reel_id}",
                        ],
                        check=False,
                    )
            else:  # default or enter
                play_in_mpv(selected_reel_id, url)
                break
        except (KeyboardInterrupt, EOFError):
            break


def child_argv(args: argparse.Namespace) -> list[str]:
    """
    Arguments for the relaunched (floating) copy, rebuilt from the PARSED options so the child can
    never re-float. Stripping "-f" out of sys.argv misses "-fa" and abbreviations such as "--fl",
    which made the child relaunch itself forever.
    """
    out: list[str] = []
    if args.all:
        out.append("--all")
    if args.opened:
        out.append("--opened")
    if args.table:
        out.append("--table")
    if args.notify:
        out.append("--notify")
    if args.limit != DEFAULT_LIMIT:
        out += ["--limit", str(args.limit)]
    return out


def launch_floating(argv: list[str]) -> None:
    """Run this script in the floating terminal (ui.terminal), falling back to ui.terminal_fallback."""
    tail = [sys.executable, os.path.realpath(__file__), *argv]
    try:
        subprocess.Popen([*UI.terminal, *tail])
    except OSError:
        subprocess.Popen([*UI.terminal_fallback, *tail])


def main() -> None:
    parser = argparse.ArgumentParser(description="WhatsApp Instagram Reels Digest & Interactive Viewer")
    parser.add_argument("-a", "--all", action="store_true", help="Show all reels (opened and unopened)")
    parser.add_argument("-o", "--opened", action="store_true", help="Show already opened/watched reels history")
    parser.add_argument("-f", "--float", action="store_true", help="Launch in centered floating window (1000x600)")
    parser.add_argument(
        "-t", "--table", action="store_true", help="Force static table output instead of interactive FZF"
    )
    parser.add_argument("-n", "--notify", action="store_true", help="Send desktop notification summary")
    parser.add_argument("-l", "--limit", type=int, default=DEFAULT_LIMIT, help="Maximum number of reels to display")
    parser.add_argument("--preview", type=str, metavar="REEL_ID", help=argparse.SUPPRESS)
    args = parser.parse_args()

    # Re-launch in the floating terminal if --float requested
    if args.float:
        launch_floating(child_argv(args))
        return

    # Hidden flag for fzf live preview pane
    if args.preview:
        render_preview(args.preview)
        return

    if args.opened:
        reels = get_opened_reels(limit=args.limit)
        title = f"Watched Reels ({len(reels)})"
    elif args.all:
        reels = get_all_reels(limit=args.limit)
        title = f"Reels Archive ({len(reels)})"
    else:
        reels = get_unopened_reels()
        title = f"Unopened Reels ({len(reels)})"
        if not reels:
            reels = get_opened_reels(limit=args.limit)
            title = f"Reels ({len(reels)})"

    if not reels:
        if args.notify:
            subprocess.run(
                ["notify-send", "-a", UI.notify_app_name, "-i", "instagram", "Reels Digest", "No reels found."],
                check=False,
            )
        else:
            print("\nNo reels found in database.")
        return

    if args.notify:
        for r in reels[:5]:
            sender = r.get("sender") or "Unknown"
            summary = r.get("summary") or r.get("title") or "(No summary)"
            ts = r.get("timestamp") or 0
            time_str, _ = format_time(ts)
            subprocess.run(
                [
                    "notify-send",
                    "-a",
                    UI.notify_app_name,
                    "-i",
                    "instagram",
                    f"Reel from {sender} ({time_str})",
                    summary,
                ],
                check=False,
            )
        return

    # If user wants table or stdout is piped, print table
    if args.table or not sys.stdin.isatty():
        if HAS_RICH:
            print_rich_table(reels, title)
        else:
            for idx, r in enumerate(reels, 1):
                sender = r.get("sender") or "Unknown"
                summary = r.get("summary") or r.get("title") or "(No summary)"
                print(f"[{idx}] {sender}: {summary}")
        return

    # Interactive FZF UI
    run_fzf_ui(reels, title)


if __name__ == "__main__":
    main()
