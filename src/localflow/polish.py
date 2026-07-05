"""LLM cleanup ("polish") layer: strict prompts to a local llama-server.

The model only reformats — it must not invent content. On any failure the
caller falls back to the rule-cleaned text so a dictation is never lost.
"""

from __future__ import annotations

import re
import time

import requests

SYSTEM_PROMPT = (
    "You are the formatting layer for a local dictation app. Rewrite only to "
    "convert spoken dictation into text the user intended to type. Preserve "
    "meaning and facts. Do not add new information. Do not answer unless "
    "mode=command. Apply dictionary spellings exactly. Return only final text."
)

# Constant rules come FIRST so llama.cpp can prefix-cache them across calls;
# only the per-utterance fields below them get re-processed each time.
DICTATION_TEMPLATE = """MODE: dictation
Rules:
- Remove filler words and repeated false starts.
- Handle corrections like "actually", "scratch that", and restatements.
- Add punctuation and capitalization.
- Fix grammar slips (tense, subject-verb agreement, articles, plurals) without changing the meaning, tone, or word choice.
- Write numbers as digits, never as words:
  times: "three forty five pm" -> "3:45 PM", "half past two" -> "2:30", "ten o'clock" -> "10:00"
  dates: "july ninth" -> "July 9", "the twenty third of may" -> "May 23"
  money: "twenty dollars" -> "$20", "one fifty" (price) -> "$1.50"
  percent: "ninety five percent" -> "95%"
  phone numbers: digit groups, e.g. "020 555 7788"
  counts/quantities: "twenty three people" -> "23 people"
  Keep words for casual non-measurements: "one of them", "a couple".
- DICTIONARY entries may include "(sounds like: ...)" variants; if the transcript contains a variant, write the canonical term instead.
- Use a numbered or bulleted list when the speaker enumerates items (first/second/third, one/two/three), one item per line.
- For chat apps, omit a final period for short casual messages unless spoken.
- Do not replace unusual words unless the transcript clearly indicates a correction.
- If the user says punctuation by name, insert that punctuation.
- Return only the final text, nothing else.
APP_CATEGORY: {category}
STYLE: {style}
CLEANUP_LEVEL: {level}
DICTIONARY: {dictionary}
SNIPPETS: {snippets}
SURROUNDING_TEXT_BEFORE_CURSOR: {surrounding}
RAW_TRANSCRIPT:
{raw}"""

COMMAND_TEMPLATE = """MODE: command
SELECTED_TEXT:
{text}
SPOKEN_COMMAND:
{command}

Apply the spoken command to selected text. Preserve meaning unless the command asks to transform it. Return only replacement text."""

COMMAND_GEN_TEMPLATE = """MODE: command
SPOKEN_COMMAND:
{command}

No text is selected. Write the text the spoken command asks for (e.g. "write a short apology for missing the meeting" -> that apology). Plain text only: no markdown fences, no preamble, no explanation. Return only the text."""


def build_dictation_prompt(raw: str, *, level: str, category: str, style: str,
                           dictionary: list[str], snippets: list[tuple[str, str]],
                           custom_rules: str = "", surrounding: str = "") -> str:
    prompt = DICTATION_TEMPLATE.format(
        category=category or "other",
        style=style or "none",
        level=level,
        dictionary=", ".join(dictionary[:40]) if dictionary else "(none)",
        snippets="; ".join(f"{t} -> {e[:80]}" for t, e in snippets[:10]) if snippets else "(none)",
        surrounding=(surrounding or "(none)")[:300],
        raw=raw,
    )
    if custom_rules:
        prompt += "\nExtra user rules: " + custom_rules
    return prompt


class PolishError(Exception):
    pass


