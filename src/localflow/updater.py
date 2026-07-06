"""Over-the-air updates from GitHub Releases.

Notify-only flow: a background check finds a newer release and surfaces it
in the tray; nothing downloads until the user clicks "Install update".
Installing downloads LocalFlow-win64.zip, stages it under DATA_DIR/updates,
then hands over to a generated swap.cmd that waits for the app to exit,
swaps the install folder (keeping one .backup for rollback) and restarts.

Only packaged builds (sys.frozen) ever check or install. Every network or
filesystem failure degrades to "no update" — the updater must never break
dictation.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("localflow")

ASSET_NAME = "LocalFlow-win64.zip"


def is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False))


def is_newer(remote: str, local: str) -> bool:
    """True if remote version string is strictly newer than local."""
    def parse(v: str) -> tuple[int, ...] | None:
        v = v.strip().lstrip("vV")
        try:
            parts = tuple(int(p) for p in v.split("."))
        except ValueError:
            return None
        return parts + (0,) * (3 - len(parts))

    r, l = parse(remote), parse(local)
    if r is None or l is None:
        return False
    return r > l


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str
    size: int


def _http_get(url: str) -> str:
    import requests

    resp = requests.get(url, timeout=10,
                        headers={"Accept": "application/vnd.github+json"})
    resp.raise_for_status()
    return resp.text


class UpdateChecker:
    def __init__(self, repo: str, current_version: str, fetch=None):
        self.repo = repo
        self.current = current_version
        self._fetch = fetch or _http_get

    def check(self) -> UpdateInfo | None:
        try:
            raw = self._fetch(f"https://api.github.com/repos/{self.repo}/releases/latest")
            data = json.loads(raw)
            tag = str(data.get("tag_name", ""))
            if not is_newer(tag, self.current):
                return None
            for asset in data.get("assets", []):
                if asset.get("name") == ASSET_NAME:
                    return UpdateInfo(version=tag.lstrip("vV"),
                                      url=asset["browser_download_url"],
                                      size=int(asset.get("size", 0)))
            log.info("release %s has no %s asset", tag, ASSET_NAME)
            return None
        except Exception as e:
            log.debug("update check failed: %s", e)
            return None


def validate_staged(staged: Path) -> bool:
    """A staged update must at least contain the main executable."""
    return (Path(staged) / "LocalFlow.exe").is_file()


class Updater:
    def __init__(self, updates_dir: Path):
        self.updates_dir = Path(updates_dir)

    def download_and_stage(self, info: UpdateInfo) -> Path:
        """Download the release zip and extract it; return the staged folder."""
        import requests

        self.updates_dir.mkdir(parents=True, exist_ok=True)
        zip_path = self.updates_dir / ASSET_NAME
        with requests.get(info.url, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            with open(zip_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        staged = self.updates_dir / "staged"
        if staged.exists():
            import shutil
            shutil.rmtree(staged)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(staged)
        zip_path.unlink(missing_ok=True)
        # the zip may contain the folder itself or its contents directly
        root = staged / "LocalFlow" if (staged / "LocalFlow").is_dir() else staged
        if not validate_staged(root):
            raise RuntimeError("staged update is missing LocalFlow.exe")
        return root

    def apply_and_restart(self, staged: Path, quit_app) -> None:
        """Spawn the detached swap helper, then ask the app to quit."""
        install_dir = Path(sys.executable).parent
        helper = self.updates_dir / "swap.cmd"
        helper.write_text(_swap_script(install_dir, Path(staged)), encoding="ascii")
        subprocess.Popen(
            ["cmd", "/c", str(helper)],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
            close_fds=True,
            cwd=str(self.updates_dir),
        )
        log.info("update helper launched; quitting for swap to %s", staged)
        quit_app()


def _swap_script(install_dir: Path, staged: Path) -> str:
    """Batch script: wait for exit, swap folders with rollback, restart."""
    backup = install_dir.with_name(install_dir.name + ".backup")
    return f"""@echo off
set LIVE={install_dir}
set STAGED={staged}
set BACKUP={backup}

if exist "%BACKUP%" rmdir /s /q "%BACKUP%"

rem -- renaming LIVE fails while any of its files are still locked, so the
rem -- move doubles as a non-destructive "has the app exited yet" probe
for /l %%i in (1,1,60) do (
  move "%LIVE%" "%BACKUP%" >nul 2>&1 && goto swap
  rem ping is the sleep that still works in a detached, console-less process
  ping -n 2 127.0.0.1 >nul
)
exit /b 1

:swap
move "%STAGED%" "%LIVE%" >nul || goto rollback
start "" "%LIVE%\\LocalFlow.exe"
exit /b 0

:rollback
move "%BACKUP%" "%LIVE%" >nul
:fail_start_old
start "" "%LIVE%\\LocalFlow.exe"
exit /b 1
"""
