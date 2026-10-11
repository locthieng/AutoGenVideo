"""ChatGPT web: profile riêng, chỉ thao tác UI và tải ảnh bằng nút download."""
from __future__ import annotations

import re
import threading
from urllib.parse import urlparse

from . import config, gemini_web, images

HOME = "https://chatgpt.com/"
JOB_LOCK = threading.Lock()


def status() -> dict:
    return gemini_web.status("CHATGPT_WEB")


def open_browser() -> str:
    return gemini_web.open_browser(prefix="CHATGPT_WEB", port=config.CHATGPT_WEB_PORT,
                                   profile=config.CHATGPT_WEB_PROFILE, home=HOME)


def valid_chat_url(url: str) -> bool:
    parsed = urlparse(url)
    return (parsed.scheme == "https" and parsed.netloc == "chatgpt.com"
            and re.fullmatch(r"/c/[a-zA-Z0-9-]+", parsed.path) is not None)


class ChatGPTWeb(gemini_web.GeminiWeb):
    name = "ChatGPT"
    prefix = "CHATGPT_WEB"
    home = HOME
    composer_selector = '#prompt-textarea'
    response_selector = '[data-message-author-role="assistant"]'
    download_pattern = r"Download|Save image|Save$|Tải xuống|Tải ảnh|Lưu ảnh|^Lưu$"

    @property
    def timeout(self):
        return config.CHATGPT_WEB_TIMEOUT

    @property
    def login_timeout(self):
        return config.CHATGPT_WEB_LOGIN_TIMEOUT

    def open_browser(self):
        return open_browser()

    def valid_chat_url(self, url):
        return valid_chat_url(url)

    def begin(self, chat_url: str = ""):
        super().begin(chat_url)
        if valid_chat_url(chat_url):
            from playwright.sync_api import TimeoutError as PlaywrightTimeout
            try:
                self._responses().first.wait_for(state="attached", timeout=10000)
            except PlaywrightTimeout:
                self.log("Conversation chưa có container assistant; sẽ kiểm tra khi đọc prompt")

    def _composer(self):
        custom = config.env(f"{self.prefix}_COMPOSER_SELECTOR")
        if custom:
            return self.page.locator(custom).filter(visible=True).last
        # Các phiên bản ChatGPT có thể dùng textarea hoặc contenteditable,
        # không phải phiên bản nào cũng giữ id prompt-textarea.
        preferred = self.page.locator(
            '#prompt-textarea, textarea[placeholder="Ask ChatGPT"], '
            '[role="textbox"][aria-label="Ask ChatGPT"]'
        ).filter(visible=True)
        if preferred.count():
            return preferred.last
        editable = self.page.locator(
            'textarea:not([disabled]), [contenteditable="true"]'
        ).filter(visible=True)
        if editable.count() > 1:
            raise RuntimeError("Có nhiều ô nhập ChatGPT; cần cấu hình CHATGPT_WEB_COMPOSER_SELECTOR trong .env")
        return editable.first

    def _signed_out(self) -> bool:
        return (urlparse(self.page.url).hostname != "chatgpt.com" or
                self.page.get_by_role("button", name=re.compile(
                    r"^(Log in|Sign in|Đăng nhập)$", re.I)).first.is_visible() or
                self.page.get_by_role("link", name=re.compile(
                    r"^(Log in|Sign in|Đăng nhập)$", re.I)).first.is_visible())

    def _fill_prompt(self, prompt: str):
        editor = self._composer()
        tag = editor.evaluate("e => e.tagName.toLowerCase()")
        if tag in ("textarea", "input"):
            editor.fill(prompt)
        else:
            # ProseMirror cần input event qua browser để cập nhật editor state.
            editor.click()
            editor.press("ControlOrMeta+A")
            self.page.keyboard.insert_text(prompt)

    def _send_prompt(self):
        from playwright.sync_api import expect, TimeoutError as PlaywrightTimeout

        editor = self._composer()
        button = self.page.get_by_role("button", name=re.compile(
            r"^(Send|Send message|Send prompt|Gửi|Gửi tin nhắn)$", re.I
        )).filter(visible=True).last
        try:
            expect(button).to_be_enabled(timeout=30000)
            button.click(timeout=15000, no_wait_after=True)
            # Enter có thể chỉ thêm dòng mới. Chỉ báo đã gửi khi UI xác nhận
            # bằng cách xóa nội dung composer; không tự gửi lại gây trùng lượt.
            tag = editor.evaluate("e => e.tagName.toLowerCase()")
            if tag in ("textarea", "input"):
                expect(editor).to_have_value("", timeout=15000)
            else:
                expect(editor).to_have_text(re.compile(r"^\s*$"), timeout=15000)
        except (AssertionError, PlaywrightTimeout) as exc:
            raise RuntimeError(
                "ChatGPT chưa xác nhận gửi prompt: nút Send chưa sẵn sàng hoặc "
                "nội dung vẫn còn trong ô nhập. Kiểm tra cửa sổ ChatGPT trước khi tiếp tục."
            ) from exc

    def _stop_button(self):
        return self.page.locator(
            '[data-testid="stop-button"], button[aria-label="Stop streaming"], '
            'button[aria-label="Stop generating"], button[aria-label="Stop"], '
            'button[aria-label="Dừng tạo"]'
        ).first

    def _responses(self):
        # Download controls có thể nằm ngoài message body nhưng trong cùng lượt trả lời.
        selector = config.env(f"{self.prefix}_RESPONSE_SELECTOR")
        if selector:
            return self.page.locator(selector)
        # Nhận diện assistant trước; không yêu cầu đồng thời cả hai thuộc tính
        # data-testid và data-message-author-role như adapter cũ.
        markers = (
            '[data-message-author-role="assistant"], [data-turn="assistant"], '
            '[data-role="assistant"], [data-testid="assistant-message"], '
            '[data-content-search-unit-key$=":assistant"]'
        )
        turns = self.page.locator('article[data-turn="assistant"]')
        if turns.count():
            return turns
        assistant = self.page.locator(markers)
        # UI mới dùng container turn cho ảnh; image reply không có marker
        # :assistant như text reply. Đếm cùng cấp container để không lệch baseline.
        content = self.page.locator(
            markers + ', [data-testid="generated-image-gallery"], [data-testid="generated-image-preview"]'
        )
        turns = self.page.locator('[data-talvt-turn-state]').filter(has=content)
        if turns.count():
            return turns
        for selector in ('article', '[data-testid^="conversation-turn-"]'):
            turns = self.page.locator(selector).filter(has=assistant)
            if turns.count():
                return turns
        return assistant

    def _close_viewer(self):
        try:
            close_btn = self.page.locator(
                'button[aria-label="Close viewer"], button[aria-label="Đóng"], '
                'button[aria-label="Close"]'
            ).filter(visible=True).first
            if close_btn.count() and close_btn.is_visible():
                close_btn.click(timeout=2000)
                self.page.wait_for_timeout(300)
            else:
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(200)
        except Exception:
            pass

    def _last_turn_marker(self) -> str:
        try:
            marker = self._responses().last.evaluate(
                'e => e.closest("[data-turn-key]")?.getAttribute("data-turn-key") || '
                'e.closest("[data-content-search-turn-key]")?.getAttribute("data-content-search-turn-key") || '
                'e.getAttribute("data-chatgpt-search-message-ids") || '
                'e.querySelector("[data-message-id]")?.getAttribute("data-message-id") || '
                'e.getAttribute("id") || (e.innerText || "").slice(0, 100)',
                timeout=1000,
            )
            if hasattr(marker, "_mock_return_value") or marker is None:
                return ""
            return str(marker)
        except Exception:
            return ""

    def _find_generated_image(self, response):
        previews = response.locator(
            '[data-testid="generated-image-preview"] img, [data-testid="generated-image-gallery"] img'
        ).filter(visible=True)
        try:
            if previews.count():
                return previews
        except (TypeError, Exception):
            pass
        imgs = response.locator("img").filter(visible=True)
        try:
            not_orig = imgs.evaluate_all(
                "es => es.map((e, i) => {"
                "  const alt = (e.getAttribute('alt') || '').toLowerCase();"
                "  const aria = (e.getAttribute('aria-label') || '').toLowerCase();"
                "  const pAria = (e.parentElement?.getAttribute('aria-label') || '').toLowerCase();"
                "  return (alt.includes('original') || aria.includes('original') || pAria.includes('original')) ? -1 : i;"
                "}).filter(i => i >= 0)"
            )
            if not_orig:
                return imgs.nth(not_orig[-1])
        except Exception:
            pass
        return imgs

    def _image_ready(self, response) -> bool:
        gen = self._find_generated_image(response)
        try:
            return bool(gen.evaluate_all(
                "es => es.some(e => e.complete && e.naturalWidth >= 256 && e.naturalHeight >= 256)"
            ))
        except Exception:
            return False

    def _download_button(self, response):
        self._dismiss_image_comparison()
        if config.env(f"{self.prefix}_DOWNLOAD_SELECTOR"):
            return super()._download_button(response)

        self._close_viewer()

        # Kiểm tra nếu đã có nút download trực tiếp trên turn
        direct = response.locator('button').filter(has_text=re.compile(self.download_pattern, re.I))
        if direct.count() and direct.first.is_visible():
            return self._single_image_download(direct.first)

        # Tìm preview ảnh đã sinh trong response
        preview = response.locator('[data-testid="generated-image-preview"]').filter(visible=True).first
        if not preview.count() or not preview.is_visible():
            gen = self._find_generated_image(response)
            if gen.count():
                preview = gen.first
            else:
                preview = response.locator("img").filter(visible=True).first

        preview.scroll_into_view_if_needed(timeout=5000)
        # Click preview để mở image viewer dialog
        preview.click(timeout=10000)
        # Viewer mount bất đồng bộ sau Skip. Đợi nút thực sự xuất hiện,
        # không quyết định selector bằng snapshot sau một sleep cố định.
        dialog = self.page.get_by_role("dialog").filter(visible=True)
        download_btn = dialog.get_by_role("button", name=re.compile(
            self.download_pattern, re.I)).filter(visible=True).last
        download_btn.wait_for(state="visible", timeout=10000)

        return self._single_image_download(download_btn)

    def _single_image_download(self, button):
        if button.get_attribute("aria-haspopup") == "menu":
            button.click(timeout=5000)
            item = self.page.get_by_role("menuitem", name=re.compile(
                r"^(Download image|Tải ảnh xuống|Tải xuống ảnh)$", re.I)).filter(visible=True).first
            item.wait_for(state="visible", timeout=5000)
            self.log("Chọn Download image: chỉ tải một ảnh, không tải cả bộ biến thể")
            return item
        return button

    def _dismiss_image_comparison(self):
        from playwright.sync_api import expect, TimeoutError as PlaywrightTimeout
        choice = self.page.get_by_role("button", name=re.compile(
            r"^Image [12] is better$", re.I)).filter(visible=True)
        if not choice.count():
            return
        skip = self.page.get_by_role("button", name=re.compile(
            r"^(Skip|Bỏ qua)$", re.I)).filter(visible=True).last
        if not skip.count():
            raise RuntimeError("[IMAGE_COMPARISON] ChatGPT yêu cầu chọn giữa hai ảnh nhưng không có nút Skip.")
        self.log("ChatGPT hiển thị so sánh 2 ảnh; bấm Skip và chỉ lưu một ảnh cho cảnh này")
        try:
            skip.click(timeout=5000)
            expect(choice).to_have_count(0, timeout=10000)
        except (AssertionError, PlaywrightTimeout) as exc:
            raise RuntimeError("[IMAGE_COMPARISON] Không đóng được màn hình chọn ảnh của ChatGPT.") from exc

    def _reply_text(self, response):
        # Một container turn có thể bao gồm cả câu hỏi và câu trả lời.
        # Chỉ lấy assistant để JSON mẫu trong instruction không lẫn kết quả.
        bodies = response.locator(
            '[data-message-author-role="assistant"], '
            '[data-content-search-unit-key$=":assistant"]'
        )
        body = bodies.last if bodies.count() else response
        for code in body.locator('pre').all():
            text = code.text_content(timeout=5000) or ""
            if images._parse_prompts(text, 1) is not None:
                return text
        if bodies.count():
            return body.inner_text(timeout=5000)
        # Image/tool replies có thể không có assistant marker; loại câu hỏi
        # khỏi container trước khi kiểm tra thông báo lỗi từ dịch vụ.
        return response.evaluate('''e => {
            const copy = e.cloneNode(true);
            copy.querySelectorAll('[data-message-author-role="user"], '
                + '[data-content-search-unit-key$=":user"]').forEach(n => n.remove());
            return copy.textContent || '';
        }''')
