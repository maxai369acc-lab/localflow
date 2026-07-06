"""Tests for the GitHub Releases auto-updater (updater.py)."""

import io
import json
import zipfile
from pathlib import Path

import pytest

from localflow.updater import UpdateChecker, UpdateInfo, is_newer, validate_staged


# ---- version comparison ------------------------------------------------------

@pytest.mark.parametrize("remote,local,expected", [
    ("0.2.0", "0.1.0", True),
    ("v0.2.0", "0.1.0", True),          # tolerate leading v
    ("0.1.0", "0.1.0", False),
    ("v0.1.0", "0.1.0", False),
    ("0.1.0", "0.2.0", False),          # downgrade is not an update
    ("0.10.0", "0.9.0", True),          # numeric, not lexicographic
    ("1.0", "0.9.9", True),             # missing parts treated as 0
    ("0.2", "0.2.0", False),
    ("garbage", "0.1.0", False),        # unparseable -> never an update
    ("", "0.1.0", False),
])
def test_is_newer(remote, local, expected):
    assert is_newer(remote, local) is expected


# ---- release checking --------------------------------------------------------

def release_json(tag: str, asset_name: str = "LocalFlow-win64.zip") -> str:
    return json.dumps({
        "tag_name": tag,
        "assets": [{
            "name": asset_name,
            "browser_download_url": f"https://example.com/{asset_name}",
            "size": 12345,
        }],
    })


def checker(current="0.1.0", fetch=None):
    return UpdateChecker(repo="someone/localflow", current_version=current,
                         fetch=fetch)


def test_check_finds_newer_release():
    c = checker(fetch=lambda url: release_json("v0.2.0"))
    info = c.check()
    assert info == UpdateInfo(version="0.2.0",
                              url="https://example.com/LocalFlow-win64.zip",
                              size=12345)


def test_check_queries_the_right_endpoint():
    seen = {}

    def fetch(url):
        seen["url"] = url
        return release_json("v0.2.0")

    checker(fetch=fetch).check()
    assert seen["url"] == "https://api.github.com/repos/someone/localflow/releases/latest"


def test_check_returns_none_when_up_to_date():
    assert checker(current="0.2.0",
                   fetch=lambda url: release_json("v0.2.0")).check() is None


def test_check_returns_none_when_remote_older():
    assert checker(current="0.3.0",
                   fetch=lambda url: release_json("v0.2.0")).check() is None


def test_check_returns_none_without_windows_asset():
    c = checker(fetch=lambda url: release_json("v9.9.9", asset_name="LocalFlow-mac.zip"))
    assert c.check() is None


def test_check_swallows_network_errors():
    def fetch(url):
        raise OSError("no wifi")

    assert checker(fetch=fetch).check() is None


def test_check_swallows_junk_json():
    assert checker(fetch=lambda url: "<html>rate limited</html>").check() is None


# ---- staged folder validation ------------------------------------------------

def make_staged(tmp_path: Path, with_exe: bool) -> Path:
    staged = tmp_path / "staged" / "LocalFlow"
    staged.mkdir(parents=True)
    if with_exe:
        (staged / "LocalFlow.exe").write_bytes(b"MZ fake exe")
        (staged / "_internal").mkdir()
    return staged


def test_validate_staged_accepts_complete_folder(tmp_path):
    staged = make_staged(tmp_path, with_exe=True)
    assert validate_staged(staged) is True


def test_validate_staged_rejects_folder_without_exe(tmp_path):
    staged = make_staged(tmp_path, with_exe=False)
    assert validate_staged(staged) is False


def test_validate_staged_rejects_missing_folder(tmp_path):
    assert validate_staged(tmp_path / "nope") is False
