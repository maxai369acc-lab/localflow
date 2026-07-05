"""FlowBar — the floating glass lozenge (hover to expand).

Collapsed it is a tiny translucent lozenge wearing the waveform mark.
Hovering breathes it open into the full bar: logo, Dictate, Open app and
the push-to-talk hint. While recording it stays open and runs live
amplitude bars — a click anywhere on it stops the take. Processing shows
the traveling ribbon; a finished take flashes a mint check before the bar
settles back into its lozenge.

Windows treats color-keyed pixels as click-through, so only the visible
pill is hot: hover/clicks land on the bar, everything around it falls
through to whatever is underneath. Runs on the tk main thread.
"""

from __future__ import annotations

import math
import tkinter as tk

from .theme import (ALERT, CARD2, DIMT, LINE, MINT, TEXT, lerp_color,
                    mono_font, signal_color)

KEY = "#010203"          # transparentcolor key (never drawn otherwise)
INK = "#101319"          # pill body — matches the dictation pill
EDGE = "#2A3040"         # pill border
SHINE = "#39415A"        # glass top highlight

EXP_W, EXP_H = 332, 44   # expanded pill
COL_W, COL_H = 64, 20    # collapsed lozenge
W, H = EXP_W + 8, EXP_H + 8
TICK_MS = 40             # 25 fps
EXPAND_STEP = 0.34       # ~120 ms to open — hover must feel instant
COLLAPSE_STEP = 0.12     # gentler close
GRACE_TICKS = 8          # ~0.3 s pointer-leave grace before collapsing
FLASH_TICKS = 22         # success/error flash length (~0.9 s)
ALPHA_LO, ALPHA_HI = 0.78, 0.98
LOGO_HEIGHTS = (0.38, 0.68, 1.0, 0.58, 0.34)


def _ease(t: float) -> float:
    return 1 - (1 - t) ** 3


def hit_action(regions, x: float, y: float) -> str | None:
    """Pure hit test so the click routing stays testable."""
    for x0, y0, x1, y1, action in regions:
        if x0 <= x <= x1 and y0 <= y <= y1:
            return action
    return None


