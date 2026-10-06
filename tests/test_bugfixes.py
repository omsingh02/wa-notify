"""Regression tests for bugs found in the audit (each one failed before its fix)."""

from __future__ import annotations

import email.message
import io
import os
import json
import subprocess
import sys
import urllib.error

import pytest
from conftest import load_script


# ---------------------------------------------------------------- digest: Rich markup injection
@pytest.fixture(scope="module")
def digest(tools):
    pytest.importorskip("rich")
    return load_script("wa-reel-digest.py")


def add_reel(tools, rid, sender, summary, title=""):
    with tools.walib.get_db() as c:
        c.execute("insert or replace into reels(reel_id,sender,url,title,timestamp,first_seen_at,summary) values(?,?,?,?,?,?,?)",
                  (rid, sender, f"https://www.instagram.com/reel/{rid}", title, 1_700_000_000, 1_700_000_000_000, summary))


def test_preview_survives_markup_in_sender_and_summary(tools, digest, capsys):
    add_reel(tools, "MARKUP000001", "Mallory [/x]", "A caption reading [/INST] then [bold]loud[/bold] [chorus] la la")
    digest.render_preview("MARKUP000001")  # used to raise rich.errors.MarkupError
    out = capsys.readouterr().out
    assert "Mallory [/x]" in out and "[/INST]" in out and "[chorus]" in out and "[bold]loud[/bold]" in out


def test_table_survives_markup_too(tools, digest, capsys):
    add_reel(tools, "MARKUP000002", "Eve [/oops]", "text with [/INST] inside")
    digest.print_rich_table([{"reel_id": "MARKUP000002", "sender": "Eve [/oops]", "summary": "text with [/INST] inside",
                              "timestamp": 1_700_000_000}], "Reels")
    out = capsys.readouterr().out
    assert "Eve [/oops]" in out and "[/INST]" in out


# ---------------------------------------------------------------- digest: --float must never relaunch itself
@pytest.mark.parametrize(
    ("argv", "expected_child"),
    [(["-f"], []), (["-fa"], ["--all"]), (["-fn"], ["--notify"]), (["--fl"], []), (["--flo", "-a"], ["--all"]),
     (["-f", "-l", "10"], ["--limit", "10"]), (["--float", "--opened", "--table"], ["--opened", "--table"])],
)
def test_float_child_never_refloats(digest, monkeypatch, argv, expected_child):
    launched = []
    monkeypatch.setattr(digest, "launch_floating", launched.append)
    monkeypatch.setattr(sys, "argv", ["wa-reel-digest", *argv])
    digest.main()
    assert launched == [expected_child]
    assert not any(a.startswith("-f") or a.startswith("--fl") for a in launched[0])


def test_launch_floating_uses_terminal_then_fallback(digest, monkeypatch):
    seen = []

    def popen(cmd, *a, **k):
        seen.append(cmd)
        if cmd[0] == "footclient":
            raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(digest.subprocess, "Popen", popen)
    digest.launch_floating(["--all"])
    assert [c[0] for c in seen] == ["footclient", "foot"] and seen[1][-1] == "--all" and seen[1][3] == sys.executable


# ---------------------------------------------------------------- alert: is_fast_network
class Result:
    def __init__(self, out="", rc=0):
        self.stdout, self.returncode = out, rc


@pytest.fixture(scope="module")
def alert(tools):
    return load_script("wa-reel-alert.py")


def fake_net(metered, wifi, ping_rc):
    def run(cmd, **kw):
        if "GENERAL.METERED" in cmd:
            return Result(metered)
        if "IN-USE,RATE" in cmd:
            return Result(wifi)
        if cmd[0] == "ping":
            return Result("", ping_rc)
        raise AssertionError(cmd)
    return run


