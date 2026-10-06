"""
Shared pytest fixtures.

Every test module gets its own sandbox: a temporary HOME / XDG_RUNTIME_DIR / data dir, fake external tools
(yt-dlp, mpv, notify-send, xdg-open, pkill, fuzzel) first on PATH, and freshly imported copies of the tools/
modules (they read their paths from the environment at import time). Nothing outside the temp dir is touched.

Tests inside one module may share state (a DB, cached files) and then rely on running in file order.
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import shutil
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
STUBS = Path(__file__).parent / "stubs"
TOOL_MODULES = ("wa_settings", "walib", "wa_reel_auth", "wa_reel_dl", "wa_reel_queue", "wa_reel_ai")
SANDBOX_ENV_REMOVE = ("WA_NOTIFY_DATA_DIR", "WA_NOTIFY_CONFIG_DIR", "GEMINI_API_KEY", "GEMINI_API_KEY_REELS")


class Checks:
    """Collects named checks so one test can assert many things and report every failure at once."""

    def __init__(self) -> None:
        self.failed: list[str] = []
        self.total = 0

    def __call__(self, name: str, condition: object, extra: object = "") -> None:
        self.total += 1
        if not condition:
            self.failed.append(f"{name}   [{extra}]" if extra else name)

    def assert_all(self) -> None:
        assert not self.failed, f"{len(self.failed)}/{self.total} checks failed:\n  " + "\n  ".join(self.failed)


@pytest.fixture
def check() -> Checks:
    return Checks()


class Sandbox(SimpleNamespace):
    """Paths and helpers for the fake tools."""

    def set_mode(self, mode: str) -> None:
        """Behaviour of the fake yt-dlp ('a,b,c' = one mode per call, the last one sticks)."""
        self.mode_file.write_text(mode)

    def reset_log(self) -> None:
        self.log.write_text("")

    def calls(self, tool: str | None = None) -> list[dict]:
        rows = [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]
        return [r for r in rows if tool is None or r["tool"] == tool]


def _purge_tool_modules() -> None:
    for name in TOOL_MODULES:
        sys.modules.pop(name, None)


@pytest.fixture(scope="module")
def imgserver(tmp_path_factory):
    """Local HTTP server with small/big fake images (img1_big.jpg ...), standing in for Instagram's CDN."""
    root = tmp_path_factory.mktemp("imgsrv")
    for n in (1, 2, 3):
        for size, length in (("small", 1500), ("big", 3000)):
            (root / f"img{n}_{size}.jpg").write_bytes(os.urandom(length))
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None  # type: ignore[attr-defined]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield SimpleNamespace(port=server.server_address[1])
    server.shutdown()


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory, request):
    """Isolated environment + freshly imported tools; see the module docstring."""
    mp = pytest.MonkeyPatch()
    root = tmp_path_factory.mktemp("home")
    bin_dir = root / "stubs-bin"
    bin_dir.mkdir()
    for stub in STUBS.iterdir():  # copy so the exec bit never depends on how the repo was checked out
        shutil.copy(stub, bin_dir / stub.name)
        (bin_dir / stub.name).chmod(0o755)
    run_dir = root / "run"
    run_dir.mkdir()

    for name in SANDBOX_ENV_REMOVE:
        mp.delenv(name, raising=False)
    mp.setenv("HOME", str(root))
    mp.setenv("XDG_RUNTIME_DIR", str(run_dir))
    mp.setenv("STUB_LOG", str(root / "calls.log"))
    mp.setenv("FAKE_MODE_FILE", str(root / "mode"))
    mp.setenv("PYTHONDONTWRITEBYTECODE", "1")
    mp.setenv("FAKE_DELAY", "0")
    mp.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    if "imgserver" in request.fixturenames:
        mp.setenv("IMG_PORT", str(request.getfixturevalue("imgserver").port))
    mp.syspath_prepend(str(TOOLS))
    _purge_tool_modules()

    box = Sandbox(
        home=root,
        run=run_dir,
        log=root / "calls.log",
        mode_file=root / "mode",
        config_dir=root / ".config" / "wa-notify",
        data_dir=root / ".local" / "share" / "wa-notify",
        tools_dir=TOOLS,
        bin_dir=bin_dir,
    )
    box.log.write_text("")
    box.mode_file.write_text("ok")
    yield box
    mp.undo()
    _purge_tool_modules()


@pytest.fixture(scope="module")
def tools(sandbox) -> SimpleNamespace:
    """Freshly imported tools modules (paths resolved inside the sandbox) with the DB initialised."""
    import wa_reel_ai
    import wa_reel_auth
    import wa_reel_dl
    import wa_reel_queue
    import wa_settings
    import walib

    walib.init_db()
    return SimpleNamespace(
        settings=wa_settings, walib=walib, dl=wa_reel_dl, auth=wa_reel_auth, queue=wa_reel_queue, ai=wa_reel_ai
    )


def load_script(name: str):
    """Import a dashed CLI script (wa-reel-alert.py ...) from tools/ as a module."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("script_" + name.replace("-", "_").removesuffix(".py"), TOOLS / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