class FlowBar:
    """Owns one Toplevel. show()/hide()/set_state() from the tk thread."""

    def __init__(self, root, levels, hint: str = "Ctrl+Win"):
        self.on_action = None            # callable(str): "mic" | "open"
        self.on_move = None              # callable(x, y)
        self._root = root
        self._levels = levels            # shared deque fed by Ui.set_level
        self._hint = hint
        self._state = "starting"
        self._p = 0.0                    # expansion progress 0..1
        self._hover = False
        self._away = 0                   # ticks since pointer left
        self._flash: str | None = None   # "success" | "error"
        self._flash_t = 0
        self._tick_n = 0
        self._phase = 0.0
        self._drag: tuple[int, int, int, int] | None = None
        self._regions: list[tuple[float, float, float, float, str]] = []
        self._hidden = True
        self._last_sig = None
        self._words = 0                  # shown next to the success check
        self._err = ""                   # shown during the error flash

        self._win = w = tk.Toplevel(root)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        bg = INK
        try:
            w.attributes("-transparentcolor", KEY)
            bg = KEY
        except tk.TclError:
            pass
        w.configure(bg=bg)
        self._c = c = tk.Canvas(w, width=W, height=H, bg=bg,
                                highlightthickness=0, bd=0, cursor="hand2")
        c.pack()
        self._mono = mono_font(c)
        c.bind("<Enter>", self._enter)
        c.bind("<Leave>", self._leave)
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._motion)
        c.bind("<ButtonRelease-1>", self._release)
        self._loop()

    # ---- public API (tk thread) ------------------------------------------
    def show(self, pos: tuple[int, int] | None = None) -> None:
        sw = self._win.winfo_screenwidth()
        sh = self._win.winfo_screenheight()
        x, y = pos or ((sw - W) // 2, sh - H - 50)
        x = min(max(0, int(x)), sw - W)
        y = min(max(0, int(y)), sh - H)
        self._win.geometry(f"{W}x{H}+{x}+{y}")
        self._hidden = False
        self._last_sig = None
        self._win.deiconify()
        self._win.attributes("-topmost", True)

    def hide(self) -> None:
        self._hidden = True
        self._win.withdraw()

    @property
    def visible(self) -> bool:
        return not self._hidden

    def note_words(self, n: int) -> None:
        """Word count for the next success flash (bar replaces the pill)."""
        self._words = int(n)

    def note_error(self, msg: str) -> None:
        self._err = str(msg)[:40]

    def set_state(self, state: str) -> None:
        if state == self._state:
            return
        if self._state == "processing" and state == "idle":
            self._flash, self._flash_t = "success", self._tick_n
        elif state == "error":
            self._flash, self._flash_t = "error", self._tick_n
        self._state = state
        self._last_sig = None

    # ---- pointer ----------------------------------------------------------
    def _enter(self, _e) -> None:
        self._hover, self._away = True, 0
        # don't wait for the next tick: first expansion frame right now
        self._p = max(self._p, 0.35)
        self._set_alpha(True)
        self._draw()
        self._last_sig = (round(self._p, 3), self._state, self._flash)

    def _leave(self, _e) -> None:
        self._hover = False

    def _set_alpha(self, hi: bool) -> None:
        if getattr(self, "_alpha_hi", None) == hi:
            return
        self._alpha_hi = hi
        try:
            self._win.attributes("-alpha", ALPHA_HI if hi else ALPHA_LO)
        except tk.TclError:
            pass

    def _press(self, e) -> None:
        self._drag = (e.x_root, e.y_root,
                      self._win.winfo_x(), self._win.winfo_y())

    def _motion(self, e) -> None:
        if not self._drag:
            return
        x0, y0, wx, wy = self._drag
        self._win.geometry(f"+{wx + e.x_root - x0}+{wy + e.y_root - y0}")

    def _release(self, e) -> None:
        if not self._drag:
            return
        x0, y0, _, _ = self._drag
        moved = abs(e.x_root - x0) + abs(e.y_root - y0)
        self._drag = None
        if moved > 4:
            pos = (self._win.winfo_x(), self._win.winfo_y())
            if self.on_move:
                try:
                    self.on_move(*pos)
                except Exception:
                    pass
            return
        if self._state == "recording":
            self._fire("mic")            # tap anywhere = stop the take
        elif self._p > 0.9:
            action = hit_action(self._regions, e.x, e.y)
            if action:
                self._fire(action)

    def _fire(self, action: str) -> None:
        if self.on_action:
            try:
                self.on_action(action)
            except Exception:
                pass

    # ---- animation loop -----------------------------------------------------
    def _loop(self) -> None:
        try:
            self._step()
        except tk.TclError:
            return                        # window is being torn down
        self._win.after(TICK_MS, self._loop)

    def _step(self) -> None:
        self._tick_n += 1
        if self._hidden:
            return
        if self._flash and self._tick_n - self._flash_t > FLASH_TICKS:
            self._flash = None
            self._words = 0
            self._err = ""
            self._last_sig = None
        if not self._hover:
            self._away += 1

        busy = self._state in ("recording", "processing")
        want = (self._hover or busy or self._flash is not None
                or self._away < GRACE_TICKS)
        target = 1.0 if want else 0.0
        self._set_alpha(want)
        if self._p != target:
            self._p = (min(target, self._p + EXPAND_STEP)
                       if target > self._p
                       else max(target, self._p - COLLAPSE_STEP))
            self._last_sig = None

        animated = busy or self._flash is not None
        sig = (round(self._p, 3), self._state, self._flash)
        if animated or sig != self._last_sig:
            self._phase += 0.22
            self._draw()
            self._last_sig = sig

    # ---- drawing --------------------------------------------------------------
    def _draw(self) -> None:
        c = self._c
        c.delete("all")
        self._regions = []
        t = _ease(self._p)
        pw = COL_W + (EXP_W - COL_W) * t
        ph = COL_H + (EXP_H - COL_H) * t
        cx, cy = W / 2, H / 2
        x0, y0 = cx - pw / 2, cy - ph / 2
        x1, y1 = cx + pw / 2, cy + ph / 2
        r = ph / 2
        # glass pill: body, border, top shine
        c.create_oval(x0, y0, x0 + ph, y1, fill=INK, outline=EDGE)
        c.create_oval(x1 - ph, y0, x1, y1, fill=INK, outline=EDGE)
        c.create_rectangle(x0 + r, y0, x1 - r, y1, fill=INK, outline=INK)
        c.create_line(x0 + r, y0, x1 - r, y0, fill=EDGE)
        c.create_line(x0 + r, y1, x1 - r, y1, fill=EDGE)
        c.create_line(x0 + r * 0.8, y0 + 2, x1 - r * 0.8, y0 + 2, fill=SHINE)

        if self._flash == "success":
            self._draw_check(c, cx, cy, pw)
        elif self._flash == "error" or self._state == "error":
            if self._err:
                c.create_text(cx, cy, text=self._err, fill=ALERT,
                              font=("Segoe UI", 9))
            else:
                c.create_line(cx - pw / 2 + 16, cy, cx + pw / 2 - 16, cy,
                              fill=ALERT, width=2.5, capstyle="round")
        elif self._state == "recording" and t > 0.6:
            self._draw_levels(c, cx, cy, pw)
        elif self._state == "processing" and t > 0.6:
            self._draw_ribbon(c, cx, cy, pw)
        elif t > 0.88:                     # eased width, not raw progress —
            self._draw_menu(c, cx, cy, pw, ph)  # menu shows on tick 1-2
        else:
            self._draw_logo(c, cx, cy, max(COL_H, ph) * 0.66, dim=0.35)

    def _draw_logo(self, c, cx, cy, span, dim=0.0, x=None) -> None:
        lx = (x if x is not None else cx - 10)
        for i, hf in enumerate(LOGO_HEIGHTS):
            t = i / (len(LOGO_HEIGHTS) - 1)
            bh = hf * span
            bx = lx + i * 5
            c.create_line(bx, cy - bh / 2, bx, cy + bh / 2,
                          fill=signal_color(t, dim), width=2.6,
                          capstyle="round")

    def _draw_menu(self, c, cx, cy, pw, ph) -> None:
        px0 = cx - pw / 2
        self._draw_logo(c, cx, cy, ph * 0.52, x=px0 + 20)

        def button(bx, label, glyph_fn, action):
            bw, bh = 96, 28
            by0 = cy - bh / 2
            c.create_oval(bx, by0, bx + bh, by0 + bh, fill=CARD2,
                          outline=LINE)
            c.create_oval(bx + bw - bh, by0, bx + bw, by0 + bh, fill=CARD2,
                          outline=LINE)
            c.create_rectangle(bx + bh / 2, by0, bx + bw - bh / 2, by0 + bh,
                               fill=CARD2, outline=CARD2)
            glyph_fn(bx + 20, cy)
            c.create_text(bx + 30, cy, text=label, anchor="w", fill=TEXT,
                          font=("Segoe UI", 9, "bold"))
            self._regions.append((bx, by0, bx + bw, by0 + bh, action))

        def mic(mx, my):
            col = signal_color(0.0)
            c.create_rectangle(mx - 2.6, my - 7, mx + 2.6, my + 1, fill=col,
                               width=0)
            c.create_oval(mx - 2.6, my - 9, mx + 2.6, my - 5, fill=col,
                          width=0)
            c.create_arc(mx - 5.5, my - 4, mx + 5.5, my + 6, start=180,
                         extent=180, style="arc", outline=col, width=1.8)
            c.create_line(mx, my + 6, mx, my + 9, fill=col, width=1.8)

        def open_glyph(gx, gy):
            col = signal_color(1.0)
            c.create_rectangle(gx - 6, gy - 6, gx + 6, gy + 6, outline=col,
                               width=1.8)
            c.create_line(gx - 6, gy - 2, gx + 6, gy - 2, fill=col,
                          width=1.8)

        button(px0 + 52, "Dictate", mic, "mic")
        button(px0 + 156, "Open app", open_glyph, "open")
        c.create_text(cx + pw / 2 - 14, cy, text=self._hint, anchor="e",
                      fill=DIMT, font=(self._mono, 8))

    def _draw_levels(self, c, cx, cy, pw) -> None:
        inner = pw - 44
        n = max(8, int(inner // 7))
        vals = list(self._levels)[-n:]
        vals = [0.06] * (n - len(vals)) + vals
        for i, v in enumerate(vals):
            t = i / max(1, n - 1)
            x = cx - inner / 2 + t * inner
            bh = 3 + v * 15
            c.create_line(x, cy - bh, x, cy + bh, fill=signal_color(t),
                          width=3, capstyle="round")

    def _draw_ribbon(self, c, cx, cy, pw) -> None:
        inner = pw - 44
        pts = []
        for i in range(40):
            t = i / 39
            x = cx - inner / 2 + t * inner
            y = cy + math.sin(self._phase + t * 5.2) * 7 * math.sin(
                t * math.pi)
            pts += [x, y]
        c.create_line(*pts, fill=signal_color(0.75), width=2.5, smooth=True,
                      capstyle="round")

    def _draw_check(self, c, cx, cy, pw) -> None:
        c.create_line(cx - pw / 2 + 16, cy, cx - 8, cy, fill=MINT, width=2.5,
                      capstyle="round")
        c.create_line(cx - 8, cy, cx - 2, cy + 6, fill=MINT, width=2.5,
                      capstyle="round")
        c.create_line(cx - 2, cy + 6, cx + 10, cy - 7, fill=MINT, width=2.5,
                      capstyle="round")
        right = cx + pw / 2 - 16
        if self._words:
            c.create_text(right, cy, text=f"{self._words} wds", anchor="e",
                          fill=MINT, font=(self._mono, 8))
            right -= 52
        c.create_line(cx + 14, cy, right, cy,
                      fill=lerp_color(MINT, INK, 0.45), width=2.5,
                      capstyle="round")
