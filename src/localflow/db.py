"""SQLite storage: history, dictionary, snippets, styles, app rules, benchmarks."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .config import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  app_name TEXT,
  mode TEXT NOT NULL,
  audio_path TEXT,
  raw_text TEXT,
  final_text TEXT,
  asr_ms INTEGER,
  llm_ms INTEGER,
  pasted BOOLEAN,
  error TEXT
);

CREATE TABLE IF NOT EXISTS dictionary_terms (
  id INTEGER PRIMARY KEY,
  term TEXT NOT NULL UNIQUE,
  pronunciation_hint TEXT,
  source TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snippets (
  id INTEGER PRIMARY KEY,
  trigger TEXT NOT NULL UNIQUE,
  expansion TEXT NOT NULL,
  enabled BOOLEAN DEFAULT 1
);

CREATE TABLE IF NOT EXISTS styles (
  id INTEGER PRIMARY KEY,
  app_category TEXT NOT NULL UNIQUE,
  style_name TEXT NOT NULL,
  custom_rules TEXT
);

CREATE TABLE IF NOT EXISTS app_rules (
  id INTEGER PRIMARY KEY,
  process_name TEXT NOT NULL UNIQUE,
  category TEXT NOT NULL,
  paste_strategy TEXT DEFAULT 'clipboard'
);

CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY,
  history_id INTEGER,
  before_text TEXT,
  after_text TEXT,
  inferred_term TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS benchmarks (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  engine TEXT NOT NULL,
  llm_model TEXT,
  clip TEXT NOT NULL,
  audio_s REAL,
  asr_ms INTEGER,
  llm_ms INTEGER,
  total_ms INTEGER,
  raw_text TEXT,
  final_text TEXT
);
"""

DEFAULT_APP_RULES = [
    # chat
    ("discord.exe", "chat", "clipboard"), ("slack.exe", "chat", "clipboard"),
    ("teams.exe", "chat", "clipboard"), ("ms-teams.exe", "chat", "clipboard"),
    ("telegram.exe", "chat", "clipboard"), ("whatsapp.exe", "chat", "clipboard"),
    ("signal.exe", "chat", "clipboard"),
    # email
    ("outlook.exe", "email", "clipboard"), ("olk.exe", "email", "clipboard"),
    ("thunderbird.exe", "email", "clipboard"),
    # docs
    ("winword.exe", "doc", "clipboard"), ("notepad.exe", "doc", "clipboard"),
    ("obsidian.exe", "doc", "clipboard"), ("notion.exe", "doc", "clipboard"),
    # code
    ("code.exe", "code", "clipboard"), ("cursor.exe", "code", "clipboard"),
    ("windsurf.exe", "code", "clipboard"), ("devenv.exe", "code", "clipboard"),
    ("idea64.exe", "code", "clipboard"), ("pycharm64.exe", "code", "clipboard"),
    ("sublime_text.exe", "code", "clipboard"),
    # terminals (Shift+Insert is the safer paste there)
    ("windowsterminal.exe", "terminal", "shift_insert"), ("wt.exe", "terminal", "shift_insert"),
    ("cmd.exe", "terminal", "shift_insert"), ("powershell.exe", "terminal", "shift_insert"),
    ("pwsh.exe", "terminal", "shift_insert"), ("conhost.exe", "terminal", "shift_insert"),
    ("alacritty.exe", "terminal", "shift_insert"), ("wezterm-gui.exe", "terminal", "shift_insert"),
    # browsers
    ("chrome.exe", "browser", "clipboard"), ("msedge.exe", "browser", "clipboard"),
    ("firefox.exe", "browser", "clipboard"), ("brave.exe", "browser", "clipboard"),
    ("opera.exe", "browser", "clipboard"), ("vivaldi.exe", "browser", "clipboard"),
    # macOS app names (frontmost app localizedName, lowercased)
    ("discord", "chat", "clipboard"), ("slack", "chat", "clipboard"),
    ("telegram", "chat", "clipboard"), ("whatsapp", "chat", "clipboard"),
    ("messages", "chat", "clipboard"), ("signal", "chat", "clipboard"),
    ("mail", "email", "clipboard"), ("outlook", "email", "clipboard"),
    ("notes", "doc", "clipboard"), ("obsidian", "doc", "clipboard"),
    ("notion", "doc", "clipboard"), ("pages", "doc", "clipboard"),
    ("code", "code", "clipboard"), ("cursor", "code", "clipboard"),
    ("windsurf", "code", "clipboard"), ("xcode", "code", "clipboard"),
    ("terminal", "terminal", "clipboard"), ("iterm2", "terminal", "clipboard"),
    ("warp", "terminal", "clipboard"), ("ghostty", "terminal", "clipboard"),
    ("safari", "browser", "clipboard"), ("google chrome", "browser", "clipboard"),
    ("arc", "browser", "clipboard"), ("firefox", "browser", "clipboard"),
]

