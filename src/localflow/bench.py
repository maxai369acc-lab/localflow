"""Phase 0 benchmark harness (spec section 13).

Generates spoken test clips with Windows SAPI TTS (so the pipeline can be
measured end-to-end without a human speaking), runs ASR -> rules -> LLM and
reports per-stage latency against the spec targets.

Usage:
    uv run localflow-bench                     # whisper + LLM cleanup
    uv run localflow-bench --engine both       # also try Parakeet (onnx-asr)
    uv run localflow-bench --no-llm
    uv run localflow-bench --llm-model "D:/AI/models/qwen2.5-3b-instruct-q4_k_m.gguf"
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import BENCH_DIR, load_config
from .db import DB
from .llm_server import LlamaServerManager
from .polish import LlamaClient, PolishError
from .rules import apply_rules, chat_trailing_period

CLIP_DIR = BENCH_DIR / "clips"

# (name, app_category, spoken text)
SAMPLES: list[tuple[str, str, str]] = [
    ("greeting", "doc", "Um, hope your week has started well. I was talking to Cheyene earlier about the launch."),
    ("backtrack", "chat", "Let's do coffee at 2, actually 3."),
    ("scratch", "doc", "Send the report on Friday. Scratch that. Send it on Monday morning."),
    ("list", "doc", "My goals are first finish the report, second send the slides, third follow up with the team."),
    ("chat_short", "chat", "sounds good, see you then"),
    ("email", "email", "Hi Sarah. Um, just following up on the invoice from last week. Could you, uh, send the updated version when you get a chance?"),
    ("punct", "doc", "The deadline is Friday period can we move it question mark"),
    ("newline", "doc", "First point new line second point new paragraph final block"),
    ("dev", "code", "Set user ID to none and restart the server."),
    ("long", "doc", "I think the main thing we need to focus on this quarter is improving the onboarding flow because, um, a lot of users drop off after the first screen, and, you know, if we can fix the empty states and add better tooltips, we should see activation go up by maybe ten percent."),
]

BENCH_DICTIONARY = ["Cheyene", "Supabase", "Tailscale"]


def tts_to_wav(text: str, out_path: Path) -> None:
    """Render text with Windows SAPI, then convert to 16 kHz mono via ffmpeg."""
    import pythoncom
    import win32com.client
    try:
        pythoncom.CoInitialize()
    except pythoncom.com_error:
        pass
    tmp = str(out_path) + ".sapi.wav"
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Open(tmp, 3, False)  # 3 = SSFMCreateForWrite
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    voice.AudioOutputStream = stream
    voice.Speak(text)
    stream.Close()
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", tmp,
         "-ar", "16000", "-ac", "1", str(out_path)],
        check=True)
    os.remove(tmp)


def ensure_clips() -> list[tuple[str, str, str, Path]]:
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for name, category, text in SAMPLES:
        wav = CLIP_DIR / f"{name}.wav"
        if not wav.exists():
            print(f"  generating clip: {name}")
            tts_to_wav(text, wav)
        out.append((name, category, text, wav))
    return out


def _mk_engines(cfg: dict, which: str):
    factories = []
    if which in ("whisper", "both"):
        def _whisper():
            from .asr import WhisperEngine
            a = cfg["asr"]
            return WhisperEngine(model=a["whisper_model"], compute_type=a["compute_type"],
                                 cpu_threads=int(a["cpu_threads"]),
                                 beam_size=int(a["beam_size"]))
        factories.append(("whisper", _whisper))
    if which in ("parakeet", "both"):
        def _parakeet():
            from .asr import ParakeetEngine
            return ParakeetEngine()
        factories.append(("parakeet", _parakeet))
    return factories


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="localflow-bench")
    ap.add_argument("--engine", choices=["whisper", "parakeet", "both"],
                    default="whisper")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--llm-model", help="path to a GGUF to use for cleanup")
    ap.add_argument("--llm-port", type=int,
                    help="use a different llama-server port (e.g. to compare "
                         "models while the app's own server keeps running)")
    ap.add_argument("--keep-server", action="store_true",
                    help="leave llama-server running afterwards")
    args = ap.parse_args(argv)

    cfg = load_config()
    if args.llm_model:
        cfg["llm"]["model_path"] = args.llm_model
    if args.llm_port:
        cfg["llm"]["port"] = args.llm_port
    llm_name = Path(cfg["llm"]["model_path"]).stem

    print("Generating/collecting test clips (SAPI TTS)…")
    clips = ensure_clips()

    client = None
    mgr = None
    started_server = False
    if not args.no_llm:
        mgr = LlamaServerManager(cfg)
        try:
            started_server = not mgr.health()
            print(f"Starting llama-server with {llm_name}…")
            mgr.ensure_running()
            client = LlamaClient(port=cfg["llm"]["port"], timeout_s=90)
        except Exception as e:
            print(f"  ! LLM unavailable, benchmarking ASR+rules only: {e}")
            client = None

    db = DB()
    all_rows = []
    for ename, factory in _mk_engines(cfg, args.engine):
        print(f"\nLoading ASR engine: {ename}…")
        t0 = time.perf_counter()
        try:
            eng = factory()
            eng.warmup()
        except Exception as e:
            print(f"  ! {ename} failed to load ({type(e).__name__}: {e}); skipping")
            continue
        print(f"  loaded in {time.perf_counter() - t0:.1f}s -> {eng.name}")

        for name, category, spoken, wav in clips:
            r = eng.transcribe(str(wav), dictionary=BENCH_DICTIONARY)
            rres = apply_rules(r.raw_text, level="medium", category=category,
                               dictionary=BENCH_DICTIONARY, snippets=[])
            final, llm_ms = rres.text, None
            if client is not None:
                try:
                    final, llm_ms = client.polish(
                        rres.text, level="medium", category=category,
                        style="none", dictionary=BENCH_DICTIONARY, snippets=[])
                    # mirror the app: re-apply the chat trailing-period rule
                    final, _ = chat_trailing_period(final.strip(), category)
                except PolishError as e:
                    print(f"  ! polish failed on {name}: {e}")
            total = r.asr_ms + (llm_ms or 0)
            row = dict(engine=eng.name, clip=name, audio_s=round(r.duration_s, 2),
                       asr_ms=r.asr_ms, rtf=r.rtf, llm_ms=llm_ms, total_ms=total,
                       raw=r.raw_text, final=final)
            all_rows.append(row)
            db.add_benchmark(eng.name, llm_name if llm_ms else None, name,
                             r.duration_s, r.asr_ms, llm_ms, total, r.raw_text, final)
            print(f"  {name:<11} audio={r.duration_s:5.2f}s asr={r.asr_ms:5d}ms "
                  f"rtf={r.rtf:5.2f} llm={llm_ms if llm_ms is not None else '  --'}ms "
                  f"total={total:5d}ms")

    if not all_rows:
        print("No results produced.")
        return 2

    # ---- report -----------------------------------------------------------
    lines = ["# Local Flow — Phase 0 benchmark", "",
             f"Machine: i5-12400 CPU-only • LLM: {llm_name if client else 'disabled'}", ""]
    for ename in {r["engine"] for r in all_rows}:
        rows = [r for r in all_rows if r["engine"] == ename]
        avg_asr = sum(r["asr_ms"] for r in rows) / len(rows)
        avg_total = sum(r["total_ms"] for r in rows) / len(rows)
        short = [r for r in rows if r["audio_s"] <= 8]
        avg_short = sum(r["total_ms"] for r in short) / max(1, len(short))
        lines += [f"## {ename}", "",
                  "| clip | audio s | asr ms | rtf | llm ms | total ms |",
                  "|---|---|---|---|---|---|"]
        for r in rows:
            lines.append(f"| {r['clip']} | {r['audio_s']} | {r['asr_ms']} | {r['rtf']} "
                         f"| {r['llm_ms'] if r['llm_ms'] is not None else '—'} | {r['total_ms']} |")
        lines += ["", f"avg ASR {avg_asr:.0f} ms · avg total {avg_total:.0f} ms · "
                      f"avg total (short clips) {avg_short:.0f} ms",
                  f"Spec exit criterion (<2000 ms stop→paste for short dictation): "
                  f"{'PASS' if avg_short < 2000 else 'FAIL'}", ""]
        lines += ["### Transcripts", ""]
        for r in rows:
            lines += [f"**{r['clip']}**", f"- raw: {r['raw']}", f"- final: {r['final']}", ""]

    report = BENCH_DIR / "bench_results.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport written to {report}")

    if mgr is not None and started_server and not args.keep_server:
        mgr.stop()
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
