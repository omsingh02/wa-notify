"""
site/vercel.json `ignoreCommand`: skip a Vercel build only when it is certain that site/ did not change.

Vercel runs the command in the project's Root Directory (site/) of a shallow clone (depth 10); exit code 0 cancels
the deployment, anything else builds it. Skipping by mistake would silently freeze the landing page, so every
doubtful case below must build. Needs only git and sh.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMMAND = json.loads((REPO / "site" / "vercel.json").read_text(encoding="utf-8"))["ignoreCommand"]
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, env={**os.environ, **GIT_ENV}, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


@pytest.fixture(scope="module")
def clone(tmp_path_factory):
    """A shallow clone like Vercel's: c1 and c2 are older than the clone depth, so they are missing from it."""
    root = tmp_path_factory.mktemp("vercel")
    origin = root / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    shas = {}

    def commit(label: str, path: str, text: str) -> None:
        target = origin / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        git(origin, "add", "-A")
        git(origin, "commit", "-q", "-m", label)
        shas[label] = git(origin, "rev-parse", "HEAD")

    commit("c1", "site/index.html", "first page")
    commit("c2", "README.md", "readme 2")
    commit("c3", "docs/guide.md", "guide")
    commit("c4", "site/index.html", "page changed")
    commit("c5", "README.md", "readme 5")
    commit("c6", "README.md", "readme 6")
    git(root, "clone", "-q", "--depth=4", f"file://{origin}", str(root / "clone"))
    return root / "clone", shas


def run_in(cwd: Path, previous: str, commit: str) -> int:
    env = {**os.environ, **GIT_ENV, "VERCEL_GIT_PREVIOUS_SHA": previous, "VERCEL_GIT_COMMIT_SHA": commit}
    return subprocess.run(["sh", "-c", COMMAND], cwd=cwd, env=env, capture_output=True, text=True).returncode


CASES = [
    ("only README changed since the last deployment", "c5", "c6", True),
    ("several commits, none of them touching site/", "c4", "c6", True),
    ("site/ changed in between", "c3", "c6", False),
    ("first deployment of a branch (empty previous SHA)", "", "c6", False),
    ("redeploy of the commit that is already live", "c6", "c6", False),
    ("previous commit is older than the shallow clone", "c1", "c6", False),
    ("previous commit unknown to git (history was rewritten)", "0123456789abcdef0123456789abcdef01234567", "c6", False),
    ("commit SHA missing", "c5", "", False),
]


@pytest.mark.parametrize(("why", "previous", "commit", "skips"), CASES, ids=[case[0] for case in CASES])
def test_ignore_command_skips_only_when_site_is_unchanged(clone, why, previous, commit, skips):
    repo, shas = clone
    code = run_in(repo / "site", shas.get(previous, previous), shas.get(commit, commit))
    assert (code == 0) is skips, f"{why}: exit code {code}"


def test_ignore_command_builds_when_git_is_unavailable(tmp_path):
    """Not inside a repository (or git cannot answer): never skip."""
    assert run_in(tmp_path, "a" * 40, "b" * 40) != 0
