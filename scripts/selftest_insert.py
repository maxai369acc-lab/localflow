"""End-to-end paste test: opens a tk window, pastes into it, verifies content.

Run:  uv run python scripts/selftest_insert.py
"""

import sys
import threading
import time
import tkinter as tk

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, "src")
from localflow.insert import insert_text  # noqa: E402

EXPECTED = "Local Flow paste self-test [OK] 123"


def main() -> int:
    root = tk.Tk()
    root.title("Local Flow insert self-test")
    root.geometry("420x120+200+200")
    root.attributes("-topmost", True)
    box = tk.Text(root, height=4)
    box.pack(fill="both", expand=True)
    result = {"ok": False, "got": ""}

    def worker():
        time.sleep(0.8)          # let the window take focus
        box.focus_force()
        insert_text(EXPECTED, strategy="clipboard", restore_clipboard=True)
        time.sleep(1.0)          # let the paste land + clipboard restore timer run

        def check():
            result["got"] = box.get("1.0", "end").strip()
            result["ok"] = result["got"] == EXPECTED
            root.destroy()
        root.after(0, check)

    root.after(100, lambda: root.focus_force())
    threading.Thread(target=worker, daemon=True).start()
    root.mainloop()

    print(f"pasted text: {result['got']!r}")
    print("PASS" if result["ok"] else "FAIL")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
