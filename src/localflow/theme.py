"""Ink & Signal theme: blue-black instrument surfaces, indigo→violet signal.

One rule holds the identity together: the indigo→violet gradient belongs to
live signal only (waveforms, the active nav mark, meters). Primary buttons
are light "keys" on the ink, numbers read out in mono like meter displays.
Shared tokens and widget factories for the main window and editor panels.
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# ---- tokens -----------------------------------------------------------------
GROUND = "#0C0F14"       # window + sidebar ground
CARD = "#141924"         # main content panel
CARD2 = "#1B2130"        # inner cards, hover rows
LINE = "#262D3B"         # hairlines
TEXT = "#E9EDF4"         # primary text (mist)
DIMT = "#8B93A5"         # secondary text
NAV_ACTIVE = "#1B2130"   # sidebar selected pill
KEYCAP = "#E9EDF4"       # primary button ground (a light key on the ink)
KEYCAP_HI = "#FFFFFF"

FLOW = "#7C8CF8"         # indigo — live signal
PULSE = "#B48CF2"        # violet — signal gradient partner
MINT = "#4ADE9E"         # success only
ALERT = "#F2555A"        # errors only

FONT = "Segoe UI"
MONO = "Consolas"        # upgraded to Cascadia Mono at runtime if installed

_DISPLAY: str | None = None
_MONO: str | None = None
_ICON_FONT: str | None = None


def display_font(root) -> str:
    """Headline face: Segoe UI Variable Display when the OS ships it."""
    global _DISPLAY
    if _DISPLAY is None:
        fams = set(tkfont.families(root))
        _DISPLAY = ("Segoe UI Variable Display"
                    if "Segoe UI Variable Display" in fams else FONT)
    return _DISPLAY


def mono_font(root) -> str:
    """Meter-readout face for numbers and hotkeys."""
    global _MONO
    if _MONO is None:
        fams = set(tkfont.families(root))
        _MONO = "Cascadia Mono" if "Cascadia Mono" in fams else MONO
    return _MONO


def icon_font(root) -> str | None:
    """Windows ships line-icon fonts; pick whichever exists."""
    global _ICON_FONT
    if _ICON_FONT is None:
        fams = set(tkfont.families(root))
        for cand in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
            if cand in fams:
                _ICON_FONT = cand
                break
        else:
            _ICON_FONT = ""
    return _ICON_FONT or None


def lerp_color(c1: str, c2: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    a, b = int(c1[1:], 16), int(c2[1:], 16)
    r = round(((a >> 16) & 255) + (((b >> 16) & 255) - ((a >> 16) & 255)) * t)
    g = round(((a >> 8) & 255) + (((b >> 8) & 255) - ((a >> 8) & 255)) * t)
    bl = round((a & 255) + ((b & 255) - (a & 255)) * t)
    return f"#{r:02x}{g:02x}{bl:02x}"


def signal_color(t: float, dim: float = 0.0) -> str:
    """Position t (0..1) along the indigo→violet run, optionally sunk into ink."""
    c = lerp_color(FLOW, PULSE, t)
    return lerp_color(c, CARD, dim) if dim > 0 else c


_styled_roots: set = set()


def ensure_style(widget) -> None:
    root = widget.winfo_toplevel()
    key = str(root)
    if key in _styled_roots:
        return
    s = ttk.Style(widget)
    s.theme_use("clam")
    s.configure("Ink.Treeview", background=CARD, fieldbackground=CARD,
                foreground=TEXT, rowheight=30, borderwidth=0, relief="flat",
                font=(FONT, 10))
    s.configure("Ink.Treeview.Heading", background=CARD, foreground=DIMT,
                borderwidth=0, relief="flat", padding=(8, 6), font=(FONT, 9))
    s.map("Ink.Treeview", background=[("selected", CARD2)],
          foreground=[("selected", TEXT)])
    _styled_roots.add(key)


# ---- factories ----------------------------------------------------------------

def btn_primary(parent, text: str, command) -> tk.Button:
    """A light key on the ink — the instrument's main action."""
    return tk.Button(parent, text=text, command=command, relief="flat", bd=0,
                     cursor="hand2", padx=16, pady=7, font=(FONT, 9, "bold"),
                     bg=KEYCAP, fg=GROUND, activebackground=KEYCAP_HI,
                     activeforeground=GROUND)


def btn_ghost(parent, text: str, command) -> tk.Button:
    return tk.Button(parent, text=text, command=command, relief="flat", bd=0,
                     cursor="hand2", padx=14, pady=6, font=(FONT, 9),
                     bg=CARD2, fg=TEXT, activebackground=LINE,
                     activeforeground=TEXT)


