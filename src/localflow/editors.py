"""Embeddable panels: dictionary and snippet editors (Ink & Signal theme).

These builders MUST be called on the tk main thread (via Ui.call)."""

from __future__ import annotations

import tkinter as tk

from .theme import (CARD, FLOW, GROUND, LINE, TEXT, btn_ghost, btn_primary,
                    dim_label, entry, listbox)


def build_dictionary(parent, db) -> None:
    parent.configure(bg=CARD)
    lb = listbox(parent)
    lb.pack(fill="both", expand=True, padx=24, pady=(12, 8))
    data: list[tuple[str, str]] = []

    def refresh() -> None:
        nonlocal data
        lb.delete(0, "end")
        data = db.dictionary_entries()
        for term, sounds in data:
            lb.insert("end", f"{term}  ←  {sounds}" if sounds else term)

    form = tk.Frame(parent, bg=CARD)
    form.pack(fill="x", padx=24)
    dim_label(form, "Word (correct spelling):").grid(row=0, column=0, sticky="w")
    term_e = entry(form, width=24)
    term_e.grid(row=0, column=1, sticky="we", padx=8, ipady=4)
    dim_label(form, "Sounds like (what gets typed\ninstead, comma-separated):"
              ).grid(row=1, column=0, sticky="w", pady=(8, 0))
    sounds_e = entry(form, width=24)
    sounds_e.grid(row=1, column=1, sticky="we", padx=8, pady=(8, 0), ipady=4)
    form.columnconfigure(1, weight=1)

    btns = tk.Frame(parent, bg=CARD)
    btns.pack(fill="x", padx=24, pady=12)

    def add() -> None:
        db.add_term(term_e.get(), sounds_e.get())
        term_e.delete(0, "end")
        sounds_e.delete(0, "end")
        refresh()

    def remove() -> None:
        sel = lb.curselection()
        if sel and sel[0] < len(data):
            db.remove_term(data[sel[0]][0])
            refresh()

    def load_selected(_e=None) -> None:
        sel = lb.curselection()
        if sel and sel[0] < len(data):
            term, sounds = data[sel[0]]
            term_e.delete(0, "end")
            term_e.insert(0, term)
            sounds_e.delete(0, "end")
            sounds_e.insert(0, sounds)

    btn_primary(btns, "Add new word", add).pack(side="left")
    btn_ghost(btns, "Remove selected", remove).pack(side="left", padx=8)
    sounds_e.bind("<Return>", lambda e: add())
    lb.bind("<<ListboxSelect>>", load_selected)
    refresh()


def build_snippets(parent, db) -> None:
    parent.configure(bg=CARD)
    lb = listbox(parent)
    lb.pack(fill="both", expand=True, padx=24, pady=(12, 8))
    data: list[tuple[str, str]] = []

    def refresh() -> None:
        nonlocal data
        lb.delete(0, "end")
        data = db.snippets()
        for trig, exp in data:
            lb.insert("end", f"“{trig}”  →  {exp[:60]}")

    form = tk.Frame(parent, bg=CARD)
    form.pack(fill="x", padx=24)
    dim_label(form, "Trigger (spoken):").grid(row=0, column=0, sticky="w")
    trig_e = entry(form, width=30)
    trig_e.grid(row=0, column=1, sticky="we", padx=8, ipady=4)
    dim_label(form, "Expands to:").grid(row=1, column=0, sticky="nw",
                                        pady=(8, 0))
    exp_e = tk.Text(form, width=40, height=4, bg=GROUND, fg=TEXT,
                    insertbackground=FLOW, relief="flat",
                    highlightthickness=1, highlightbackground=LINE,
                    highlightcolor=FLOW, font=("Segoe UI", 10))
    exp_e.grid(row=1, column=1, sticky="we", padx=8, pady=8)
    form.columnconfigure(1, weight=1)

    btns = tk.Frame(parent, bg=CARD)
    btns.pack(fill="x", padx=24, pady=(0, 12))

    def add() -> None:
        db.add_snippet(trig_e.get(), exp_e.get("1.0", "end").strip())
        trig_e.delete(0, "end")
        exp_e.delete("1.0", "end")
        refresh()

    def remove() -> None:
        sel = lb.curselection()
        if sel and sel[0] < len(data):
            db.remove_snippet(data[sel[0]][0])
            refresh()

    btn_primary(btns, "Add new snippet", add).pack(side="left")
    btn_ghost(btns, "Remove selected", remove).pack(side="left", padx=8)
    refresh()
