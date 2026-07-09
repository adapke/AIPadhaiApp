"""prod-217 — unit coverage for the YouTube Data API curator's pure logic.

The network path needs a live YT_API_KEY (and the YouTube Data API v3 enabled
on the key's project), so it isn't exercised here. These tests pin the
network-free pieces: ISO-8601 duration parsing, placeholder-URL parsing, and
the missing-key guard.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "youtube_curate", REPO_ROOT / "scripts" / "youtube_curate.py",
)
yc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(yc)


@pytest.mark.parametrize("iso,secs", [
    ("PT1H2M3S", 3723),
    ("PT45S", 45),
    ("PT10M", 600),
    ("PT2H", 7200),
    ("", None),
    ("garbage", None),
])
def test_iso_to_sec(iso, secs):
    assert yc._iso_to_sec(iso) == secs


def test_placeholder_parse():
    m = yc._PLACEHOLDER.search(
        "https://www.youtube.com/@PeekabooKidz/search?query=gravity",
    )
    assert m is not None
    assert m.group(1) == "PeekabooKidz"
    assert m.group(2) == "gravity"


def test_placeholder_does_not_match_real_video():
    assert yc._PLACEHOLDER.search("https://www.youtube.com/watch?v=CMiPYHNNg28") is None


def test_missing_key_returns_2(monkeypatch):
    monkeypatch.delenv("YT_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["youtube_curate.py", "--dry-run"])
    assert yc.main() == 2
