"""Local ASR engines.

Primary: faster-whisper small.en int8 on CPU (spec tier B, reliable).
Optional: NVIDIA Parakeet TDT 0.6B v3 via onnx-asr (spec tier A, benchmark).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import WHISPER_DIR


@dataclass
class AsrResult:
    raw_text: str
    segments: list[dict] = field(default_factory=list)
    duration_s: float = 0.0
    asr_ms: int = 0
    rtf: float = 0.0
    engine: str = ""


class WhisperEngine:
    name = "faster-whisper"

    def __init__(self, model: str = "small.en", compute_type: str = "int8",
                 cpu_threads: int = 6, beam_size: int = 1, language: str = "en"):
        from faster_whisper import WhisperModel  # deferred: heavy import

        self.model_name = model
        self.language = language if model.endswith(".en") is False else "en"
        self.beam_size = beam_size
        kwargs = dict(device="cpu", compute_type=compute_type,
                      cpu_threads=cpu_threads, download_root=str(WHISPER_DIR))
        try:
            self.model = WhisperModel(model, **kwargs)
        except Exception:
            # offline / HF unreachable: use the already-downloaded copy
            self.model = WhisperModel(model, local_files_only=True, **kwargs)
        self.name = f"faster-whisper-{model}-{compute_type}"

    def warmup(self) -> None:
        silence = np.zeros(int(16000 * 0.6), dtype=np.float32)
        segs, _ = self.model.transcribe(silence, language=self.language, beam_size=1,
                                        vad_filter=False, without_timestamps=True)
        list(segs)

    def transcribe(self, audio: "np.ndarray | str | Path",
                   dictionary: list[str] | None = None) -> AsrResult:
        if isinstance(audio, (str, Path)):
            audio_in = str(audio)
        else:
            audio_in = audio
        initial_prompt = None
        if dictionary:
            initial_prompt = "Glossary: " + ", ".join(dictionary[:40]) + "."
        t0 = time.perf_counter()
        segs, info = self.model.transcribe(
            audio_in,
            language=self.language,
            beam_size=self.beam_size,
            temperature=0.0,
            vad_filter=True,
            condition_on_previous_text=False,
            without_timestamps=True,
            initial_prompt=initial_prompt,
        )
        out_segments, parts = [], []
        for s in segs:
            parts.append(s.text.strip())
            out_segments.append({"start": round(s.start, 2), "end": round(s.end, 2),
                                 "text": s.text.strip()})
        ms = int((time.perf_counter() - t0) * 1000)
        dur = float(getattr(info, "duration", 0.0) or 0.0)
        return AsrResult(
            raw_text=" ".join(p for p in parts if p).strip(),
            segments=out_segments,
            duration_s=dur,
            asr_ms=ms,
            rtf=round(ms / 1000 / dur, 3) if dur > 0 else 0.0,
            engine=self.name,
        )


class ParakeetEngine:
    """NVIDIA Parakeet TDT 0.6B v3 via onnx-asr (CPU, int8). Optional."""

    name = "parakeet-tdt-0.6b-v3-int8"

    def __init__(self):
        import onnx_asr  # optional extra

        self.model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", quantization="int8")

    def warmup(self) -> None:
        silence = np.zeros(int(16000 * 0.6), dtype=np.float32)
        try:
            self.model.recognize(silence, sample_rate=16000)
        except TypeError:
            pass  # older onnx-asr: numpy input unsupported; warmed on first file

    def transcribe(self, audio: "np.ndarray | str | Path",
                   dictionary: list[str] | None = None) -> AsrResult:
        t0 = time.perf_counter()
        if isinstance(audio, (str, Path)):
            text = self.model.recognize(str(audio))
            import wave
            with wave.open(str(audio), "rb") as w:
                dur = w.getnframes() / w.getframerate()
        else:
            text = self.model.recognize(audio.astype(np.float32), sample_rate=16000)
            dur = len(audio) / 16000
        ms = int((time.perf_counter() - t0) * 1000)
        return AsrResult(
            raw_text=(text or "").strip(),
            segments=[],
            duration_s=dur,
            asr_ms=ms,
            rtf=round(ms / 1000 / dur, 3) if dur > 0 else 0.0,
            engine=self.name,
        )


def make_engine(cfg: dict):
    a = cfg["asr"]
    if a.get("engine") == "parakeet":
        try:
            return ParakeetEngine()
        except Exception:
            pass  # fall back to whisper below
    return WhisperEngine(
        model=a.get("whisper_model", "small.en"),
        compute_type=a.get("compute_type", "int8"),
        cpu_threads=int(a.get("cpu_threads", 6)),
        beam_size=int(a.get("beam_size", 1)),
        language=a.get("language", "en"),
    )
