"""Gọi Gemini CLI (đăng nhập bằng tài khoản Google / gói Gemini Pro) để xử lý phần chữ."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from . import config

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_cli() -> str | None:
    custom = config.env("GEMINI_CLI_PATH")
    if custom and Path(custom).exists():
        return custom
    for name in ("gemini.cmd", "gemini"):
        p = shutil.which(name)
        if p:
            return p
    appdata = os.environ.get("APPDATA")
    if appdata:
        p = Path(appdata) / "npm" / "gemini.cmd"
        if p.exists():
            return str(p)
    return None


def is_logged_in() -> bool:
    """Gemini CLI lưu thông tin đăng nhập Google tại ~/.gemini/oauth_creds.json."""
    return (Path.home() / ".gemini" / "oauth_creds.json").exists()


def status() -> dict:
    cli = find_cli()
    return {"installed": bool(cli), "logged_in": bool(cli) and is_logged_in(), "path": cli or ""}


def ask(prompt: str, cwd: Path, timeout: int = 240) -> str:
    """Gửi prompt qua stdin (tránh lỗi ký tự đặc biệt trên Windows), trả về câu trả lời dạng text."""
    cli = find_cli()
    if not cli:
        raise RuntimeError("Chưa cài Gemini CLI (npm install -g @google/gemini-cli)")
    if not is_logged_in():
        raise RuntimeError("Gemini CLI chưa đăng nhập. Chạy file login_gemini.bat một lần.")
    args = [cli, "-p", "Follow the instructions given in stdin exactly. Do not use any tools.",
            "--output-format", "json"]
    model = config.env("GEMINI_CLI_MODEL")
    if model:
        args += ["-m", model]
    env = {**os.environ}
    # đảm bảo CLI dùng tài khoản Google (gói Pro), không dùng API key trả phí
    env.pop("GEMINI_API_KEY", None)
    env.pop("GOOGLE_API_KEY", None)
    proc = subprocess.run(args, input=prompt, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=_NO_WINDOW, env=env)
    out = proc.stdout.strip()
    if proc.returncode != 0 and not out:
        raise RuntimeError(f"Gemini CLI lỗi: {(proc.stderr or '')[-600:]}")
    # --output-format json -> {"response": "...", "stats": {...}}
    start = out.find("{")
    if start != -1:
        try:
            data = json.loads(out[start:])
            if isinstance(data, dict):
                if data.get("error"):
                    raise RuntimeError(f"Gemini CLI lỗi: {data['error']}")
                if "response" in data:
                    return str(data["response"])
        except json.JSONDecodeError:
            pass
    return out
