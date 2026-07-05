"""Global hotkeys via low-level keyboard hook.

Defaults (spec section 11):
  - Push-to-talk: hold Ctrl+Win, release to finish.
  - Command Mode: hold Ctrl+Win+Alt (Alt can join an active hold).
  - Hands-free:   Ctrl+Win+Space toggles start/stop.
  - Paste last:   Alt+Shift+Z.
  - Cancel:       Esc (only while a session is active).

Any other key pressed during a Ctrl+Win hold aborts the push-to-talk so real
OS shortcuts (Win+Ctrl+Left etc.) don't produce ghost dictations.
"""

from __future__ import annotations

import sys
import threading

if sys.platform == "win32":
    import keyboard

CTRL_NAMES = {"ctrl", "left ctrl", "right ctrl"}
WIN_NAMES = {"windows", "left windows", "right windows"}
ALT_NAMES = {"alt", "left alt", "right alt", "alt gr"}
_ALLOWED_DURING_PTT = CTRL_NAMES | WIN_NAMES | ALT_NAMES | {"space", "esc"}


class WinHotkeyManager:
    def __init__(self, *, on_ptt_start, on_ptt_stop, on_ptt_abort,
                 on_handsfree_toggle, on_cancel, on_paste_last,
                 on_command_start=None):
        self.on_ptt_start = on_ptt_start
        self.on_ptt_stop = on_ptt_stop
        self.on_ptt_abort = on_ptt_abort
        self.on_handsfree_toggle = on_handsfree_toggle
        self.on_cancel = on_cancel
        self.on_paste_last = on_paste_last
        self.on_command_start = on_command_start

        self._ctrl = False
        self._win = False
        self._alt = False
        self._command_fired = False
        self._chord_active = False
        self._paused = False
        self._esc_handle = None
        self._lock = threading.Lock()
        self._started = False

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        keyboard.hook(self._on_event)
        keyboard.add_hotkey("ctrl+windows+space", self._safe(self.on_handsfree_toggle),
                            suppress=True)
        keyboard.add_hotkey("alt+shift+z", self._safe(self.on_paste_last),
                            suppress=True)
        self._started = True

    def stop(self) -> None:
        if self._started:
            try:
                keyboard.unhook_all()
            except Exception:
                pass
            self._started = False

    # While we synthesize Ctrl+V ourselves, don't interpret our own events.
    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        # re-read the physical state so a stale 'down' can't linger
        try:
            self._ctrl = any(keyboard.is_pressed(k) for k in ("ctrl", "right ctrl"))
            self._win = any(keyboard.is_pressed(k) for k in ("left windows", "right windows"))
            self._alt = any(keyboard.is_pressed(k) for k in ("alt", "right alt"))
        except Exception:
            self._ctrl = self._win = self._alt = False
        self._paused = False

    # Esc should only be swallowed while a dictation session is running.
    def enable_cancel(self) -> None:
        with self._lock:
            if self._esc_handle is None:
                self._esc_handle = keyboard.add_hotkey(
                    "esc", self._safe(self.on_cancel), suppress=True)

    def disable_cancel(self) -> None:
        with self._lock:
            if self._esc_handle is not None:
                try:
                    keyboard.remove_hotkey(self._esc_handle)
                except (KeyError, ValueError):
                    pass
                self._esc_handle = None

    # -- internals -------------------------------------------------------
    @staticmethod
    def _safe(fn):
        def wrapper(*_a):
            try:
                fn()
            except Exception:
                pass
        return wrapper

    def _on_event(self, e) -> None:
        name = (e.name or "").lower()
        down = e.event_type == "down"

        if name in CTRL_NAMES:
            self._ctrl = down
        elif name in WIN_NAMES:
            self._win = down
        elif name in ALT_NAMES:
            self._alt = down
        elif self._chord_active and down and name not in _ALLOWED_DURING_PTT:
            # the user is doing some other Ctrl+Win+X shortcut
            self._chord_active = False
            if not self._paused:
                try:
                    self.on_ptt_abort()
                except Exception:
                    pass
            return

        if self._paused:
            return

        if self._ctrl and self._win and not self._chord_active:
            self._chord_active = True
            self._command_fired = False
            try:
                self.on_ptt_start()
            except Exception:
                pass
        elif self._chord_active and not (self._ctrl and self._win):
            self._chord_active = False
            self._command_fired = False
            try:
                self.on_ptt_stop()
            except Exception:
                pass

        # Alt joining (or already held during) a Ctrl+Win hold = Command Mode
        if (self._chord_active and self._alt and not self._command_fired
                and self.on_command_start):
            self._command_fired = True
            try:
                self.on_command_start()
            except Exception:
                pass


