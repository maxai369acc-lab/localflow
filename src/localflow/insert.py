"""Text insertion: clipboard set + Ctrl+V (or Shift+Insert), then restore.

The previous clipboard is restored only when it held plain text (restoring
images/files reliably is out of MVP scope). If restore is skipped the final
text simply stays on the clipboard, which doubles as the failure fallback.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

IS_MAC = sys.platform == "darwin"

if not IS_MAC:
    import keyboard
    import pywintypes
    import win32clipboard as wc
    import win32con


class ClipboardError(Exception):
    pass


if IS_MAC:
    def get_clipboard_text() -> tuple[str | None, bool]:
        try:
            out = subprocess.run(["pbpaste"], capture_output=True,
                                 timeout=3).stdout
            text = out.decode("utf-8", errors="replace")
            return (text if text else None), True
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ClipboardError(f"pbpaste failed: {e}") from e

    def set_clipboard_text(text: str) -> None:
        try:
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=3,
                           check=True)
        except (OSError, subprocess.CalledProcessError,
                subprocess.TimeoutExpired) as e:
            raise ClipboardError(f"pbcopy failed: {e}") from e

    def _osa_keystroke(spec: str) -> None:
        subprocess.run(
            ["osascript", "-e",
             f'tell application "System Events" to {spec}'],
            capture_output=True, timeout=5)

    def _send_paste_chord(strategy: str) -> None:
        _osa_keystroke('keystroke "v" using command down')

    def _send_copy_chord() -> None:
        _osa_keystroke('keystroke "c" using command down')

    def _send_enter() -> None:
        _osa_keystroke("key code 36")  # return

    def _wait_modifiers_released(timeout_s: float = 2.0) -> None:
        # pynput's pressed state isn't polled here; a short settle delay is
        # enough in practice because the chord keys are modifier-only.
        time.sleep(0.25)

else:
    def _open_clipboard(retries: int = 8, delay: float = 0.05) -> None:
        last = None
        for _ in range(retries):
            try:
                wc.OpenClipboard()
                return
            except pywintypes.error as e:
                last = e
                time.sleep(delay)
        raise ClipboardError(f"could not open clipboard: {last}")

    def get_clipboard_text() -> tuple[str | None, bool]:
        """Returns (text or None, is_restorable). Non-text -> (None, False)."""
        _open_clipboard()
        try:
            if wc.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return wc.GetClipboardData(win32con.CF_UNICODETEXT), True
            has_any = wc.EnumClipboardFormats(0) != 0
            return None, not has_any  # empty clipboard is trivially restorable
        finally:
            wc.CloseClipboard()

    def set_clipboard_text(text: str) -> None:
        _open_clipboard()
        try:
            wc.EmptyClipboard()
            wc.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            wc.CloseClipboard()

    def _send_paste_chord(strategy: str) -> None:
        chord = "shift+insert" if strategy == "shift_insert" else "ctrl+v"
        keyboard.send(chord)

    def _send_copy_chord() -> None:
        keyboard.send("ctrl+c")

    def _send_enter() -> None:
        keyboard.send("enter")

    def _wait_modifiers_released(timeout_s: float = 2.0) -> None:
        """Don't send the paste chord while hotkey modifiers are held."""
        deadline = time.monotonic() + timeout_s
        mods = ("ctrl", "left windows", "right windows", "alt", "shift")
        while time.monotonic() < deadline:
            try:
                if not any(keyboard.is_pressed(m) for m in mods):
                    return
            except Exception:
                return
            time.sleep(0.02)


def capture_selection(pause_hook=None, resume_hook=None,
                      settle_ms: int = 250) -> str | None:
    """Read the focused app's selected text by synthesizing Ctrl+C.

    Returns the selection, or None when nothing is selected. The previous
    clipboard is restored before returning (we keep the selection in memory).
    Do not call for terminal windows — Ctrl+C is an interrupt there.
    """
    prev_text: str | None = None
    prev_restorable = False
    try:
        prev_text, prev_restorable = get_clipboard_text()
    except ClipboardError:
        pass

    try:
        set_clipboard_text("")  # sentinel: empty means "no selection copied"
    except ClipboardError:
        return None
    _wait_modifiers_released()

    if pause_hook:
        pause_hook()
    try:
        _send_copy_chord()
    finally:
        if resume_hook:
            resume_hook()
    time.sleep(settle_ms / 1000.0)

    selection: str | None = None
    try:
        selection, _ = get_clipboard_text()
    except ClipboardError:
        pass

    if prev_restorable and prev_text is not None:
        try:
            set_clipboard_text(prev_text)
        except ClipboardError:
            pass
    return selection or None


def insert_text(text: str, *, strategy: str = "clipboard",
                restore_clipboard: bool = True, restore_delay_ms: int = 900,
                pre_paste_delay_ms: int = 60, send_enter: bool = False,
                pause_hook=None, resume_hook=None) -> bool:
    """Paste `text` into the focused field. Returns True if the chord was sent."""
    if not text:
        return False

    prev_text: str | None = None
    prev_restorable = False
    try:
        prev_text, prev_restorable = get_clipboard_text()
    except ClipboardError:
        pass

    set_clipboard_text(text)  # raises ClipboardError -> caller notifies
    time.sleep(pre_paste_delay_ms / 1000.0)
    _wait_modifiers_released()

    if pause_hook:
        pause_hook()
    try:
        _send_paste_chord(strategy)
        if send_enter:
            time.sleep(0.12)
            _send_enter()
    finally:
        if resume_hook:
            resume_hook()

    if restore_clipboard and prev_restorable:
        def _restore():
            try:
                if prev_text is not None:
                    set_clipboard_text(prev_text)
                # previous clipboard was empty: leave our text (harmless)
            except ClipboardError:
                pass
        threading.Timer(restore_delay_ms / 1000.0, _restore).start()
    return True
