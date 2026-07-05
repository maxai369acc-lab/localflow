"""Lifecycle manager for the local llama.cpp server (cleanup LLM)."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import requests

from .config import LOG_DIR

CREATE_NO_WINDOW = 0x08000000


class LlamaServerManager:
    def __init__(self, cfg: dict):
        llm = cfg["llm"]
        self.exe = Path(llm["server_exe"])
        self.model = Path(llm["model_path"])
        self.port = int(llm["port"])
        self.ctx = int(llm.get("ctx", 4096))
        self.threads = int(llm.get("threads", 6))
        self.base = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen | None = None
        self._log_handle = None

    def health(self) -> bool:
        try:
            return requests.get(self.base + "/health", timeout=2).status_code == 200
        except requests.RequestException:
            return False

    def preflight_error(self) -> str | None:
        if not self.exe.exists():
            return f"llama-server.exe not found at {self.exe}"
        if not self.model.exists():
            return f"LLM model not found at {self.model}"
        return None

    def ensure_running(self, timeout_s: float = 180.0) -> None:
        """Start llama-server if not already serving; block until healthy."""
        if self.health():
            return
        err = self.preflight_error()
        if err:
            raise RuntimeError(err)

        log_path = LOG_DIR / "llama-server.log"
        self._log_handle = open(log_path, "ab")
        args = [
            str(self.exe), "-m", str(self.model),
            "--host", "127.0.0.1", "--port", str(self.port),
            "-c", str(self.ctx), "-t", str(self.threads),
            "--jinja", "--no-webui",
        ]
        self.proc = subprocess.Popen(
            args, stdout=self._log_handle, stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        )
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"llama-server exited with code {self.proc.returncode}; see {log_path}")
            if self.health():
                self._warmup()
                return
            time.sleep(0.4)
        raise RuntimeError(f"llama-server did not become healthy in {timeout_s}s; see {log_path}")

    def _warmup(self) -> None:
        try:
            requests.post(
                self.base + "/v1/chat/completions",
                json={"model": "local", "max_tokens": 1, "temperature": 0,
                      "messages": [{"role": "user", "content": "ok"}]},
                timeout=60,
            )
        except requests.RequestException:
            pass

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None
