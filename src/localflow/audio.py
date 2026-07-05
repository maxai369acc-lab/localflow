"""Microphone capture via sounddevice (WASAPI), 16 kHz mono float32."""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

TARGET_SR = 16000


def list_input_devices() -> list[tuple[int, str]]:
    out = []
    try:
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0:
                out.append((i, d["name"]))
    except Exception:
        pass
    return out


def default_input_device() -> str | None:
    try:
        idx = sd.default.device[0]
        if idx is not None and idx >= 0:
            return sd.query_devices(idx)["name"]
    except Exception:
        pass
    return None


class Recorder:
    """Push-to-talk style recorder: start() ... stop() -> 16 kHz mono float32."""

    def __init__(self, device: int | None = None, on_level=None):
        self.device = device
        self.on_level = on_level
        self._stream: sd.InputStream | None = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._recording = False
        self._native_sr = TARGET_SR
        self.started_at: float = 0.0

    def _callback(self, indata, frames, t, status):
        if not self._recording:
            return
        mono = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
        with self._lock:
            self._chunks.append(mono)
        if self.on_level is not None:
            rms = float(np.sqrt(np.mean(mono ** 2)) if len(mono) else 0.0)
            try:
                self.on_level(rms)
            except Exception:
                pass

    def start(self) -> None:
        if self._recording:
            return
        self._chunks = []
        last_err: Exception | None = None
        for sr in (TARGET_SR, None):  # None -> device default rate
            try:
                self._stream = sd.InputStream(
                    samplerate=sr, channels=1, dtype="float32",
                    device=self.device, callback=self._callback,
                    blocksize=0,
                )
                self._stream.start()
                self._native_sr = int(self._stream.samplerate)
                last_err = None
                break
            except Exception as e:  # PortAudioError on unsupported rate
                last_err = e
                self._stream = None
        if last_err is not None:
            raise RuntimeError(f"Could not open microphone: {last_err}")
        self._recording = True
        self.started_at = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at if self._recording else 0.0

    def stop(self) -> np.ndarray:
        """Stop and return the whole utterance resampled to 16 kHz."""
        self._recording = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(self._chunks)
            self._chunks = []
        if self._native_sr != TARGET_SR and len(audio) > 1:
            n_out = int(len(audio) * TARGET_SR / self._native_sr)
            x_old = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
            x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
            audio = np.interp(x_new, x_old, audio).astype(np.float32)
        return audio.astype(np.float32)

    def cancel(self) -> None:
        self._recording = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        with self._lock:
            self._chunks = []


def peak_rms(audio: np.ndarray, win: int = 1600) -> float:
    """Max RMS over 100 ms windows — used as a 'was there any speech' gate."""
    if len(audio) == 0:
        return 0.0
    n = max(1, len(audio) // win)
    vals = [float(np.sqrt(np.mean(audio[i * win:(i + 1) * win] ** 2)))
            for i in range(n)]
    return max(vals) if vals else 0.0


def save_wav(audio: np.ndarray, path: str | Path, sr: int = TARGET_SR) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
