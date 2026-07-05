"""Developer mode: identifier extraction from user-granted workspace folders.

When the focused app is a code editor, the top identifiers from the user's
workspaces are fed into the dictation pipeline two ways:
  - as dictionary terms (ASR hint + LLM DICTIONARY field), and
  - as sound-alike pairs mapping the spoken form to the identifier,
    e.g. "get user by id" -> getUserById.

Pure regex, no parsing dependencies; scans are cheap and run off the main
thread. Folders are only ever read after the user grants them via the tray.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path

log = logging.getLogger("localflow")

IGNORE_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
               "__pycache__", "dist", "build", "out", "target", ".next",
               ".idea", ".vscode", "coverage", "vendor"}
CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".rs", ".go",
             ".java", ".kt", ".cs", ".cpp", ".cc", ".c", ".h", ".hpp",
             ".rb", ".php", ".swift", ".lua", ".sql", ".sh", ".ps1"}

_IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{4,39}\b")
_CAMEL_SPLIT_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")

# identifiers that are language noise, not vocabulary worth biasing toward
_COMMON = {
    "async", "await", "break", "catch", "class", "const", "continue",
    "default", "delete", "elif", "else", "except", "export", "extends",
    "false", "final", "finally", "float", "return", "import", "lambda",
    "match", "print", "public", "private", "protected", "raise", "range",
    "static", "struct", "super", "switch", "throw", "throws", "true",
    "typeof", "union", "unsigned", "value", "values", "while", "yield",
    "abstract", "assert", "boolean", "double", "enums", "global", "import",
    "instanceof", "interface", "module", "namespace", "native", "package",
    "params", "result", "results", "string", "number", "object", "array",
    "index", "count", "items", "item", "data", "error", "errors", "self",
    "this", "none", "null", "undefined", "function", "method", "field",
    "props", "state", "config", "options", "kwargs", "args",
}


def spoken_form(ident: str) -> str:
    """getUserById -> 'get user by id'; save_wav -> 'save wav'."""
    words: list[str] = []
    for part in re.split(r"[_\-]+", ident):
        words.extend(_CAMEL_SPLIT_RE.findall(part))
    return " ".join(w.lower() for w in words if w)


def scan_identifiers(folders: list[str], *, max_files: int = 500,
                     max_bytes: int = 200_000, top_n: int = 60) -> list[str]:
    """Frequency-ranked identifiers across all granted workspace folders."""
    freq: Counter[str] = Counter()
    seen_files = 0
    for folder in folders:
        root = Path(folder)
        if not root.is_dir():
            continue
        stack = [root]
        while stack and seen_files < max_files:
            d = stack.pop()
            try:
                children = sorted(d.iterdir())
            except OSError:
                continue
            for child in children:
                if child.is_dir():
                    if child.name.lower() not in IGNORE_DIRS:
                        stack.append(child)
                    continue
                if child.suffix.lower() not in CODE_EXTS:
                    continue
                if seen_files >= max_files:
                    break
                seen_files += 1
                try:
                    text = child.read_bytes()[:max_bytes].decode(
                        "utf-8", errors="ignore")
                except OSError:
                    continue
                # filename stem is vocabulary too ("app.py" -> app)
                freq[child.stem] += 2
                for m in _IDENT_RE.finditer(text):
                    freq[m.group(0)] += 1

    # Only multi-word identifiers (camelCase/snake_case) are worth biasing:
    # plain single words ("audio", "model") are already in the ASR's grasp.
    ranked = [w for w, c in freq.most_common(400)
              if c >= 2 and w.lower() not in _COMMON
              and not w.isupper()          # skip SCREAMING_CONSTANTS noise
              and len(spoken_form(w).split()) >= 2]
    # de-dup case-insensitively, keep first (highest-frequency) casing
    out, seen = [], set()
    for w in ranked:
        if w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
        if len(out) >= top_n:
            break
    log.info("devmode: scanned %d files -> %d identifiers", seen_files, len(out))
    return out


def sound_alike_pairs(identifiers: list[str]) -> list[tuple[str, str]]:
    """(spoken form, identifier) for names whose spoken form differs.

    'get user by id' -> getUserById. Single plain lowercase words map to
    themselves and are skipped.
    """
    pairs = []
    for ident in identifiers:
        sp = spoken_form(ident)
        if sp and sp != ident.lower() and len(sp.split()) >= 2:
            pairs.append((sp, ident))
    return pairs