@pytest.mark.parametrize(
    ("metered", "wifi", "ping_rc", "expected"),
    [
        ("GENERAL.METERED:no\n", "*:300 Mbit/s\n", 1, True),  # fast Wi-Fi: no need to ping
        ("GENERAL.METERED:no\n", "*:2 Mbit/s\n", 0, False),  # slow Wi-Fi used to fall through to ping -> True
        ("GENERAL.METERED:no\n", "*:25 Mbit/s\n", 0, True),  # boundary
        ("GENERAL.METERED:no\n", "", 0, True),  # wired / unknown: online is enough
        ("GENERAL.METERED:no\n", "", 1, False),  # ...and offline is not
        ("GENERAL.METERED:yes\n", "*:300 Mbit/s\n", 0, False),
        ("GENERAL.METERED:guess-yes\n", "*:300 Mbit/s\n", 0, False),
    ],
)
def test_is_fast_network(alert, monkeypatch, metered, wifi, ping_rc, expected):
    monkeypatch.setattr(alert.subprocess, "run", fake_net(metered, wifi, ping_rc))
    assert alert.is_fast_network() is expected


def test_missing_nmcli_does_not_crash(alert, monkeypatch):
    def run(cmd, **kw):
        if cmd[0] == "nmcli":
            raise FileNotFoundError("nmcli")
        return Result("", 0)
    monkeypatch.setattr(alert.subprocess, "run", run)
    assert alert.is_fast_network() is True


# ---------------------------------------------------------------- wa_reel_ai: API key parsing
def test_secrets_parser(tools):
    p = tools.ai.parse_secrets
    assert p("# GEMINI_API_KEY_REELS=OLD_REVOKED\nGEMINI_API_KEY_REELS=NEW\n") == {"GEMINI_API_KEY_REELS": "NEW"}
    assert p("GEMINI_API_KEY_REELS=NEW   # rotated 2026\n") == {"GEMINI_API_KEY_REELS": "NEW"}
    assert p('export GEMINI_API_KEY_REELS="quoted value" # note\n') == {"GEMINI_API_KEY_REELS": "quoted value"}
    assert p("GEMINI_API_KEY='single'\n") == {"GEMINI_API_KEY": "single"}
    assert p("GEMINI_API_KEY_REELS=first\nGEMINI_API_KEY_REELS=second\n") == {"GEMINI_API_KEY_REELS": "first"}
    assert p("OTHER=1\n\n   \n") == {}


def test_get_api_key_precedence(tools, monkeypatch, tmp_path):
    ai = tools.ai
    secrets = tmp_path / "secrets"
    secrets.write_text("# GEMINI_API_KEY_REELS=old\nGEMINI_API_KEY=generic  # shared\n")
    monkeypatch.setattr(ai, "SECRETS_FILE", str(secrets))
    for name in ai.KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    assert ai.get_api_key() == "generic"  # the generic name now works from the file too
    secrets.write_text("GEMINI_API_KEY_REELS=reels\nGEMINI_API_KEY=generic\n")
    assert ai.get_api_key() == "reels"
    monkeypatch.setenv("GEMINI_API_KEY", " 'from-env' ")
    monkeypatch.setenv("GEMINI_API_KEY_REELS", "reels-env")
    assert ai.get_api_key() == "reels-env"
    monkeypatch.delenv("GEMINI_API_KEY_REELS")
    assert ai.get_api_key() == "from-env"
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setattr(ai, "SECRETS_FILE", str(tmp_path / "missing"))
    assert ai.get_api_key() is None


# ---------------------------------------------------------------- wa_reel_ai: retry loop
class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, reason, body=b"", retry_after=None):
    hdrs = email.message.Message()
    if retry_after is not None:
        hdrs["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://example.test", code, reason, hdrs, io.BytesIO(body))


