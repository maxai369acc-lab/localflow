"""Deterministic, rule-based cleanup that runs before (or instead of) the LLM.

Fast paths per spec section 10: fillers, punctuation-by-name, new line commands,
press-enter detection, snippet expansion, dictionary fuzzy replacement, simple
backtrack handling, chat trailing-period removal.

Nuanced fillers ("like", "you know"), list formatting and restatements are left
to the LLM at medium/high cleanup levels.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz


@dataclass
class RuleResult:
    text: str
    send_enter: bool = False
    edits: list[str] = field(default_factory=list)


# --- individual passes ----------------------------------------------------

_FILLER_RE = re.compile(r"(?<![\w'])(?:um+|uh+|uhm+|erm+|hmm+|mhm+)(?![\w'])[,.!?]?\s*", re.IGNORECASE)
_REPEAT_RE = re.compile(r"\b(\w+)(?:[,]?\s+\1\b)+", re.IGNORECASE)

_PRESS_ENTER_RE = re.compile(r"[\s,.!?]*(?:press|hit)\s+enter[\s,.!?]*$", re.IGNORECASE)

# spoken token -> (replacement, attaches_to_previous_word)
_PUNCT_MAP: list[tuple[re.Pattern, str, bool]] = [
    (re.compile(r"\bnew paragraph\b[,.]?", re.I), "\n\n", False),
    (re.compile(r"\bnew line\b[,.]?", re.I), "\n", False),
    (re.compile(r"\b(?:full stop|period)\b", re.I), ".", True),
    (re.compile(r"\bcomma\b", re.I), ",", True),
    (re.compile(r"\bquestion mark\b", re.I), "?", True),
    (re.compile(r"\bexclamation (?:mark|point)\b", re.I), "!", True),
    (re.compile(r"\bsemicolon\b", re.I), ";", True),
    (re.compile(r"\bcolon\b", re.I), ":", True),
    (re.compile(r"\bellipsis\b|\bdot dot dot\b", re.I), "...", True),
    (re.compile(r"\bopen (?:paren|parenthesis|bracket)\b", re.I), " (", False),
    (re.compile(r"\bclose (?:paren|parenthesis|bracket)\b", re.I), ")", True),
    (re.compile(r"\bopen quote\b", re.I), ' "', False),
    (re.compile(r"\bclose quote\b", re.I), '"', True),
]

_BACKTRACK_MARK_RE = re.compile(r"\b(?:actually|no wait|wait no|i mean)\b[,]?\s+", re.IGNORECASE)
_SCRATCH_RE = re.compile(
    r"(?:\b(?:no|actually|oh)\b[,]?\s+)?\b(?:scratch|strike|forget|delete) (?:that|it)\b[,.]?\s*",
    re.IGNORECASE,
)
_SENT_END_RE = re.compile(r"[.!?\n]")


def remove_fillers(text: str) -> tuple[str, bool]:
    changed = False
    new = _FILLER_RE.sub("", text)
    if new != text:
        changed = True
    def _keep_one(m: re.Match) -> str:
        return m.group(1)
    new2 = _REPEAT_RE.sub(_keep_one, new)
    if new2 != new:
        changed = True
    return new2, changed


def detect_press_enter(text: str) -> tuple[str, bool]:
    m = _PRESS_ENTER_RE.search(text)
    if m and m.start() > 0:
        return text[: m.start()].rstrip(), True
    return text, False


def apply_punct_names(text: str) -> tuple[str, bool]:
    changed = False
    for pat, repl, attach in _PUNCT_MAP:
        def _sub(m: re.Match) -> str:
            return repl
        new = pat.sub(_sub, text)
        if new != text:
            changed = True
            text = new
    if changed:
        # attach punctuation to the preceding word: "hello ." -> "hello."
        text = re.sub(r"\s+([.,!?;:)\]…]|\.\.\.)", r"\1", text)
        # ASR often already punctuated ("Friday period." -> "Friday..");
        # collapse runs, keeping the last (explicitly spoken) mark
        text = text.replace("...", "\x00")
        text = re.sub(r"[.!?,;:]{2,}", lambda m: m.group(0)[-1], text)
        text = text.replace("\x00", "...")
        text = re.sub(r"[ \t]{2,}", " ", text)
    return text, changed


def apply_scratch_that(text: str) -> tuple[str, bool]:
    """Remove the clause spoken before 'scratch that' (the previous sentence)."""
    changed = False
    while True:
        m = _SCRATCH_RE.search(text)
        if not m:
            break
        before, after = text[: m.start()], text[m.end():]
        b = before.rstrip()
        # drop the boundary punctuation right before the marker, then cut back
        # to the previous sentence end so the whole last sentence is removed
        if b and b[-1] in ".!?":
            b = b[:-1]
        ends = [mm.end() for mm in _SENT_END_RE.finditer(b)]
        cut = ends[-1] if ends else 0
        text = (b[:cut].rstrip() + " " + after.lstrip()).strip()
        changed = True
    return text, changed


def apply_actually_correction(text: str) -> tuple[str, bool]:
    """'lets do coffee at 2 actually 3' -> 'lets do coffee at 3'.

    Only fires when the phrase before and after the marker look like close
    alternatives; otherwise 'actually' is treated as a normal adverb.
    """
    matches = list(_BACKTRACK_MARK_RE.finditer(text))
    if not matches:
        return text, False
    m = matches[-1]
    before, after = text[: m.start()], text[m.end():]
    if not before.strip() or not after.strip():
        return text, False

    # ASR may close the sentence right before the marker ("...at 2. Actually 3");
    # drop that boundary so the previous sentence is treated as the X clause.
    b = before.rstrip()
    if b and b[-1] in ".!?":
        b = b[:-1]
    ends = [mm.end() for mm in _SENT_END_RE.finditer(b)]
    cut = ends[-1] if ends else 0
    head, x_clause = b[:cut], b[cut:].strip()
    y_clause = after.strip()
    if not x_clause:
        return text, False

    x_tok, y_tok = x_clause.split(), y_clause.split()
    if not x_tok or not y_tok:
        return text, False

    def _norm(tok: str) -> str:
        return tok.strip(".,!?;:").lower()

    def _numlike(tok: str) -> bool:
        return bool(re.fullmatch(r"[$€£#]?\d[\d:.,%\-]*(?:am|pm)?", tok))

    _STOP = {"i", "we", "you", "he", "she", "they", "it", "a", "an", "the",
             "is", "are", "was", "were", "and", "but", "or", "to", "of",
             "in", "on", "at", "so", "that", "this"}
    fire = False
    # short corrections: "... at 2 actually 3", "... Tuesday actually Wednesday"
    if len(y_tok) <= 3:
        xl, yf = _norm(x_tok[-1]), _norm(y_tok[0])
        if xl not in _STOP:
            if (_numlike(xl) and _numlike(yf)) or fuzz.ratio(xl, yf) >= 60:
                fire = True
    if not fire and len(x_tok) >= 2:
        # full restatements: compare tails of similar token length
        n = min(len(x_tok), len(y_tok), 8)
        x_tail = " ".join(x_tok[-n:]).lower()
        y_head = " ".join(y_tok[:n]).lower()
        score = fuzz.token_set_ratio(x_tail, y_head)
        if min(len(x_tail), len(y_head)) >= 8:
            score = max(score, fuzz.partial_ratio(x_tail, y_head))
        fire = score >= 55
    if not fire:
        return text, False

    # Keep the shared prefix of the X clause when Y only restates the tail:
    # X = "let's do coffee at 2", Y = "3"  ->  "let's do coffee at 3"
    if len(y_tok) < len(x_tok):
        keep = " ".join(x_tok[: len(x_tok) - len(y_tok)])
        merged = (keep + " " + y_clause).strip()
    else:
        merged = y_clause
    out = (head.rstrip() + " " + merged).strip() if head.strip() else merged
    return out, True


def expand_snippets(text: str, snippets: list[tuple[str, str]]) -> tuple[str, bool, bool]:
    """Returns (text, changed, whole_utterance_replaced)."""
    changed = False
    for trigger, expansion in snippets:
        if not trigger:
            continue
        if text.strip().lower().strip(".!?,") == trigger.lower():
            return expansion, True, True
        pat = re.compile(r"(?<![\w])" + re.escape(trigger) + r"(?![\w])", re.IGNORECASE)
        new = pat.sub(expansion.replace("\\", "\\\\"), text)
        if new != text:
            text, changed = new, True
    return text, changed, False


_FILE_EXTS = ("py|js|ts|tsx|jsx|json|md|txt|yml|yaml|toml|html|css|rs|go|"
              "java|cs|cpp|c|h|rb|php|sh|ps1|sql|env|cfg|ini|lock")
_FILE_TAG_RE = re.compile(
    r"\bat ([A-Za-z][\w\- ]{0,40}?) ?dot (" + _FILE_EXTS + r")\b", re.IGNORECASE)


def apply_file_tags(text: str, category: str) -> tuple[str, bool]:
    """Dev-mode file tagging: 'at app dot py' -> '@app.py' (code/terminal only)."""
    if category not in ("code", "terminal"):
        return text, False

    def _sub(m: re.Match) -> str:
        name = m.group(1).strip().replace(" ", "_")
        return f"@{name}.{m.group(2).lower()}"

    new = _FILE_TAG_RE.sub(_sub, text)
    return new, new != text


def apply_sound_alikes(text: str, pairs: list[tuple[str, str]]) -> tuple[str, bool]:
    """Replace known misheard forms with their canonical term.

    pairs = [(variant, term)], e.g. ("shy anne", "Cheyene"). Variants are what
    the ASR actually writes when the user says the term; matching is
    case-insensitive on whole-word boundaries.
    """
    changed = False
    for variant, term in pairs:
        if not variant or not term:
            continue
        pat = re.compile(r"(?<![\w])" + re.escape(variant) + r"(?![\w])",
                         re.IGNORECASE)
        new = pat.sub(term.replace("\\", "\\\\"), text)
        if new != text:
            text, changed = new, True
    return text, changed


_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")


def apply_dictionary(text: str, terms: list[str]) -> tuple[str, bool]:
    """Force exact dictionary spellings using fuzzy matching (spec 10)."""
    if not terms:
        return text, False
    changed = False
    single = [t for t in terms if " " not in t and len(t) >= 3]
    multi = [t for t in terms if " " in t]

    def _repl(m: re.Match) -> str:
        nonlocal changed
        w = m.group(0)
        for t in single:
            if w.lower() == t.lower():
                if w != t:
                    changed = True
                    return t
                return w
            if (len(w) >= 4 and w[0].lower() == t[0].lower()
                    and fuzz.ratio(w.lower(), t.lower()) >= 87):
                changed = True
                return t
        return w

    text = _WORD_RE.sub(_repl, text)

    for t in multi:
        n = len(t.split())
        words = text.split()
        for i in range(0, max(0, len(words) - n + 1)):
            window = " ".join(words[i:i + n])
            if fuzz.ratio(window.lower(), t.lower()) >= 88 and window != t:
                words[i:i + n] = t.split()
                text = " ".join(words)
                changed = True
                break
    return text, changed


def tidy_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]+([.,!?;:])", r"\1", text)
    text = re.sub(r"([.,!?;:])(?=[A-Za-z])", r"\1 ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def sentence_case(text: str) -> str:
    def _cap(m: re.Match) -> str:
        return m.group(1) + m.group(2).upper()
    text = re.sub(r"^(\W*)([a-z])", _cap, text)
    text = re.sub(r"([.!?]\s+|\n)([a-z])", _cap, text)
    return text


def chat_trailing_period(text: str, category: str) -> tuple[str, bool]:
    if (category == "chat" and len(text) <= 120 and "\n" not in text
            and text.endswith(".") and not text.endswith("..")
            and text.count(".") == 1):
        return text[:-1], True
    return text, False


# --- main entry -------------------------------------------------------------

def apply_rules(raw: str, *, level: str = "medium", category: str = "other",
                dictionary: list[str] | None = None,
                snippets: list[tuple[str, str]] | None = None,
                sound_alikes: list[tuple[str, str]] | None = None) -> RuleResult:
    """Deterministic pre-pass. level='off' returns raw text untouched."""
    res = RuleResult(text=(raw or "").strip())
    if level == "off" or not res.text:
        return res

    res.text, hit = detect_press_enter(res.text)
    if hit:
        res.send_enter = True
        res.edits.append("press enter")

    res.text, hit = remove_fillers(res.text)
    if hit:
        res.edits.append("removed fillers")

    res.text, hit = apply_scratch_that(res.text)
    if hit:
        res.edits.append("scratch that")

    res.text, hit = apply_actually_correction(res.text)
    if hit:
        res.edits.append("backtrack correction")

    res.text, hit = apply_punct_names(res.text)
    if hit:
        res.edits.append("spoken punctuation")

    res.text, hit, whole = expand_snippets(res.text, snippets or [])
    if hit:
        res.edits.append("snippet expansion")
    if whole:
        # exact text block requested — don't case-mangle or edit it further
        return res

    res.text, hit = apply_sound_alikes(res.text, sound_alikes or [])
    if hit:
        res.edits.append("sound-alike correction")

    res.text, hit = apply_dictionary(res.text, dictionary or [])
    if hit:
        res.edits.append("dictionary spelling")

    res.text = tidy_whitespace(res.text)
    res.text = sentence_case(res.text)

    # after tidy/case passes: '@app.py' must not be re-spaced or re-cased
    res.text, hit = apply_file_tags(res.text, category)
    if hit:
        res.edits.append("file tag")

    res.text, hit = chat_trailing_period(res.text, category)
    if hit:
        res.edits.append("chat: dropped trailing period")

    return res
