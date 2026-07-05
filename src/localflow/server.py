"""Optional HTTP services exposing the spec's API contracts (section 8).

Not needed by the tray app (which calls the engines in-process), but useful
for a future Tauri shell or external tooling.

Run:  uv run uvicorn localflow.server:app --host 127.0.0.1 --port 8730
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .asr import make_engine
from .config import load_config
from .polish import LlamaClient, PolishError
from .rules import apply_rules

app = FastAPI(title="Local Flow services", version="0.1.0")

_cfg = load_config()
_engine = None
_client = LlamaClient(port=_cfg["llm"]["port"], timeout_s=_cfg["llm"]["timeout_s"])


def _get_engine():
    global _engine
    if _engine is None:
        _engine = make_engine(_cfg)
        _engine.warmup()
    return _engine


class TranscribeRequest(BaseModel):
    audio_path: str
    language: str = "en"
    dictionary_terms: list[str] = Field(default_factory=list)
    mode: str = "dictation"


@app.post("/transcribe")
def transcribe(req: TranscribeRequest):
    try:
        res = _get_engine().transcribe(req.audio_path, dictionary=req.dictionary_terms)
    except FileNotFoundError:
        raise HTTPException(404, f"audio file not found: {req.audio_path}")
    return {
        "raw_text": res.raw_text,
        "segments": res.segments,
        "confidence": None,
        "rtf": res.rtf,
        "asr_ms": res.asr_ms,
        "engine": res.engine,
    }


class AppInfo(BaseModel):
    name: str = "unknown"
    category: str = "other"


class Snippet(BaseModel):
    trigger: str
    text: str


class PolishRequest(BaseModel):
    raw_text: str
    cleanup_level: str = "medium"
    app: AppInfo = AppInfo()
    surrounding_text: str = ""
    style: str = "none"
    dictionary: list[str] = Field(default_factory=list)
    snippets: list[Snippet] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)


@app.post("/polish")
def polish(req: PolishRequest):
    snippets = [(s.trigger, s.text) for s in req.snippets]
    rres = apply_rules(req.raw_text, level=req.cleanup_level,
                       category=req.app.category, dictionary=req.dictionary,
                       snippets=snippets)
    final, edits, llm_ms = rres.text, list(rres.edits), None
    if req.cleanup_level in ("medium", "high") and _client.health():
        try:
            final, llm_ms = _client.polish(
                rres.text, level=req.cleanup_level, category=req.app.category,
                style=req.style, dictionary=req.dictionary, snippets=snippets,
                custom_rules="; ".join(req.rules), surrounding=req.surrounding_text)
            edits.append("llm polish")
        except PolishError as e:
            edits.append(f"llm skipped: {e}")
    return {"final_text": final, "edits": edits, "llm_ms": llm_ms,
            "send_enter": rres.send_enter}


@app.get("/health")
def health():
    return {"status": "ok", "asr_loaded": _engine is not None,
            "llm_healthy": _client.health()}