@pytest.fixture
def api(tools, monkeypatch):
    """Scripted urlopen: a list of responses (payload dict -> 200, exception -> raised); records calls and sleeps."""
    state = {"script": [], "calls": [], "sleeps": []}

    def urlopen(req, timeout=None):
        state["calls"].append(req.full_url.rsplit("/", 1)[-1].split(":")[0])
        item = state["script"].pop(0)
        if isinstance(item, BaseException):
            raise item
        return FakeResponse(item)

    monkeypatch.setattr(tools.ai.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(tools.ai.time, "sleep", state["sleeps"].append)
    return state


GOOD = {"candidates": [{"content": {"parts": [{"text": " A summary. "}]}}]}
BLOCKED = {"candidates": [{"finishReason": "SAFETY"}], "promptFeedback": {"blockReason": "OTHER"}}


def test_200_without_text_is_not_retried_and_says_why(tools, api, capsys):
    api["script"] = [BLOCKED]
    assert tools.ai.generate_summary("m1", b"{}", "key") is None
    assert len(api["calls"]) == 1  # used to repeat the identical request
    assert "blockReason=OTHER" in capsys.readouterr().err


def test_empty_candidates_logged(tools, api, capsys):
    api["script"] = [{}]
    assert tools.ai.generate_summary("m1", b"{}", "key") is None
    assert "no candidates" in capsys.readouterr().err


def test_429_retries_once_then_succeeds(tools, api):
    api["script"] = [http_error(429, "Too Many Requests"), GOOD]
    assert tools.ai.generate_summary("m1", b"{}", "key") == "A summary."
    assert len(api["calls"]) == 2 and api["sleeps"] == [tools.ai.DEFAULT_RETRY_DELAY]


def test_retry_after_header_is_honoured_and_capped(tools, api):
    api["script"] = [http_error(503, "Unavailable", retry_after=7), GOOD]
    tools.ai.generate_summary("m1", b"{}", "key")
    api["script"] = [http_error(503, "Unavailable", retry_after=999), GOOD]
    tools.ai.generate_summary("m1", b"{}", "key")
    assert api["sleeps"] == [7.0, tools.ai.MAX_RETRY_DELAY]


def test_persistent_429_logs_the_error_body(tools, api, capsys):
    body = b'{"error": {"message": "Quota exceeded for metric generate_content_free_tier_requests"}}'
    api["script"] = [http_error(429, "Too Many Requests", body), http_error(429, "Too Many Requests", body)]
    assert tools.ai.generate_summary("m1", b"{}", "key") is None
    err = capsys.readouterr().err
    assert len(api["calls"]) == 2
    assert "HTTP 429: Too Many Requests" in err and "Quota exceeded" in err  # the reason used to be discarded


def test_error_body_is_bounded(tools, api, capsys):
    api["script"] = [http_error(400, "Bad Request", b"x" * 5000)]
    tools.ai.generate_summary("m1", b"{}", "key")
    assert len(capsys.readouterr().err) < 500


def test_non_retryable_status_is_not_retried(tools, api):
    api["script"] = [http_error(400, "Bad Request", b"nope")]
    assert tools.ai.generate_summary("m1", b"{}", "key") is None
    assert len(api["calls"]) == 1 and api["sleeps"] == []


def test_network_errors_get_one_retry(tools, api):
    api["script"] = [urllib.error.URLError("dns"), urllib.error.URLError("dns")]
    assert tools.ai.generate_summary("m1", b"{}", "key") is None
    assert len(api["calls"]) == 2 and api["sleeps"] == [1.5]
    api["calls"].clear()
    api["script"] = [TimeoutError("slow"), GOOD]
    assert tools.ai.generate_summary("m1", b"{}", "key") == "A summary."


def test_cascade_costs_one_call_per_model_when_blocked(tools, api, monkeypatch, tmp_path):
    ai = tools.ai
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 20000)
    monkeypatch.setattr(ai, "get_local_reel_file", lambda rid: str(video))
    monkeypatch.setattr(ai, "get_cached_summary", lambda rid: None)
    monkeypatch.setattr(ai, "get_api_key", lambda: "key")
    saved = []
    monkeypatch.setattr(ai, "save_summary", lambda rid, text: saved.append((rid, text)))
    api["script"] = [BLOCKED, BLOCKED]
    assert ai.summarize_reel("REELX") is None
    assert api["calls"] == list(ai.AI.models)  # one call per model, in the configured order (was 4)
    api["calls"].clear()
    api["script"] = [BLOCKED, GOOD]  # the lite model answers
    assert ai.summarize_reel("REELX") == "A summary." and saved == [("REELX", "A summary.")]


def test_downscale_uses_a_private_temp_dir_and_cleans_up(tools, monkeypatch, tmp_path):
    ai = tools.ai
    big = tmp_path / "big.mp4"
    big.write_bytes(b"0" * (ai.MAX_INLINE_BYTES + 1))
    seen = {}

    def run(cmd, **kw):
        seen["target"] = cmd[-1]
        open(cmd[-1], "wb").write(b"small")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ai.subprocess, "run", run)
    assert ai.prepare_video_bytes(str(big), "REELY") == b"small"
    assert seen["target"] != "/tmp/wa_reel_opt_REELY.mp4" and not os.path.exists(seen["target"])