class LlamaClient:
    def __init__(self, port: int = 8735, timeout_s: float = 25.0):
        self.base = f"http://127.0.0.1:{port}"
        self.timeout_s = timeout_s

    def health(self) -> bool:
        try:
            r = requests.get(self.base + "/health", timeout=2)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def _chat(self, user_prompt: str, max_tokens: int) -> tuple[str, int]:
        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "stream": False,
            "cache_prompt": True,
        }
        t0 = time.perf_counter()
        r = requests.post(self.base + "/v1/chat/completions", json=payload,
                          timeout=self.timeout_s)
        ms = int((time.perf_counter() - t0) * 1000)
        r.raise_for_status()
        data = r.json()
        text = (data["choices"][0]["message"]["content"] or "").strip()
        return text, ms

    @staticmethod
    def _clean_output(text: str, fallback: str) -> str:
        if not text:
            return fallback
        # models sometimes wrap the answer in quotes or label it
        for prefix in ("Final text:", "FINAL TEXT:", "Output:", "Text:"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        if len(text) >= 2 and text[0] == '"' and text[-1] == '"' and '"' not in text[1:-1]:
            text = text[1:-1].strip()
        return text or fallback

    # dictations longer than this are polished in sentence chunks so a single
    # huge request can't blow the context window or the request timeout
    CHUNK_WORDS = 300

    def polish(self, raw: str, *, level: str, category: str, style: str,
               dictionary: list[str], snippets: list[tuple[str, str]],
               custom_rules: str = "", surrounding: str = "") -> tuple[str, int]:
        """Returns (final_text, llm_ms). Raises PolishError on failure."""
        if len(raw.split()) > self.CHUNK_WORDS:
            return self._polish_chunked(
                raw, level=level, category=category, style=style,
                dictionary=dictionary, snippets=snippets,
                custom_rules=custom_rules, surrounding=surrounding)
        prompt = build_dictation_prompt(
            raw, level=level, category=category, style=style,
            dictionary=dictionary, snippets=snippets,
            custom_rules=custom_rules, surrounding=surrounding)
        # constrain output length: roughly the input plus a small margin
        max_tokens = min(int(len(raw.split()) * 2.2) + 80, 1200)
        try:
            text, ms = self._chat(prompt, max_tokens)
        except requests.RequestException as e:
            raise PolishError(f"llama-server request failed: {e}") from e
        text = self._clean_output(text, raw)
        # sanity: reject runaway hallucinations
        if len(text) > max(len(raw) * 4, len(raw) + 400):
            raise PolishError("LLM output suspiciously long; using rule-cleaned text")
        # sanity: reject over-deletion (input is already rule-cleaned, so any
        # legitimate shrinkage at this stage is small). Each digit in the
        # output likely replaced a whole spoken word ("three forty five pm"
        # -> "3:45 PM"), so count digits toward the output budget.
        in_words, out_words = len(raw.split()), len(text.split())
        out_words += sum(ch.isdigit() for ch in text)
        if in_words >= 5 and out_words < in_words * 0.55:
            raise PolishError(
                f"LLM dropped too much text ({out_words}/{in_words} words); "
                "using rule-cleaned text")
        return text, ms

    def _polish_chunked(self, raw: str, **kw) -> tuple[str, int]:
        """Split on sentence boundaries into ~CHUNK_WORDS-word chunks and
        polish each; a chunk that fails falls back to its raw text so one bad
        chunk can't sink a three-minute dictation."""
        sentences = re.split(r"(?<=[.!?])\s+", raw)
        chunks: list[str] = []
        cur: list[str] = []
        n = 0
        for s in sentences:
            cur.append(s)
            n += len(s.split())
            if n >= self.CHUNK_WORDS:
                chunks.append(" ".join(cur))
                cur, n = [], 0
        if cur:
            chunks.append(" ".join(cur))

        # a chunk may still exceed the limit (one enormous unpunctuated
        # sentence); hard-split it by words so recursion always terminates
        sized: list[str] = []
        for chunk in chunks:
            w = chunk.split()
            if len(w) > self.CHUNK_WORDS:
                for i in range(0, len(w), self.CHUNK_WORDS):
                    sized.append(" ".join(w[i:i + self.CHUNK_WORDS]))
            else:
                sized.append(chunk)
        chunks = sized

        out: list[str] = []
        total_ms = 0
        for chunk in chunks:
            try:
                text, ms = self.polish(chunk, **kw)
            except PolishError:
                text, ms = chunk, 0
            out.append(text)
            total_ms += ms
        return " ".join(out), total_ms

    def command(self, selected_text: str, command: str) -> tuple[str, int]:
        if selected_text.strip():
            prompt = COMMAND_TEMPLATE.format(text=selected_text[:8000],
                                             command=command)
            max_tokens = min(int(len(selected_text.split()) * 2.5) + 200, 2000)
        else:
            prompt = COMMAND_GEN_TEMPLATE.format(command=command)
            max_tokens = 700
        try:
            text, ms = self._chat(prompt, max_tokens)
        except requests.RequestException as e:
            raise PolishError(f"llama-server request failed: {e}") from e
        return self._clean_output(text, selected_text), ms
