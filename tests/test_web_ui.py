"""Browser smoke test cho UI local; không gọi dịch vụ AI hay dùng tài khoản."""
import json
import unittest
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from app import config, gemini_web, chatgpt_web


class WebUISmokeTests(unittest.TestCase):
    def test_modes_login_resume_and_old_settings(self):
        requests = []
        errors = []
        poll_count = [0]
        project = {
            "id": "chat-test", "title": "ChatGPT test", "mode": "chatgpt",
            "options": {"voice": config.VOICES[0][0], "rate": 20, "music": "",
                        "music_volume": 0.12, "uppercase": True, "words_per_caption": 3},
            "scenes": [{"text": "One", "prompt": "one", "image": "", "index": 0}],
            "video": "", "thumbnail": "", "thumb_seconds": 0.5,
        }

        def route_request(route):
            request = route.request
            path = urlparse(request.url).path
            if path == "/":
                route.fulfill(content_type="text/html", body=(config.STATIC_DIR / "index.html").read_text(encoding="utf-8"))
                return
            requests.append((path, request.post_data_json if request.post_data else None))
            if path == "/api/options":
                data = {"voices": [{"id": v, "name": n} for v, n in config.VOICES],
                        "styles": [{"id": k, "prompt": v} for k, v in config.STYLES.items()],
                        "music": [], "gemini_web": {"installed": True, "automation_installed": True},
                        "chatgpt_web": {"installed": True, "automation_installed": True}}
            elif path == "/api/projects":
                data = [{"id": "chat-test", "title": "ChatGPT test", "has_video": False,
                         "images": 0, "scenes": 1}]
            elif path == "/api/projects/chat-test":
                data = project
            elif path.startswith("/api/jobs/"):
                poll_count[0] += 1
                if poll_count[0] == 1:
                    route.abort()
                    return
                data = {"status": "done", "step": "Hoàn thành", "progress": 1,
                        "logs": [], "project": "", "video": ""}
            elif path == "/api/jobs" or path.endswith("/resume-web"):
                data = {"id": "fixture-job"}
            elif path == "/api/split":
                data = {"scenes": ["Hello world"]}
            else:
                data = {"ok": True}
            route.fulfill(content_type="application/json", body=json.dumps(data))

        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(gemini_web._browser_executable()), headless=True)
            try:
                page = browser.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/*", route_request)
                page.add_init_script('localStorage.setItem("shorts-settings", JSON.stringify({mode:"manual"}));')
                page.goto("http://localhost:8000/")
                page.locator(".tab.active[data-mode='web']").wait_for()
                self.assertEqual(page.locator(".tab").count(), 2)
                page.locator(".tab[data-mode='chatgpt']").click()
                page.locator("#openGemini").click()
                page.wait_for_function("!document.getElementById('openGemini').disabled")
                self.assertIn(("/api/chatgpt-web/open", {}), requests)
                page.locator("#script").fill("Hello world")
                page.locator("#go").click()
                page.locator("#result .err").wait_for()
                self.assertIn("Không cập nhật được tiến trình", page.locator("#result").inner_text())
                page.wait_for_function("!document.getElementById('go').disabled")
                created = [data for path, data in requests if path == "/api/jobs"]
                self.assertEqual(created[0]["mode"], "chatgpt")
                page.locator("#history a").first.click()
                page.locator("#resumeWebBtn").wait_for(state="visible")
                self.assertIn("ChatGPT", page.locator("#resumeWebBtn").inner_text())
                self.assertEqual(page.locator("#rate").input_value(), "20")
                page.locator("#resumeWebBtn").click()
                page.wait_for_function("!document.getElementById('go').disabled")
                resumed = [data for path, data in requests if path.endswith("/resume-web")]
                self.assertEqual(resumed[0]["rate"], 20)
                self.assertEqual(errors, [])

                # Reproduce the new ChatGPT composer without id prompt-textarea.
                page.set_content('<textarea placeholder="Ask ChatGPT"></textarea>'
                                 '<textarea id="prompt-textarea" style="display:none"></textarea>')
                session = chatgpt_web.ChatGPTWeb(lambda _: None)
                session.page = page
                session._composer().fill("Test prompt")
                self.assertEqual(page.locator('textarea[placeholder="Ask ChatGPT"]').input_value(), "Test prompt")
                page.set_content('<div role="textbox" contenteditable="true" aria-label="Nhập tin nhắn"></div>')
                session._composer().fill("Fallback prompt")
                self.assertEqual(page.locator('[contenteditable="true"]').inner_text(), "Fallback prompt")
                page.set_content('<textarea></textarea><textarea></textarea>')
                with self.assertRaisesRegex(RuntimeError, "COMPOSER_SELECTOR"):
                    session._composer()

                # Response layouts without the old conversation-turn test id.
                for markup in (
                    '<div data-message-author-role="assistant">answer</div>',
                    '<div data-content-search-unit-key="fallback-turn-0:0:assistant">answer</div>',
                    '<article data-turn="assistant">answer</article>',
                    '<article><div data-message-author-role="assistant">answer</div>'
                    '<button>Download</button></article>',
                    '<article data-testid="conversation-turn-1"><div '
                    'data-message-author-role="assistant">answer</div></article>',
                ):
                    page.set_content('<article data-turn="user">question</article>' + markup)
                    self.assertEqual(session._responses().count(), 1)
                    self.assertIn("answer", session._responses().first.inner_text())

                page.set_content('''<div data-talvt-turn-state="complete">
                    <div data-content-search-unit-key="turn-0:assistant">answer</div></div>
                    <div data-talvt-turn-state="complete"><div data-testid="generated-image-gallery">
                    <img alt="Generated image"></div></div>''')
                self.assertEqual(session._responses().count(), 2)
                self.assertEqual(session._responses().last.locator('img').count(), 1)
                page.set_content('''<div data-turn-key="old-turn"><div data-talvt-turn-state="complete">
                    <div data-content-search-unit-key="old:assistant">same prefix</div></div></div>
                    <div data-turn-key="new-turn"><div data-talvt-turn-state="complete">
                    <div data-content-search-unit-key="new:assistant">same prefix</div></div></div>''')
                self.assertEqual(session._last_turn_marker(), 'new-turn')
                page.locator('[data-turn-key="old-turn"]').evaluate('e => e.remove()')
                self.assertEqual(session._last_turn_marker(), 'new-turn')
                page.locator('[data-turn-key="new-turn"]').evaluate('e => e.setAttribute("data-turn-key", "next-turn")')
                self.assertEqual(session._last_turn_marker(), 'next-turn')

                # A turn can include the user's invalid JSON example as well
                # as the assistant's valid JSON code block.
                page.set_content('''<div data-talvt-turn-state="complete">
                    <div data-content-search-unit-key="turn-0:user">
                    Return JSON: {"prompts":["example", ...]}</div>
                    <div data-content-search-unit-key="turn-0:assistant">
                    JSON<button>Copy</button><pre><code>{"prompts":["actual answer"]}</code></pre>
                    </div></div>''')
                self.assertEqual(session.latest_reply(), '{"prompts":["actual answer"]}')
                page.set_content('''<div data-talvt-turn-state="complete">
                    <div data-content-search-unit-key="turn-0:user">Cannot create an image?</div>
                    <div role="alert">You've reached your image generation limit.</div></div>''')
                response = page.locator('[data-talvt-turn-state]')
                self.assertNotIn('Cannot create', session._reply_text(response))
                self.assertIn('[QUOTA]', session._page_failure())
                with self.assertRaisesRegex(RuntimeError, r'\[QUOTA\]'):
                    session._fill_prompt = lambda _: None
                    session._send_prompt = lambda: None
                    session.generate('unused', config.OUTPUT_DIR / 'never-written.jpg', lambda _: None)
                del session._fill_prompt
                del session._send_prompt

                page.set_content('''<textarea placeholder="Ask ChatGPT"></textarea>
                    <button aria-label="Send" disabled>Send</button>
                    <div data-message-author-role="assistant">old answer</div>
                    <script>
                    window.sendCount = 0;
                    const editor = document.querySelector('textarea');
                    const send = document.querySelector('button');
                    editor.addEventListener('input', () => {
                      setTimeout(() => send.disabled = false, 500);
                    });
                    send.addEventListener('click', () => {
                      window.sendCount++;
                      const user = document.createElement('div');
                      user.dataset.messageAuthorRole = 'user';
                      user.textContent = editor.value;
                      editor.value = '';
                      document.body.appendChild(user);
                      const reply = document.createElement('div');
                      reply.dataset.messageAuthorRole = 'assistant';
                      reply.textContent = 'Thinking...';
                      document.body.appendChild(reply);
                      setTimeout(() => {
                        const final = document.createElement('div');
                        final.dataset.contentSearchUnitKey = 'fallback-turn-1:1:assistant';
                        final.textContent = '{"prompts":["new answer"]}';
                        document.body.appendChild(final);
                      }, 3500);
                    });
                    </script>''')
                self.assertEqual(session.ask("Write prompts", lambda _: None),
                                 '{"prompts":["new answer"]}')
                self.assertEqual(page.evaluate('window.sendCount'), 1)
                self.assertEqual(page.locator('textarea').input_value(), '')
                # The contenteditable composer also clears only after Send.
                page.set_content('''<div role="textbox" aria-label="Ask ChatGPT"
                    contenteditable="true"></div><button aria-label="Send">Send</button>
                    <script>document.querySelector('button').onclick = () => {
                    document.querySelector('div').textContent = '';
                    }</script>''')
                session._fill_prompt('Image prompt')
                session._send_prompt()
                self.assertEqual(page.locator('[role="textbox"]').inner_text(), '')
                page.set_content('''<div data-content-search-unit-key="fallback-turn-1:1:assistant">ChatGPT said:</div>
                    <script>setTimeout(() => {
                    document.querySelector('div').textContent = '{"prompts":["hydrated answer"]}';
                    }, 1500)</script>''')
                self.assertEqual(session.latest_reply(), '{"prompts":["hydrated answer"]}')
                page.set_content('''<button id="skip">Skip</button>
                    <button class="choice">Image 1 is better</button>
                    <button class="choice">Image 2 is better</button>
                    <script>window.skipped = false;
                    document.getElementById('skip').onclick = () => {
                      window.skipped = true;
                      document.querySelectorAll('.choice').forEach(e => e.remove());
                    };</script>''')
                session._dismiss_image_comparison()
                self.assertTrue(page.evaluate('window.skipped'))
                self.assertEqual(page.locator('.choice').count(), 0)
                page.set_content('''<button aria-label="Download" aria-haspopup="menu"
                    onclick="document.getElementById('menu').hidden=false">Download</button>
                    <div id="menu" role="menu" hidden>
                    <button role="menuitem">Download image</button>
                    <button role="menuitem">Download 2 images in this series</button></div>''')
                item = session._single_image_download(page.get_by_role('button', name='Download', exact=True))
                self.assertEqual(item.inner_text(), 'Download image')
                page.set_content('''<div data-turn="assistant">
                    <button data-testid="generated-image-preview">Open image</button></div>
                    <script>document.querySelector('button').onclick = () => {
                      setTimeout(() => {
                        const dialog = document.createElement('div');
                        dialog.setAttribute('role', 'dialog');
                        dialog.innerHTML = '<button aria-label="Download">Download</button>';
                        document.body.appendChild(dialog);
                      }, 1200);
                    };</script>''')
                button = session._download_button(page.locator('[data-turn="assistant"]'))
                self.assertTrue(button.is_visible())
                self.assertEqual(button.get_attribute('aria-label'), 'Download')
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