def entry(parent, **kw) -> tk.Entry:
    return tk.Entry(parent, bg=GROUND, fg=TEXT, insertbackground=FLOW,
                    relief="flat", highlightthickness=1,
                    highlightbackground=LINE, highlightcolor=FLOW,
                    font=(FONT, 10), **kw)


def listbox(parent, **kw) -> tk.Listbox:
    return tk.Listbox(parent, bg=CARD, fg=TEXT, relief="flat", bd=0,
                      highlightthickness=1, highlightbackground=LINE,
                      highlightcolor=LINE, selectbackground=CARD2,
                      selectforeground=TEXT, activestyle="none",
                      font=(FONT, 10), **kw)


def dim_label(parent, text: str, bg: str = CARD) -> tk.Label:
    return tk.Label(parent, text=text, bg=bg, fg=DIMT, font=(FONT, 9))


def eyebrow(parent, text: str, bg: str = CARD) -> tk.Label:
    return tk.Label(parent, text=text.upper(), bg=bg, fg=DIMT,
                    font=(FONT, 8, "bold"))


def h1(parent, text: str, bg: str = CARD) -> tk.Label:
    return tk.Label(parent, text=text, bg=bg, fg=TEXT,
                    font=(FONT, 14, "bold"))


def signal_strip(parent, title: str, subtitle: str, height: int = 120,
                 kbd: str = "", state_fn=None) -> tk.Canvas:
    """The signature element: a live waveform strip under the headline.

    Ambient bars breathe across the indigo→violet run; when state_fn reports
    "recording" they wake to full signal, "processing" leans violet.
    """
    c = tk.Canvas(parent, height=height, bg=GROUND, highlightthickness=0,
                  bd=0)
    disp = display_font(parent)
    mono = mono_font(parent)
    phase = [0.0]

    def _paint() -> None:
        c.delete("all")
        w = c.winfo_width() or 600
        st = state_fn() if state_fn else "idle"
        # waveform bed along the bottom
        n = max(24, w // 9)
        base_y = height - 14
        p = phase[0]
        for i in range(n):
            t = i / max(1, n - 1)
            env = math.sin(t * math.pi) ** 0.7   # taller mid-strip
            wob = (math.sin(p + i * 0.55) * 0.5 +
                   math.sin(p * 0.7 + i * 0.23) * 0.5)
            if st == "recording":
                amp, dim = 26 * env * (0.55 + 0.45 * abs(wob)), 0.0
            elif st == "processing":
                amp, dim = 18 * env * (0.5 + 0.5 * abs(wob)), 0.15
            else:
                amp, dim = 10 * env * (0.45 + 0.55 * abs(wob)), 0.55
            x = 8 + t * (w - 16)
            col = signal_color(t, dim)
            c.create_line(x, base_y - amp, x, base_y + amp * 0.35,
                          fill=col, width=3, capstyle="round")
        c.create_text(28, 26, text=title, anchor="w", fill=TEXT,
                      font=(disp, 16, "bold"))
        c.create_text(28, 52, text=subtitle, anchor="w", fill=DIMT,
                      font=(FONT, 9))
        if kbd:
            c.create_text(28, height - 42, text=kbd, anchor="w",
                          fill=FLOW, font=(mono, 9))

    def _tick() -> None:
        if not c.winfo_exists():
            return
        phase[0] += 0.16
        _paint()
        c.after(80, _tick)

    c.bind("<Configure>", lambda e: _paint())
    _tick()
    return c


def tabs_row(parent, names: list[str], on_select, bg: str = CARD):
    """Underlined tab strip; the active tab carries a signal underline."""
    f = tk.Frame(parent, bg=bg)
    labels: dict[str, tuple[tk.Label, tk.Canvas]] = {}

    def select(name: str) -> None:
        for n, (lbl, bar) in labels.items():
            active = n == name
            lbl.config(fg=TEXT if active else DIMT,
                       font=(FONT, 10, "bold" if active else "normal"))
            bar.delete("all")
            if active:
                w = bar.winfo_reqwidth()
                for i in range(0, w, 3):
                    bar.create_line(i, 1, min(i + 3, w), 1,
                                    fill=signal_color(i / max(1, w)), width=2)
        on_select(name)

    for n in names:
        cell = tk.Frame(f, bg=bg)
        cell.pack(side="left", padx=(0, 18))
        lbl = tk.Label(cell, text=n, bg=bg, fg=DIMT, cursor="hand2",
                       font=(FONT, 10))
        lbl.pack()
        bar = tk.Canvas(cell, bg=bg, height=2, highlightthickness=0,
                        width=lbl.winfo_reqwidth())
        bar.pack(fill="x", pady=(3, 0))
        lbl.bind("<Button-1>", lambda e, n=n: select(n))
        labels[n] = (lbl, bar)
    return f, select
