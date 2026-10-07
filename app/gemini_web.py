"""Gemini web adapter: browser profile riêng, thao tác UI, tải ảnh gốc.

Không dùng API nội bộ Google hay cookie của browser cá nhân.
Browser được giữ mở để người dùng đăng nhập/xử lý yêu cầu xác minh.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from . import config, images

JOB_LOCK = threading.Lock()
_BROWSER_LOCK = threading.Lock()
_process: subprocess.Popen | None = None
HOME = "https://gemini.google.com/app"


def _browser_executable() -> Path:
    custom = config.env("GEMINI_WEB_BROWSER")
    if custom:
        path = Path(custom)
        if path.is_file():
            return path
        raise RuntimeError("GEMINI_WEB_BROWSER không trỏ tới browser executable hợp lệ")
    for root in (os.getenv("PROGRAMFILES(X86)"), os.getenv("PROGRAMFILES"), os.getenv("LOCALAPPDATA")):
        if root:
            for relative in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
                path = Path(root) / relative
                if path.is_file():
                    return path
    raise RuntimeError("Cần cài Microsoft Edge hoặc Google Chrome để dùng Gemini web")


def status() -> dict:
    from importlib.util import find_spec

    try:
        _browser_executable()
        installed = True
    except RuntimeError:
        installed = False
    return {"installed": installed, "automation_installed": find_spec("playwright") is not None}


def _version(endpoint: str) -> dict:
    with urlopen(endpoint + "/json/version", timeout=1) as response:
        return json.load(response)


def _owns_browser_port() -> bool:
    """Xác thực profile từ process đang listen khi chưa có session marker."""
    command = (
        f"$listener = Get-NetTCPConnection -LocalPort {config.GEMINI_WEB_PORT} -State Listen -ErrorAction Stop | Select-Object -First 1; "
        "(Get-CimInstance Win32_Process -Filter ('ProcessId=' + $listener.OwningProcess)).CommandLine"
    )
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    match = re.search(r'--user-data-dir=(?:"([^"]+)"|([^\s]+))', result.stdout)
    if result.returncode != 0 or not match:
        return False
    profile = Path(match.group(1) or match.group(2)).resolve()
    return (profile == config.GEMINI_WEB_PROFILE.resolve() and
            f"--remote-debugging-port={config.GEMINI_WEB_PORT}" in result.stdout)


def open_browser() -> str:
    """Mở Edge/Chrome bình thường với remote debugging chỉ trên localhost."""
    global _process
    endpoint = f"http://127.0.0.1:{config.GEMINI_WEB_PORT}"
    session_file = config.GEMINI_WEB_PROFILE / "debug-session.json"
    with _BROWSER_LOCK:
        # Edge/Chrome có thể detach process cha. Nhận diện bằng CDP session id
        # đã lưu lúc tool tự mở browser, kể cả sau khi restart server.
        try:
            known = json.loads(session_file.read_text(encoding="utf-8"))
            if known.get("id") and known.get("endpoint") == endpoint and known["id"] == _version(endpoint).get("webSocketDebuggerUrl"):
                return endpoint
        except (OSError, ValueError):
            pass
        # Không attach nhầm một browser/service khác đang chiếm cổng.
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", config.GEMINI_WEB_PORT))
            except OSError as exc:
                if _owns_browser_port():
                    version = _version(endpoint)
                    if version.get("webSocketDebuggerUrl"):
                        session_file.write_text(json.dumps({"endpoint": endpoint, "id": version["webSocketDebuggerUrl"]}), encoding="utf-8")
                        return endpoint
                raise RuntimeError("Cổng Gemini browser đang được browser/service khác dùng. "
                                   "Đóng browser cũ hoặc đổi GEMINI_WEB_PORT trong .env.") from exc
        config.GEMINI_WEB_PROFILE.mkdir(exist_ok=True)
        _process = subprocess.Popen([
            str(_browser_executable()),
            f"--user-data-dir={config.GEMINI_WEB_PROFILE}",
            f"--remote-debugging-port={config.GEMINI_WEB_PORT}",
            "--remote-debugging-address=127.0.0.1", "--no-first-run", HOME,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                version = _version(endpoint)
                if version.get("webSocketDebuggerUrl"):
                    session_file.write_text(json.dumps({"endpoint": endpoint, "id": version["webSocketDebuggerUrl"]}), encoding="utf-8")
                    return endpoint
            except (OSError, ValueError):
                time.sleep(0.2)
        raise RuntimeError("Không mở được Gemini browser. Kiểm tra browser/profile rồi thử lại.")


def valid_chat_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc == "gemini.google.com" and parsed.path.startswith("/app/")


class GeminiWeb:
    def __init__(self, log):
        self.log = log
        self._pw = None
        self.page = None

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Thiếu Playwright. Chạy setup_gemini_web.bat rồi mở lại run.bat.") from exc
        try:
            endpoint = open_browser()
            self._pw = sync_playwright().start()
            self.browser = self._pw.chromium.connect_over_cdp(endpoint)
            self.context = self.browser.contexts[0]
            self.page = self.context.new_page()
            self.page.set_default_timeout(15000)
            return self
        except Exception:
            if self._pw:
                self._pw.stop()
            raise

    def __exit__(self, *_):
        # Giữ browser và tab để user xem lỗi/đăng nhập; chỉ ngắt kết nối.
        if self._pw:
            self._pw.stop()

    def _composer(self):
        selector = config.env("GEMINI_WEB_COMPOSER_SELECTOR", '[role="textbox"][contenteditable="true"]')
        return self.page.locator(selector).last

    def _signed_out(self) -> bool:
        return ("accounts.google.com" in self.page.url or
                self.page.get_by_role("button", name=re.compile(r"^(Sign in|Đăng nhập)$", re.I)).first.is_visible() or
                self.page.get_by_role("link", name=re.compile(r"^(Sign in|Đăng nhập)$", re.I)).first.is_visible())

    def begin(self, chat_url: str = "") -> None:
        self.page.goto(chat_url if valid_chat_url(chat_url) else HOME, wait_until="domcontentloaded")
        self.log("Đang mở Gemini web. Nếu cần, đăng nhập trong cửa sổ browser riêng của tool.")
        deadline = time.monotonic() + config.GEMINI_WEB_LOGIN_TIMEOUT
        while time.monotonic() < deadline:
            if not self._signed_out() and self._composer().is_visible():
                return
            self.page.wait_for_timeout(1000)
        raise RuntimeError("Gemini chưa sẵn sàng. Đăng nhập/xử lý xác minh trong cửa sổ browser của tool, "
                           "sau đó bấm Tiếp tục Gemini web. Không cần chạy lại kịch bản.")

    def _responses(self):
        return self.page.locator(config.env("GEMINI_WEB_RESPONSE_SELECTOR", "model-response"))

    def _stop_button(self):
        return self.page.get_by_role("button", name=re.compile(
            r"Stop response|Stop generating|Dừng phản hồi|Dừng tạo", re.I)).first

    def ask(self, prompt: str, record_chat) -> str:
        """Yêu cầu prompt JSON trong cùng conversation trước lượt tạo ảnh."""
        self._stop_button().wait_for(state="hidden", timeout=config.GEMINI_WEB_TIMEOUT * 1000)
        count = self._responses().count()
        self._composer().fill(prompt)
        send = self.page.get_by_role("button", name=re.compile(r"^(Send message|Send|Gửi tin nhắn|Gửi)$", re.I)).first
        if send.is_visible():
            send.click()
        else:
            self._composer().press("Enter")
        deadline = time.monotonic() + config.GEMINI_WEB_TIMEOUT
        previous = ""
        stable_since = time.monotonic()
        while time.monotonic() < deadline:
            if valid_chat_url(self.page.url):
                record_chat(self.page.url)
            if self._responses().count() > count:
                text = self._responses().nth(count).inner_text()
                if text != previous:
                    previous, stable_since = text, time.monotonic()
                if text.strip() and not self._stop_button().is_visible() and time.monotonic() - stable_since >= 3:
                    return text
            self.page.wait_for_timeout(1000)
        raise RuntimeError("Gemini không viết xong prompt đúng hạn. Kiểm tra browser rồi bấm Tiếp tục.")

    def generate(self, prompt: str, out_path: Path, record_chat) -> None:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        # Khi resume, chờ lượt cũ hoàn tất trước khi gửi cảnh mới.
        self._stop_button().wait_for(state="hidden", timeout=config.GEMINI_WEB_TIMEOUT * 1000)
        count = self._responses().count()
        self._composer().fill("Generate exactly one image for this scene. Keep recurring characters "
                              "consistent with previous images in this conversation. "
                              f"{prompt}. {images.NO_TEXT}")
        send = self.page.get_by_role("button", name=re.compile(r"^(Send message|Send|Gửi tin nhắn|Gửi)$", re.I)).first
        if send.is_visible():
            send.click()
        else:
            self._composer().press("Enter")
        deadline = time.monotonic() + config.GEMINI_WEB_TIMEOUT
        last_chat = ""
        while time.monotonic() < deadline:
            if valid_chat_url(self.page.url) and self.page.url != last_chat:
                last_chat = self.page.url
                record_chat(last_chat)
            if self._responses().count() > count:
                response = self._responses().nth(count)
                generated = response.locator("img").filter(visible=True)
                ready = generated.evaluate_all("es => es.some(e => e.complete && e.naturalWidth >= 256 && e.naturalHeight >= 256)")
                if ready and not self._stop_button().is_visible():
                    break
            self.page.wait_for_timeout(1000)
        else:
            raise RuntimeError("Gemini không trả về ảnh đúng hạn (có thể hết quota, bị từ chối hoặc cần xác minh). "
                               "Kiểm tra tab Gemini rồi bấm Tiếp tục. Ảnh các cảnh trước đã được giữ lại.")

        response.hover()
        selector = config.env("GEMINI_WEB_DOWNLOAD_SELECTOR")
        download = (response.locator(selector).last if selector else
                    response.get_by_role("button", name=re.compile(
                        r"Download.*(image|size)|Tải.*(ảnh|kích thước)|^Download$|^Tải xuống$", re.I)).last)
        raw_path = out_path.with_suffix(".download")
        try:
            with self.page.expect_download(timeout=60000) as pending:
                download.click(timeout=15000)
            pending.value.save_as(raw_path)
            # Xác thực ảnh và ghi atomically, không để resume bỏ qua ảnh hỏng.
            temp = out_path.with_suffix(".tmp.jpg")
            try:
                images.save_upload(raw_path.read_bytes(), temp)
                temp.replace(out_path)
            finally:
                temp.unlink(missing_ok=True)
        except PlaywrightTimeout as exc:
            raise RuntimeError("Không tìm thấy/tải được ảnh gốc từ Gemini. Kiểm tra tab Gemini; "
                               "có thể cần cập nhật selector theo README. Tool chưa dựng video.") from exc
        finally:
            raw_path.unlink(missing_ok=True)
