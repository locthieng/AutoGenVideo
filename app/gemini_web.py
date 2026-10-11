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


def _browser_executable(prefix: str = "GEMINI_WEB") -> Path:
    custom = config.env(f"{prefix}_BROWSER")
    if custom:
        path = Path(custom)
        if path.is_file():
            return path
        raise RuntimeError(f"{prefix}_BROWSER không trỏ tới browser executable hợp lệ")
    for root in (os.getenv("PROGRAMFILES(X86)"), os.getenv("PROGRAMFILES"), os.getenv("LOCALAPPDATA")):
        if root:
            for relative in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
                path = Path(root) / relative
                if path.is_file():
                    return path
    raise RuntimeError("Cần cài Microsoft Edge hoặc Google Chrome để dùng browser automation")


def status(prefix: str = "GEMINI_WEB") -> dict:
    from importlib.util import find_spec

    try:
        _browser_executable(prefix)
        installed = True
    except RuntimeError:
        installed = False
    return {"installed": installed, "automation_installed": find_spec("playwright") is not None}


def _version(endpoint: str) -> dict:
    with urlopen(endpoint + "/json/version", timeout=1) as response:
        return json.load(response)


def _owns_browser_port(port: int | None = None, profile_dir: Path | None = None) -> bool:
    """Xác thực profile từ process đang listen khi chưa có session marker."""
    port = config.GEMINI_WEB_PORT if port is None else port
    profile_dir = config.GEMINI_WEB_PROFILE if profile_dir is None else profile_dir
    command = (
        f"$listener = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction Stop | Select-Object -First 1; "
        "(Get-CimInstance Win32_Process -Filter ('ProcessId=' + $listener.OwningProcess)).CommandLine"
    )
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    match = re.search(r'--user-data-dir=(?:"([^"]+)"|([^\s]+))', result.stdout)
    if result.returncode != 0 or not match:
        return False
    profile = Path(match.group(1) or match.group(2)).resolve()
    return (profile == profile_dir.resolve() and
            f"--remote-debugging-port={port}" in result.stdout)


