# Local Flow

Local, private, system-wide AI dictation for Windows — a Wispr Flow-style
voice input layer that runs **fully offline** on this PC (i5-12400, 16 GB,
CPU-only). Hold a hotkey, speak, release — polished text is pasted into
whatever app has focus.

Pipeline: **mic → faster-whisper (small.en, int8) → rule cleanup →
Qwen2.5-1.5B via llama.cpp → paste into active field**, with SQLite history,
a user dictionary, and voice snippets.

## Run

```powershell
cd "D:\LOCAL WISPR FLOW\localflow"
uv run localflow            # tray app (green mic icon appears in the tray)
uv run localflow --selftest         # check mic / models / clipboard / hooks
uv run localflow --selftest --full  # + start llama-server, test a polish round-trip
uv run localflow-bench              # Phase 0 latency benchmark (TTS test clips)
uv run localflow-setup              # download/verify models
```

First start takes a few seconds (speech model warm-up) and the cleanup LLM
loads in the background. The tray icon color shows state: gray = loading,
green = idle, red = recording, orange = processing.

## Hotkeys (defaults, spec §11)

| Action | Keys |
|---|---|
| Push-to-talk | hold **Ctrl+Win**, speak, release |
| Hands-free toggle | **Ctrl+Win+Space** (start / stop) |
| Cancel while recording | **Esc** |
| Paste last transcript | **Alt+Shift+Z** |

Notes:
- Pressing any other key during a Ctrl+Win hold aborts the dictation, so
  OS shortcuts like Win+Ctrl+←/→ don't create ghost recordings.
- Ctrl+Win+Space is also a Windows input-method shortcut; if you use
  multiple keyboard layouts, change the hands-free hotkey in config.
- Elevated (admin) windows can't receive keystrokes from a non-admin app —
  run Local Flow as admin if you dictate into admin terminals.

## Voice commands understood by the rules layer

- "**period / comma / question mark / exclamation point / colon / semicolon**…"
- "**new line**", "**new paragraph**"
- "**scratch that**" (removes previous sentence), "**… at 2 actually 3**"
- "**press enter**" at the end of an utterance sends Enter after pasting
- Snippet triggers expand to their saved text (tray → Snippets…)

Cleanup levels (tray menu): **Off** = raw transcript · **Light** = rules only ·
**Medium/High** = rules + local LLM polish (app-category aware: chat gets
casual style and no trailing period, email formal, etc.).

## Data locations (everything on D:)