class MacHotkeyManager:
    """pynput-based hotkeys for macOS (requires Accessibility permission).

    Chords: hold Ctrl+Option = dictate, +Cmd = command mode,
    Ctrl+Option+Space = hands-free toggle, Option+Shift+Z = paste last,
    Esc = cancel (while a session is active). Non-suppressing: the keys also
    reach the focused app, which is acceptable for modifier-only chords.
    """

    def __init__(self, *, on_ptt_start, on_ptt_stop, on_ptt_abort,
                 on_handsfree_toggle, on_cancel, on_paste_last,
                 on_command_start=None):
        self.on_ptt_start = on_ptt_start
        self.on_ptt_stop = on_ptt_stop
        self.on_ptt_abort = on_ptt_abort
        self.on_handsfree_toggle = on_handsfree_toggle
        self.on_cancel = on_cancel
        self.on_paste_last = on_paste_last
        self.on_command_start = on_command_start

        self._ctrl = self._alt = self._cmd = self._shift = False
        self._chord_active = False
        self._command_fired = False
        self._cancel_enabled = False
        self._paused = False
        self._listener = None

    def start(self) -> None:
        from pynput import keyboard as pk
        self._pk = pk
        self._listener = pk.Listener(on_press=self._press,
                                     on_release=self._release)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def enable_cancel(self) -> None:
        self._cancel_enabled = True

    def disable_cancel(self) -> None:
        self._cancel_enabled = False

    # -- internals ---------------------------------------------------------
    def _kind(self, key) -> str:
        pk = self._pk
        if key in (pk.Key.ctrl, pk.Key.ctrl_l, pk.Key.ctrl_r):
            return "ctrl"
        if key in (pk.Key.alt, pk.Key.alt_l, pk.Key.alt_r):
            return "alt"
        if key in (pk.Key.cmd, pk.Key.cmd_l, pk.Key.cmd_r):
            return "cmd"
        if key in (pk.Key.shift, pk.Key.shift_l, pk.Key.shift_r):
            return "shift"
        if key == pk.Key.space:
            return "space"
        if key == pk.Key.esc:
            return "esc"
        try:
            return f"char:{(key.char or '').lower()}"
        except AttributeError:
            return "other"

    def _press(self, key) -> None:
        if self._paused:
            return
        k = self._kind(key)
        if k in ("ctrl", "alt", "cmd", "shift"):
            setattr(self, "_" + k, True)
        elif k == "esc":
            if self._cancel_enabled:
                self._safe(self.on_cancel)
            return
        elif k == "space" and self._ctrl and self._alt:
            self._safe(self.on_handsfree_toggle)
            return
        elif k == "char:z" and self._alt and self._shift:
            self._safe(self.on_paste_last)
            return
        elif self._chord_active and k not in ("space",):
            # another key during the hold: user is doing a real shortcut
            self._chord_active = False
            self._command_fired = False
            self._safe(self.on_ptt_abort)
            return

        if self._ctrl and self._alt and not self._chord_active:
            self._chord_active = True
            self._command_fired = False
            self._safe(self.on_ptt_start)
        if (self._chord_active and self._cmd and not self._command_fired
                and self.on_command_start):
            self._command_fired = True
            self._safe(self.on_command_start)

    def _release(self, key) -> None:
        if self._paused:
            return
        k = self._kind(key)
        if k in ("ctrl", "alt", "cmd", "shift"):
            setattr(self, "_" + k, False)
        if self._chord_active and not (self._ctrl and self._alt):
            self._chord_active = False
            self._command_fired = False
            self._safe(self.on_ptt_stop)

    @staticmethod
    def _safe(fn) -> None:
        try:
            fn()
        except Exception:
            pass


HotkeyManager = MacHotkeyManager if sys.platform == "darwin" else WinHotkeyManager