def open_browser(*, prefix: str = "GEMINI_WEB", port: int | None = None,
                 profile: Path | None = None, home: str = HOME) -> str:
    """Mở Edge/Chrome bình thường với remote debugging chỉ trên localhost."""
    global _process
    port = config.GEMINI_WEB_PORT if port is None else port
    profile = config.GEMINI_WEB_PROFILE if profile is None else profile
    endpoint = f"http://127.0.0.1:{port}"
    session_file = profile / "debug-session.json"
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
                probe.bind(("127.0.0.1", port))
            except OSError as exc:
                if _owns_browser_port(port, profile):
                    version = _version(endpoint)
                    if version.get("webSocketDebuggerUrl"):
                        session_file.write_text(json.dumps({"endpoint": endpoint, "id": version["webSocketDebuggerUrl"]}), encoding="utf-8")
                        return endpoint
                raise RuntimeError("Cổng automation browser đang được browser/service khác dùng. "
                                   f"Đóng browser cũ hoặc đổi {prefix}_PORT trong .env.") from exc
        profile.mkdir(exist_ok=True)
        _process = subprocess.Popen([
            str(_browser_executable(prefix)),
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1", "--no-first-run", home,
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
        raise RuntimeError("Không mở được automation browser. Kiểm tra browser/profile rồi thử lại.")


def valid_chat_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc == "gemini.google.com" and parsed.path.startswith("/app/")


class GeminiWeb:
    name = "Gemini"
    prefix = "GEMINI_WEB"
    home = HOME
    composer_selector = '[role="textbox"][contenteditable="true"]'
    response_selector = "model-response"
    download_pattern = r"Download.*(image|size)|Tải.*(ảnh|kích thước)|^Download$|^Tải xuống$"

    @property
    def timeout(self):
        return config.GEMINI_WEB_TIMEOUT

    @property
    def login_timeout(self):
        return config.GEMINI_WEB_LOGIN_TIMEOUT

    def open_browser(self):
        return open_browser()

    def valid_chat_url(self, url):
        return valid_chat_url(url)

    def __init__(self, log):
        self.log = log
        self._pw = None
        self.page = None

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Thiếu Playwright. Chạy setup_web.bat rồi mở lại run.bat.") from exc
        try:
            endpoint = self.open_browser()
            self._pw = sync_playwright().start()
            self.browser = self._pw.chromium.connect_over_cdp(endpoint)
            self.context = self.browser.contexts[0]
            self.page = self.context.new_page()
            self.page.bring_to_front()
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
        selector = config.env(f"{self.prefix}_COMPOSER_SELECTOR", self.composer_selector)
        return self.page.locator(selector).last

    def _signed_out(self) -> bool:
        return ("accounts.google.com" in self.page.url or
                self.page.get_by_role("button", name=re.compile(r"^(Sign in|Đăng nhập)$", re.I)).first.is_visible() or
                self.page.get_by_role("link", name=re.compile(r"^(Sign in|Đăng nhập)$", re.I)).first.is_visible())

    def begin(self, chat_url: str = "") -> None:
        self.page.goto(chat_url if self.valid_chat_url(chat_url) else self.home, wait_until="domcontentloaded")
        self.log(f"Đang mở {self.name} web. Nếu cần, đăng nhập trong cửa sổ browser riêng của tool.")
        deadline = time.monotonic() + self.login_timeout
        next_log = time.monotonic()
        missing_since = None
        while time.monotonic() < deadline:
            signed_out = self._signed_out()
            if not signed_out:
                if self._composer().is_visible():
                    self.log(f"✅ {self.name} đã sẵn sàng; bắt đầu gửi prompt")
                    return
                if missing_since is None:
                    missing_since = time.monotonic()
                if time.monotonic() - missing_since >= 30:
                    raise RuntimeError(
                        f"Không tìm thấy ô nhập {self.name} sau 30 giây. "
                        f"Kiểm tra browser và {self.prefix}_COMPOSER_SELECTOR trong .env; "
                        "sau đó restart server và bấm Tiếp tục. Project đã được giữ lại.")
            else:
                missing_since = None
            if time.monotonic() >= next_log:
                reason = "chờ đăng nhập/xác minh" if signed_out else "chưa tìm thấy ô nhập; kiểm tra UI/selector"
                remaining = max(0, round(deadline - time.monotonic()))
                self.log(f"{self.name}: {reason} (timeout đăng nhập còn {remaining}s)")
                next_log = time.monotonic() + 10
            self.page.wait_for_timeout(1000)
        raise RuntimeError(f"{self.name} chưa sẵn sàng. Đăng nhập/xử lý xác minh trong browser của tool, "
                           f"sau đó bấm Tiếp tục {self.name} web. Không cần chạy lại kịch bản.")

    def _responses(self):
        return self.page.locator(config.env(f"{self.prefix}_RESPONSE_SELECTOR", self.response_selector))

    def _stop_button(self):
        return self.page.get_by_role("button", name=re.compile(
            r"Stop response|Stop generating|Dừng phản hồi|Dừng tạo", re.I)).first

    def _download_button(self, response):
        selector = config.env(f"{self.prefix}_DOWNLOAD_SELECTOR")
        return (response.locator(selector).last if selector else
                response.get_by_role("button", name=re.compile(self.download_pattern, re.I)).last)

    def latest_reply(self) -> str:
        """Đọc phản hồi đã hoàn tất khi resume conversation đã lưu."""
        self.log(f"Đang nhận diện phản hồi đã có trong conversation {self.name}")
        stop = self._stop_button()
        if stop.is_visible():
            self.log(f"Phát hiện nút dừng {self.name}: aria-label={stop.get_attribute('aria-label')!r}, "
                     f"data-testid={stop.get_attribute('data-testid')!r}")
            return ""
        self.log(f"Không có lượt đang tạo; đang tìm container phản hồi {self.name}")
        responses = self._responses()
        count = responses.count()
        if not count:
            from playwright.sync_api import TimeoutError as PlaywrightTimeout
            self.log(f"Chờ dữ liệu conversation {self.name} tải xong (tối đa 10s)")
            try:
                responses.first.wait_for(state="attached", timeout=10000)
                responses = self._responses()
                count = responses.count()
            except PlaywrightTimeout:
                pass
        self.log(f"Tìm thấy {count} container phản hồi {self.name}")
        if not count:
            return ""
        # Container được mount trước nội dung khi conversation hydrate.
        # Đợi JSON đầy đủ thay vì đọc placeholder 'ChatGPT said:'.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            responses.last.scroll_into_view_if_needed(timeout=5000)
            text = self._reply_text(responses.last)
            if images._parse_prompts(text, 1) is not None:
                return text
            self.page.wait_for_timeout(1000)
            responses = self._responses()
            if not responses.count():
                return ""
        return ""

    def ask(self, prompt: str, record_chat) -> str:
        """Yêu cầu prompt JSON trong cùng conversation trước lượt tạo ảnh."""
        from playwright.sync_api import Error as PlaywrightError
        self.log(f"Chờ lượt tạo trước kết thúc rồi gửi prompt {self.name}")
        self._stop_button().wait_for(state="hidden", timeout=self.timeout * 1000)
        count = self._responses().count()
        self._fill_prompt(prompt)
        self._send_prompt()
        deadline = time.monotonic() + self.timeout
        previous = ""
        stable_since = time.monotonic()
        next_log = time.monotonic()
        missing_since = time.monotonic()
        self.log(f"Đã gửi yêu cầu viết prompt; đang chờ phản hồi {self.name}")
        while time.monotonic() < deadline:
            if self.valid_chat_url(self.page.url):
                record_chat(self.page.url)
            responses = self._responses()
            response_count = responses.count()
            generating = self._stop_button().is_visible()
            if response_count > count:
                # ChatGPT có thể thêm unit reasoning trước unit trả lời cuối.
                try:
                    responses.last.scroll_into_view_if_needed(timeout=5000)
                    text = self._reply_text(responses.last)
                except PlaywrightError as exc:
                    if "not attached" not in str(exc) and "Timeout" not in str(exc):
                        raise
                    self.page.wait_for_timeout(1000)
                    continue
                if text != previous:
                    previous, stable_since = text, time.monotonic()
                if (images._parse_prompts(text, 1) is not None and not generating
                        and time.monotonic() - stable_since >= 3):
                    self.log(f"✅ Đã đọc phản hồi {self.name} ({len(text)} ký tự)")
                    return text
            elif generating:
                missing_since = time.monotonic()
            elif time.monotonic() - missing_since >= 30:
                raise RuntimeError(
                    f"Không nhận diện được lượt trả lời mới của {self.name} sau 30 giây. "
                    f"Kiểm tra {self.prefix}_RESPONSE_SELECTOR trong .env. "
                    "Conversation đã lưu; không cần tạo project mới.")
            if time.monotonic() >= next_log:
                self.log(f"Chờ prompt {self.name}: phát hiện {response_count} lượt assistant "
                         f"(trước gửi: {count}), đang tạo: {generating}, "
                         f"đã đọc {len(previous)} ký tự")
                next_log = time.monotonic() + 10
            self.page.wait_for_timeout(1000)
        raise RuntimeError(f"{self.name} không viết xong prompt đúng hạn. Kiểm tra browser rồi bấm Tiếp tục.")

    def generate(self, prompt: str, out_path: Path, record_chat) -> None:
        from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout

        self._close_viewer()
        # Khi resume, chờ lượt cũ hoàn tất trước khi gửi cảnh mới.
        self._stop_button().wait_for(state="hidden", timeout=self.timeout * 1000)
        count = self._responses().count()
        last_marker = self._last_turn_marker()
        self._fill_prompt("Generate exactly ONE final image for this scene (n=1). "
                              "Return a single finished image only. Do not create multiple images, "
                              "variants, alternatives, comparison pairs, grids or collages. "
                              "Do not ask me to choose or compare results; make all creative decisions yourself. "
                              "Keep recurring characters "
                              "consistent with previous images in this conversation. "
                              f"{prompt}. {images.NO_TEXT}")
        self._send_prompt()
        started = time.monotonic()
        deadline = started + self.timeout
        last_chat = ""
        next_log = time.monotonic()
        last_text = ""
        text_since = started
        response_count = count
        generating = False
        ready = False
        self.log(f"Đã gửi yêu cầu tạo ảnh {self.name}; giới hạn chờ {self.timeout}s")
        while time.monotonic() < deadline:
            if self.valid_chat_url(self.page.url) and self.page.url != last_chat:
                last_chat = self.page.url
                record_chat(last_chat)
            response_count = self._responses().count()
            current_marker = self._last_turn_marker()
            generating = self._stop_button().is_visible()
            alert = self._page_failure()
            if alert:
                raise RuntimeError(alert)
            is_new = (response_count > count) or (bool(last_marker) and current_marker != last_marker) or (count == 0 and response_count > 0)
            if is_new:
                response = self._responses().last
                try:
                    response.scroll_into_view_if_needed(timeout=5000)
                    ready = self._image_ready(response)
                    text = self._reply_text(response)
                except PlaywrightError as exc:
                    if "not attached" not in str(exc) and "Timeout" not in str(exc):
                        raise
                    self.page.wait_for_timeout(1000)
                    continue
                if text != last_text:
                    last_text, text_since = text, time.monotonic()
                failure = self._failure_message(text)
                if failure:
                    raise RuntimeError(failure)
                if ready and not generating:
                    break
                if (not ready and not generating and text.strip()
                        and self._response_complete(response)
                        and len(text.strip()) > 40
                        and time.monotonic() - text_since >= 5):
                    raise RuntimeError(
                        f"[NO_IMAGE] {self.name} đã kết thúc phản hồi nhưng không có ảnh hợp lệ. "
                        f"Phản hồi: {self._excerpt(text)}")
            if time.monotonic() >= next_log:
                self.log(f"Chờ ảnh {self.name}: đã chờ {round(time.monotonic() - started)}s/{self.timeout}s; "
                         f"còn {max(0, round(deadline - time.monotonic()))}s; "
                         f"lượt phản hồi {response_count} (trước gửi {count}); "
                         f"đang tạo: {generating}; ảnh hợp lệ: {ready}")
                next_log = time.monotonic() + 15
            self.page.wait_for_timeout(1000)
        else:
            raise RuntimeError(
                f"[TIMEOUT] {self.name}: hết giới hạn {self.timeout}s, "
                f"đã chờ {round(time.monotonic() - started)}s. "
                f"Lượt phản hồi {response_count} (trước gửi {count}); đang tạo: {generating}; "
                f"ảnh hợp lệ: {ready}. "
                + (f"Phản hồi cuối: {self._excerpt(last_text)}. " if last_text.strip() else "Chưa đọc được phản hồi mới. ")
                + "Chưa xác định được nguyên nhân từ trang; kiểm tra browser rồi bấm Tiếp tục.")

        response.hover()
        raw_path = out_path.with_suffix(".download")
        try:
            download = self._download_button(response)
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
            raise RuntimeError(f"[DOWNLOAD] {self.name}: đã thấy ảnh nhưng không tải được file. "
                               "Giới hạn chờ download 60s, click 15s. "
                               f"Chi tiết: {self._excerpt(str(exc))}") from exc
        finally:
            raw_path.unlink(missing_ok=True)
            self._close_viewer()

    def _close_viewer(self):
        pass

    def _last_turn_marker(self) -> str:
        return ""

    def _image_ready(self, response) -> bool:
        generated = response.locator("img").filter(visible=True)
        return bool(generated.evaluate_all("es => es.some(e => e.complete && e.naturalWidth >= 256 && e.naturalHeight >= 256)"))

    def _fill_prompt(self, prompt: str):
        self._composer().fill(prompt)

    def _reply_text(self, response):
        return response.inner_text(timeout=5000)

    def _response_complete(self, response):
        return response.get_attribute("data-talvt-turn-state") == "complete"

    @staticmethod
    def _excerpt(text):
        return re.sub(r"\s+", " ", text).strip()[:500]

    def _failure_message(self, text):
        patterns = (
            ("QUOTA", "hết quota/giới hạn tạo ảnh", r"(?:reached|hit|exceeded).{0,60}(?:limit|quota)|(?:image generation|image creation).{0,40}limit|too many requests|rate limit|hết (?:quota|lượt)|đã đạt.{0,40}giới hạn"),
            ("VERIFY", "cần đăng nhập/xác minh", r"verify (?:you are|you're|your identity)|verification required|sign in to continue|log in to continue|xác minh.{0,30}(?:danh tính|con người)|đăng nhập để tiếp tục"),
            ("REFUSED", "từ chối tạo ảnh", r"(?:cannot|can't|unable to).{0,35}(?:generate|create).{0,20}(?:image|picture)|(?:violates|violate).{0,30}(?:policy|policies)|không thể.{0,30}(?:tạo|vẽ).{0,15}ảnh"),
            ("SERVICE", "dịch vụ báo lỗi", r"something went wrong|error (?:generating|creating).{0,20}image|failed to generate|đã xảy ra lỗi|lỗi khi tạo ảnh"),
        )
        for code, reason, pattern in patterns:
            if re.search(pattern, text, re.I | re.S):
                return f"[{code}] {self.name}: {reason}. Thông báo trên trang: {self._excerpt(text)}"
        return ""

    def _page_failure(self):
        for alert in self.page.get_by_role("alert").filter(visible=True).all():
            failure = self._failure_message(alert.inner_text(timeout=1000))
            if failure:
                return failure
        return ""

    def _send_prompt(self):
        send = self.page.get_by_role("button", name=re.compile(r"^(Send message|Send|Gửi tin nhắn|Gửi)$", re.I)).first
        if send.is_visible():
            send.click()
        else:
            self._composer().press("Enter")
