"""Download / verify all local models: `uv run localflow-setup`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import WHISPER_DIR, load_config

LLAMA_RELEASES = "https://github.com/ggml-org/llama.cpp/releases"
QWEN_15B = ("https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/"
            "qwen2.5-1.5b-instruct-q4_k_m.gguf")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="localflow-setup")
    ap.add_argument("--parakeet", action="store_true",
                    help="also download Parakeet TDT 0.6B v3 (onnx, ~0.7 GB)")
    args = ap.parse_args(argv)

    cfg = load_config()
    ok = True

    print(f"[1/3] faster-whisper {cfg['asr']['whisper_model']} (int8, CPU)…")
    try:
        from faster_whisper import WhisperModel
        WhisperModel(cfg["asr"]["whisper_model"], device="cpu", compute_type="int8",
                     download_root=str(WHISPER_DIR))
        print(f"      ok -> {WHISPER_DIR}")
    except Exception as e:
        ok = False
        print(f"      FAILED: {e}")

    if args.parakeet:
        print("[2/3] Parakeet TDT 0.6B v3 (onnx-asr, int8)…")
        try:
            import onnx_asr
            onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", quantization="int8")
            print("      ok (cached under HF_HOME)")
        except Exception as e:
            ok = False
            print(f"      FAILED: {e}")
    else:
        print("[2/3] Parakeet skipped (pass --parakeet to fetch it)")

    print("[3/3] llama.cpp + cleanup LLM…")
    exe = Path(cfg["llm"]["server_exe"])
    model = Path(cfg["llm"]["model_path"])
    if not exe.exists():
        ok = False
        print(f"      MISSING {exe}\n      Get the win-cpu-x64 zip from {LLAMA_RELEASES}")
    else:
        print(f"      ok -> {exe}")
    if not model.exists():
        ok = False
        print(f"      MISSING {model}\n      Download: {QWEN_15B}")
    else:
        print(f"      ok -> {model} ({model.stat().st_size / 1e9:.2f} GB)")

    print("\nAll models ready." if ok else "\nSome models are missing — see above.")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
