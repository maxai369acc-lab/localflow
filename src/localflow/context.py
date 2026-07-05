"""Active-app context: process name, window title, category, paste strategy."""

from __future__ import annotations

import sys
from dataclasses import dataclass

IS_MAC = sys.platform == "darwin"

if not IS_MAC:
    import psutil
    import win32gui
    import win32process

# heuristic fallbacks when no app_rules row matches
_HEURISTICS = [
    (("terminal", "console", "cmd", "powershell"), "terminal"),
    (("mail", "outlook"), "email"),
]


@dataclass
class AppContext:
    process: str = "unknown"
    title: str = ""
    category: str = "other"
    paste_strategy: str = "clipboard"


def _fill_foreground_mac(ctx: AppContext, read_title: bool) -> None:
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return
    ctx.process = (str(app.localizedName() or "")).lower() or "unknown"
    if read_title:
        try:
            import Quartz
            wins = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
            pid = app.processIdentifier()
            for w in wins or []:
                if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowName"):
                    ctx.title = str(w["kCGWindowName"])
                    break
        except Exception:
            pass


def _fill_foreground_win(ctx: AppContext, read_title: bool) -> None:
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    if pid:
        try:
            ctx.process = psutil.Process(pid).name().lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if read_title:
        try:
            ctx.title = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            pass


def get_foreground_context(db=None, read_title: bool = True) -> AppContext:
    ctx = AppContext()
    try:
        if IS_MAC:
            _fill_foreground_mac(ctx, read_title)
        else:
            _fill_foreground_win(ctx, read_title)
    except Exception:
        return ctx

    rule = db.app_rule(ctx.process) if db is not None else None
    if rule:
        ctx.category, ctx.paste_strategy = rule
    else:
        low = ctx.process + " " + ctx.title.lower()
        for needles, cat in _HEURISTICS:
            if any(n in low for n in needles):
                ctx.category = cat
                if cat == "terminal":
                    ctx.paste_strategy = "shift_insert"
                break
    return ctx
