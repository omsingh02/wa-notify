#!/usr/bin/env python3
"""
wa_settings.py — settings for the wa-notify Python tools

Everything that used to be hard-coded (player window, launcher, terminal, AI models, directories)
lives here as a default and can be overridden without touching code:

  environment   WA_NOTIFY_DATA_DIR    (default ~/.local/share/wa-notify)
                WA_NOTIFY_CONFIG_DIR  (default ~/.config/wa-notify)
  config.toml   <config dir>/config.toml, tables [ui] and [ai] (and [instagram], read by wa_reel_auth)

Defaults equal the values the tools shipped with, so nothing changes without a config file.
A missing file is fine; an unreadable one or a wrongly-typed value produces ONE warning and the
default is used instead.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None  # type: ignore[assignment]

ENV_DATA_DIR = "WA_NOTIFY_DATA_DIR"
ENV_CONFIG_DIR = "WA_NOTIFY_CONFIG_DIR"
DEFAULT_DATA_DIR = "~/.local/share/wa-notify"
DEFAULT_CONFIG_DIR = "~/.config/wa-notify"

_warned: set[str] = set()


def _warn_once(msg: str) -> None:
    if msg not in _warned:
        _warned.add(msg)
        print(f"[!] settings: {msg}", file=sys.stderr)


@dataclass(frozen=True)
class UiSettings:
    """Desktop integration. The defaults describe a Hyprland + foot + fuzzel + mako setup."""

    app_id: str = "wa-reel"  # mpv Wayland app id AND window title: compositor rules match it
    player_size: str = "420x750"  # mpv --autofit
    launcher: str = "fuzzel"  # dmenu-style picker (the flags used are fuzzel's)
    launcher_font: str = "JetBrains Mono NF:size=12"
    launcher_width: int = 125
    launcher_max_lines: int = 14
    terminal: tuple[str, ...] = ("footclient", "-a", "wa-reel-digest")
    terminal_fallback: tuple[str, ...] = ("foot", "-a", "wa-reel-digest")
    notify_app_name: str = "wa-reels"  # notify-send -a (mako matches this for theming)


@dataclass(frozen=True)
class AiSettings:
    """Gemini summarizer."""

    models: tuple[str, ...] = ("gemini-3.5-flash", "gemini-3.5-flash-lite")  # tried in order
    secrets_file: str = "~/.config/.secrets"
    max_inline_mb: int = 18  # bigger videos are downscaled with ffmpeg first
    clip_seconds: int = 90  # ...and cut to this length


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    config_dir: Path
    secrets_file: Path  # [ai] secrets_file with ~ expanded
    ui: UiSettings = field(default_factory=UiSettings)
    ai: AiSettings = field(default_factory=AiSettings)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "wa-notify.db"

    @property
    def reels_dir(self) -> Path:
        return self.data_dir / "reels"

    @property
    def summaries_dir(self) -> Path:
        return self.data_dir / "summaries"

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def token_file(self) -> Path:
        return self.config_dir / "token.txt"


def _coerce(name: str, default: Any, value: Any) -> Any:
    """`value` converted to the type of `default`, or the default (with a warning) if it doesn't fit."""
    if isinstance(default, tuple):
        if isinstance(value, (list, tuple)) and value and all(isinstance(v, str) for v in value):
            return tuple(value)
    elif isinstance(default, bool):
        if isinstance(value, bool):
            return value
    elif isinstance(default, int):
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    elif isinstance(default, str) and isinstance(value, str) and value.strip():
        return value
    _warn_once(f"ignoring invalid value for {name!r}: {value!r}")
    return default


def _section(cls: type, table: Any, label: str):
    """Build a settings dataclass from a config table, falling back to defaults key by key."""
    if table is None:
        return cls()
    if not isinstance(table, Mapping):
        _warn_once(f"[{label}] must be a table")
        return cls()
    defaults = cls()
    known = {f.name for f in fields(cls)}
    for key in table:
        if key not in known:
            _warn_once(f"unknown key [{label}] {key}")
    return cls(
        **{
            f.name: _coerce(f"{label}.{f.name}", getattr(defaults, f.name), table[f.name])
            for f in fields(cls)
            if f.name in table
        }
    )


def expand_home(path: str, env: Mapping[str, str] | None = None) -> Path:
    """`~` / `~/x` expanded against $HOME from `env` (default: the process environment)."""
    env = os.environ if env is None else env
    if path == "~" or path.startswith("~/"):
        path = (env.get("HOME") or str(Path.home())) + path[1:]
    return Path(path)


def _read_toml(path: Path) -> Mapping[str, Any]:
    if tomllib is None:
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:  # ValueError covers tomllib.TOMLDecodeError
        _warn_once(f"cannot read {path}: {exc}")
        return {}


def load(env: Mapping[str, str] | None = None) -> Settings:
    """Settings from defaults, the environment and <config dir>/config.toml."""
    env = os.environ if env is None else env
    data_dir = expand_home(env.get(ENV_DATA_DIR) or DEFAULT_DATA_DIR, env)
    config_dir = expand_home(env.get(ENV_CONFIG_DIR) or DEFAULT_CONFIG_DIR, env)
    cfg = _read_toml(config_dir / "config.toml")
    ai = _section(AiSettings, cfg.get("ai"), "ai")
    return Settings(
        data_dir=data_dir,
        config_dir=config_dir,
        secrets_file=expand_home(ai.secrets_file, env),
        ui=_section(UiSettings, cfg.get("ui"), "ui"),
        ai=ai,
    )


if __name__ == "__main__":
    s = load()
    print(
        f"data_dir   : {s.data_dir}\nconfig_dir : {s.config_dir}\nconfig file: {s.config_file} "
        f"({'present' if s.config_file.exists() else 'absent — defaults'})\nui         : {s.ui}\nai         : {s.ai}"
    )
