"""wa_settings: defaults equal the historical hard-coded values; config.toml and env override them safely."""

from __future__ import annotations

from pathlib import Path

import pytest


def make_env(tmp_path: Path, toml: str | None = None, **extra: str) -> dict[str, str]:
    cfg = tmp_path / "cfg"
    cfg.mkdir(exist_ok=True)
    if toml is not None:
        (cfg / "config.toml").write_text(toml)
    return {"HOME": str(tmp_path), "WA_NOTIFY_CONFIG_DIR": str(cfg), "WA_NOTIFY_DATA_DIR": str(tmp_path / "data"), **extra}


def test_defaults_match_the_original_hard_coded_values(tools, tmp_path):
    s = tools.settings.load(make_env(tmp_path))
    assert (s.ui.app_id, s.ui.player_size, s.ui.launcher, s.ui.notify_app_name) == ("wa-reel", "420x750", "fuzzel", "wa-reels")
    assert s.ui.launcher_font == "JetBrains Mono NF:size=12"
    assert (s.ui.launcher_width, s.ui.launcher_max_lines) == (125, 14)
    assert s.ui.terminal == ("footclient", "-a", "wa-reel-digest")
    assert s.ui.terminal_fallback == ("foot", "-a", "wa-reel-digest")
    assert s.ai.models == ("gemini-3.5-flash", "gemini-3.5-flash-lite")
    assert (s.ai.max_inline_mb, s.ai.clip_seconds) == (18, 90)


def test_default_directories_follow_home(tools, tmp_path):
    s = tools.settings.load({"HOME": str(tmp_path)})
    assert s.data_dir == tmp_path / ".local/share/wa-notify"
    assert s.config_dir == tmp_path / ".config/wa-notify"
    assert s.db_path == s.data_dir / "wa-notify.db"
    assert s.reels_dir == s.data_dir / "reels" and s.summaries_dir == s.data_dir / "summaries"
    assert s.token_file == s.config_dir / "token.txt"
    assert s.secrets_file == tmp_path / ".config/.secrets"


def test_environment_overrides_directories(tools, tmp_path):
    s = tools.settings.load(make_env(tmp_path))
    assert s.data_dir == tmp_path / "data" and s.config_dir == tmp_path / "cfg"


def test_config_toml_overrides_ui_and_ai(tools, tmp_path):
    toml = """
[ui]
app_id = "my-reel"
player_size = "300x600"
terminal = ["alacritty", "--class", "digest"]
launcher_width = 80
[ai]
models = ["some-model"]
clip_seconds = 30
"""
    s = tools.settings.load(make_env(tmp_path, toml))
    assert s.ui.app_id == "my-reel" and s.ui.player_size == "300x600" and s.ui.launcher_width == 80
    assert s.ui.terminal == ("alacritty", "--class", "digest")
    assert s.ui.launcher == "fuzzel"  # untouched keys keep their default
    assert s.ai.models == ("some-model",) and s.ai.clip_seconds == 30 and s.ai.max_inline_mb == 18


def test_wrongly_typed_values_fall_back_with_one_warning(tools, tmp_path, capsys):
    tools.settings._warned.clear()
    toml = '[ui]\nlauncher_width = "wide"\nterminal = "foot"\nunknown_key = 1\n[ai]\nmodels = []\n'
    s = tools.settings.load(make_env(tmp_path, toml))
    tools.settings.load(make_env(tmp_path, toml))  # second load must not repeat the warnings
    err = capsys.readouterr().err
    assert s.ui.launcher_width == 125 and s.ui.terminal == ("footclient", "-a", "wa-reel-digest") and s.ai.models[0] == "gemini-3.5-flash"
    assert err.count("launcher_width") == 1 and err.count("unknown key [ui] unknown_key") == 1


def test_broken_toml_is_tolerated(tools, tmp_path, capsys):
    tools.settings._warned.clear()
    s = tools.settings.load(make_env(tmp_path, "[ui\nthis is not toml"))
    assert s.ui.app_id == "wa-reel"
    assert "cannot read" in capsys.readouterr().err


def test_missing_config_file_is_silent(tools, tmp_path, capsys):
    tools.settings.load(make_env(tmp_path))
    assert capsys.readouterr().err == ""


def test_walib_exports_are_derived_from_settings(tools):
    w = tools.walib
    assert w.MPV_BASE[1:3] == [f"--wayland-app-id={w.UI.app_id}", f"--title={w.UI.app_id}"]
    assert w.DB_PATH == str(w.SETTINGS.db_path) and w.REELS_DIR == str(w.SETTINGS.reels_dir)
    assert w.TOKEN_FILE.endswith("token.txt") and w.CONFIG_DIR == str(w.SETTINGS.config_dir)


@pytest.mark.parametrize("bad", ["", "   ", 0, -5, True])
def test_coerce_rejects_empty_and_non_positive_values(tools, bad):
    tools.settings._warned.clear()
    default = 125 if not isinstance(bad, str) else "x"
    assert tools.settings._coerce("ui.x", default, bad) == default


def test_example_config_parses_to_the_defaults(tools, tmp_path):
    """tools/config.example.toml documents the defaults; keep it honest."""
    example = (Path(__file__).resolve().parents[1] / "tools" / "config.example.toml").read_text()
    s = tools.settings.load(make_env(tmp_path, example))
    d = tools.settings.load(make_env(tmp_path))
    assert s.ui == d.ui and s.ai == d.ai