| What | Where |
|---|---|
| Config | `D:\AI\local-flow\config.json` |
| History/dictionary/snippets DB | `D:\AI\local-flow\localflow.db` |
| Audio recordings (never-lose-audio) | `D:\AI\local-flow\audio\` |
| Logs (app + llama-server) | `D:\AI\local-flow\logs\` |
| Whisper models | `D:\AI\models\whisper\` |
| Cleanup LLM (GGUF) | `D:\AI\models\qwen2.5-1.5b-instruct-q4_k_m.gguf` |
| llama.cpp binaries | `D:\AI\llama.cpp\` |
| Benchmark clips/results | `D:\AI\local-flow\bench\` |

## Measured performance (this PC, i5-12400 CPU-only, 2026-07-04)

Stop-speaking → text-pasted for **short utterances** (3–7 s of speech),
cleanup level *medium* (rules + Qwen2.5-1.5B polish, prompt prefix-cached):

| ASR engine | ASR ms | + LLM ms | ≈ total stop→paste | notes |
|---|---|---|---|---|
| **parakeet-tdt-0.6b-v3 int8** | ~190–380 | ~300–1000 | **~0.9–1.4 s** | fastest (RTF 0.06); native punctuation; no glossary biasing, names rely on the dictionary fuzzy-replace |
| whisper small.en int8 (default) | ~1300–1450 | ~300–1000 | **~1.7–2.4 s** | best accuracy; glossary-biased names ("Cheyene") |
| whisper base.en int8 | ~470–540 | ~300–1000 | ~1.0–1.5 s | good middle ground |
| whisper tiny.en int8 | ~370 | ~300–1000 | ~0.9–1.4 s | weakest on real speech |

Enable Parakeet with `"asr": { "engine": "parakeet" }` in config (already
downloaded). Whisper stays the default until Parakeet is validated on real
microphone speech rather than TTS clips.

Cleanup LLM options (both downloaded to `D:\AI\models`):

- **Qwen2.5-1.5B** (default): ~350–800 ms polish, occasionally lazy about
  list formatting.
- **Qwen2.5-3B** (`"llm": { "model_path": ".../qwen2.5-3b-instruct-q4_k_m.gguf" }`):
  ~1.1–1.5 s, noticeably better formatting (true bulleted lists from
  "first… second… third", no over-edits in testing).

Suggested profiles: **speed** = Parakeet + 1.5B (~0.9–1.2 s) ·
**quality** = Parakeet + 3B (~1.5–1.9 s) · **conservative default** =
small.en + 1.5B (~1.7–2.4 s). Compare any combo with
`uv run localflow-bench --engine both --llm-model <gguf> --llm-port 8736`.

- LLM polish steady-state is ~350–650 ms for short texts (first call after a
  template change is ~1.2 s while llama.cpp builds the prompt cache).
- `Light` cleanup skips the LLM entirely: total ≈ ASR time alone.
- Long dictations (~18 s speech) run ~4.7 s total with small.en.
- Full details per clip: `D:\AI\local-flow\bench\bench_results.md`.

Switch to the speed profile by editing `D:\AI\local-flow\config.json`:
`"asr": { "whisper_model": "base.en", ... }` (model auto-downloads on first use).

## Config highlights (`D:\AI\local-flow\config.json`)

- `cleanup_level`: off | light | medium | high
- `asr.whisper_model`: `small.en` (default) or `medium.en` for more accuracy
- `asr.engine`: `whisper` or `parakeet` (after `localflow-setup --parakeet`)
- `llm.model_path`: point at the 3B GGUF for higher-quality cleanup
- `audio.device`: sounddevice input index (None = system default)
- `llm.enabled: false` for a pure rules-based, zero-LLM setup

## Architecture (matches spec §8)

```
src/localflow/
  app.py        orchestrator + state machine + selftest
  hotkeys.py    low-level keyboard hook (PTT chord, hands-free, esc, paste-last)
  audio.py      sounddevice capture, 16 kHz mono, WAV persistence
  asr.py        faster-whisper engine (+ optional Parakeet via onnx-asr)
  rules.py      deterministic cleanup pass (tested in tests/test_rules.py)
  polish.py     strict-prompt LLM cleanup client (llama-server /v1/chat)
  llm_server.py llama-server lifecycle (spawn, health, warmup, stop)
  insert.py     clipboard save → Ctrl+V / Shift+Insert → restore
  context.py    foreground app → category (chat/email/doc/code/terminal…)
  db.py         SQLite: history, dictionary, snippets, styles, app_rules
  ui.py         tray icon (pystray) + floating listening bar (tkinter)
  editors.py    history / dictionary / snippets windows
  bench.py      Phase 0 benchmark harness (SAPI TTS clips)
  server.py     optional FastAPI /transcribe + /polish (spec contracts)
```

## Roadmap vs spec

- [x] Phase 0 — models installed + benchmark harness
- [x] Phase 1 — tray MVP: PTT, ASR, cleanup, paste, history
- [x] Phase 2 (partial) — hands-free, cleanup levels, smart formatting,
      dictionary & snippets UI, app-category styles, press-enter
- [ ] Phase 3 — Command Mode (transform selected text) — prompts already in
      `polish.py` (`LlamaClient.command`), hotkey wiring pending
- [ ] Phase 4 — developer mode (IDE vocab, casing commands, file tagging)
- [ ] Phase 5 — optional BYOK cloud + Tauri shell (needs Rust; Node + MSVC
      are already installed)