DEFAULT_STYLES = [
    ("chat", "casual", ""),
    ("email", "formal", ""),
    ("doc", "none", ""),
    ("code", "concise", ""),
    ("terminal", "none", ""),
    ("browser", "none", ""),
    ("other", "none", ""),
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path = DB_PATH):
        ensure_dirs()
        self._cx = sqlite3.connect(str(path), check_same_thread=False)
        self._cx.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        with self._lock:
            self._cx.executescript(SCHEMA)
            self._seed()
            self._cx.commit()

    def _seed(self) -> None:
        cur = self._cx.execute("SELECT COUNT(*) FROM app_rules")
        if cur.fetchone()[0] == 0:
            self._cx.executemany(
                "INSERT OR IGNORE INTO app_rules(process_name, category, paste_strategy) VALUES (?,?,?)",
                DEFAULT_APP_RULES,
            )
        cur = self._cx.execute("SELECT COUNT(*) FROM styles")
        if cur.fetchone()[0] == 0:
            self._cx.executemany(
                "INSERT OR IGNORE INTO styles(app_category, style_name, custom_rules) VALUES (?,?,?)",
                DEFAULT_STYLES,
            )

    # -- history ---------------------------------------------------------
    def history_start(self, app_name: str, mode: str, audio_path: str | None) -> int:
        with self._lock:
            cur = self._cx.execute(
                "INSERT INTO history(created_at, app_name, mode, audio_path) VALUES (?,?,?,?)",
                (_now(), app_name, mode, audio_path),
            )
            self._cx.commit()
            return int(cur.lastrowid)

    def history_finish(self, hid: int, raw: str | None, final: str | None,
                       asr_ms: int | None, llm_ms: int | None,
                       pasted: bool, error: str | None = None) -> None:
        with self._lock:
            self._cx.execute(
                "UPDATE history SET raw_text=?, final_text=?, asr_ms=?, llm_ms=?, pasted=?, error=? WHERE id=?",
                (raw, final, asr_ms, llm_ms, int(pasted), error, hid),
            )
            self._cx.commit()

    def history_last(self, n: int = 30) -> list[tuple]:
        with self._lock:
            cur = self._cx.execute(
                "SELECT id, created_at, app_name, mode, raw_text, final_text, asr_ms, llm_ms, pasted, error "
                "FROM history ORDER BY id DESC LIMIT ?", (n,))
            return cur.fetchall()

    def last_final_text(self) -> str | None:
        with self._lock:
            cur = self._cx.execute(
                "SELECT final_text FROM history WHERE final_text IS NOT NULL AND final_text != '' "
                "ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            return row[0] if row else None

    # -- dictionary ------------------------------------------------------
    def dictionary(self) -> list[str]:
        with self._lock:
            cur = self._cx.execute("SELECT term FROM dictionary_terms ORDER BY term")
            return [r[0] for r in cur.fetchall()]

    def dictionary_entries(self) -> list[tuple[str, str]]:
        """(term, sounds_like) pairs; sounds_like is a comma-separated list ('' if none)."""
        with self._lock:
            cur = self._cx.execute(
                "SELECT term, COALESCE(pronunciation_hint, '') FROM dictionary_terms ORDER BY term")
            return cur.fetchall()

    def add_term(self, term: str, sounds_like: str = "", source: str = "manual") -> None:
        term = term.strip()
        sounds_like = sounds_like.strip()
        if not term:
            return
        with self._lock:
            self._cx.execute(
                "INSERT INTO dictionary_terms(term, pronunciation_hint, source, created_at) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT(term) DO UPDATE SET pronunciation_hint=excluded.pronunciation_hint",
                (term, sounds_like, source, _now()))
            self._cx.commit()

    def remove_term(self, term: str) -> None:
        with self._lock:
            self._cx.execute("DELETE FROM dictionary_terms WHERE term=?", (term,))
            self._cx.commit()

    # -- snippets --------------------------------------------------------
    def snippets(self) -> list[tuple[str, str]]:
        with self._lock:
            cur = self._cx.execute("SELECT trigger, expansion FROM snippets WHERE enabled=1")
            return cur.fetchall()

    def add_snippet(self, trigger: str, expansion: str) -> None:
        trigger, expansion = trigger.strip(), expansion.strip()
        if not trigger or not expansion:
            return
        with self._lock:
            self._cx.execute(
                "INSERT INTO snippets(trigger, expansion, enabled) VALUES (?,?,1) "
                "ON CONFLICT(trigger) DO UPDATE SET expansion=excluded.expansion, enabled=1",
                (trigger, expansion))
            self._cx.commit()

    def remove_snippet(self, trigger: str) -> None:
        with self._lock:
            self._cx.execute("DELETE FROM snippets WHERE trigger=?", (trigger,))
            self._cx.commit()

    # -- styles / app rules ----------------------------------------------
    def style_for(self, category: str) -> tuple[str, str]:
        with self._lock:
            cur = self._cx.execute(
                "SELECT style_name, COALESCE(custom_rules,'') FROM styles WHERE app_category=?",
                (category,))
            row = cur.fetchone()
            return (row[0], row[1]) if row else ("none", "")

    def set_style(self, category: str, style_name: str) -> None:
        with self._lock:
            self._cx.execute(
                "INSERT INTO styles(app_category, style_name) VALUES (?,?) "
                "ON CONFLICT(app_category) DO UPDATE SET style_name=excluded.style_name",
                (category, style_name))
            self._cx.commit()

    def stats(self) -> dict:
        """Aggregates for Home/Insights: words, counts, per-app usage, streak."""
        with self._lock:
            cur = self._cx.execute(
                "SELECT COALESCE(final_text, raw_text, ''), app_name, "
                "llm_ms, substr(created_at, 1, 10) FROM history "
                "WHERE error IS NULL ORDER BY id DESC LIMIT 5000")
            rows = cur.fetchall()
        words = sum(len(t.split()) for t, *_ in rows)
        apps: dict[str, int] = {}
        days: set[str] = set()
        polished = 0
        for t, app, llm_ms, day in rows:
            if app:
                apps[app] = apps.get(app, 0) + 1
            days.add(day)
            if llm_ms:
                polished += 1
        # consecutive-day streak ending today (or yesterday)
        from datetime import date, timedelta
        streak, d = 0, date.today()
        if d.isoformat() not in days:
            d -= timedelta(days=1)
        while d.isoformat() in days:
            streak += 1
            d -= timedelta(days=1)
        top_apps = sorted(apps.items(), key=lambda kv: -kv[1])[:6]
        return {"total_words": words, "dictations": len(rows),
                "polished": polished, "apps": top_apps, "streak": streak}

    def app_rule(self, process_name: str) -> tuple[str, str] | None:
        with self._lock:
            cur = self._cx.execute(
                "SELECT category, paste_strategy FROM app_rules WHERE process_name=?",
                (process_name.lower(),))
            row = cur.fetchone()
            return (row[0], row[1]) if row else None

    # -- benchmarks --------------------------------------------------------
    def add_benchmark(self, engine: str, llm_model: str | None, clip: str, audio_s: float,
                      asr_ms: int, llm_ms: int | None, total_ms: int,
                      raw: str, final: str) -> None:
        with self._lock:
            self._cx.execute(
                "INSERT INTO benchmarks(created_at, engine, llm_model, clip, audio_s, asr_ms, llm_ms, total_ms, raw_text, final_text) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (_now(), engine, llm_model, clip, audio_s, asr_ms, llm_ms, total_ms, raw, final))
            self._cx.commit()

    def close(self) -> None:
        with self._lock:
            self._cx.close()
