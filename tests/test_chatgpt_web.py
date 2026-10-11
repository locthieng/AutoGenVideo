import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image
from fastapi.testclient import TestClient

from app import chatgpt_web, config, gemini_web, pipeline, server


class ChatGPTPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=config.OUTPUT_DIR)
        self.root = Path(self.tmp.name)
        self.output = patch.object(config, "OUTPUT_DIR", self.root)
        self.output.start()
        self.pdir = self.root / "chat-test"
        (self.pdir / "images").mkdir(parents=True)
        (self.pdir / "audio").mkdir()
        pipeline.save_project(self.pdir, {
            "title": "test", "options": {"mode": "chatgpt"},
            "chatgpt_web_prompts_ready": True,
            "chatgpt_web_chat": "https://chatgpt.com/c/saved-chat",
            "scenes": [{"text": "Scene one", "prompt": "one"},
                       {"text": "Scene two", "prompt": "two"}],
        })

    def tearDown(self):
        self.output.stop()
        self.tmp.cleanup()

    def test_resume_uses_chatgpt_and_skips_saved_images(self):
        Image.new("RGB", (20, 30)).save(self.pdir / "images" / "00.jpg")
        session = MagicMock()

        def generate(prompt, path, record):
            Image.new("RGB", (20, 30)).save(path)
            record("https://chatgpt.com/c/saved-chat")

        session.generate.side_effect = generate
        with patch.object(chatgpt_web, "ChatGPTWeb") as adapter, \
                patch.object(gemini_web, "GeminiWeb") as gemini:
            adapter.return_value.__enter__.return_value = session
            pipeline.step_generate_web_images(pipeline.Job("test", "chatgpt"), self.pdir)
        gemini.assert_not_called()
        session.begin.assert_called_once_with("https://chatgpt.com/c/saved-chat")
        session.generate.assert_called_once()
        self.assertEqual(session.generate.call_args.args[0], "two")
        self.assertNotIn("gemini_web_chat", pipeline.load_project("chat-test"))

    def test_invalid_prompts_stop_images_and_can_retry(self):
        data = pipeline.load_project("chat-test")
        data.pop("chatgpt_web_prompts_ready")
        pipeline.save_project(self.pdir, data)
        session = MagicMock()
        session.ask.return_value = '{"prompts":["only one"]}'
        with patch.object(chatgpt_web, "ChatGPTWeb") as adapter:
            adapter.return_value.__enter__.return_value = session
            with self.assertRaisesRegex(RuntimeError, "ChatGPT"):
                pipeline.step_generate_web_images(pipeline.Job("test", "chatgpt"), self.pdir)
        session.generate.assert_not_called()
        self.assertNotIn("chatgpt_web_prompts_ready", pipeline.load_project("chat-test"))

    def test_resume_recovers_completed_prompts_without_sending_again(self):
        data = pipeline.load_project("chat-test")
        data.pop("chatgpt_web_prompts_ready")
        pipeline.save_project(self.pdir, data)
        session = MagicMock()
        session.latest_reply.return_value = '{"prompts":["recovered one","recovered two"]}'
        with patch.object(chatgpt_web, "ChatGPTWeb") as adapter:
            adapter.return_value.__enter__.return_value = session
            pipeline.step_generate_web_images(pipeline.Job("test", "chatgpt"), self.pdir)
        session.ask.assert_not_called()
        self.assertEqual([c.args[0] for c in session.generate.call_args_list],
                         ["recovered one", "recovered two"])
        self.assertTrue(pipeline.load_project("chat-test")["chatgpt_web_prompts_ready"])

    def test_resume_cannot_switch_provider(self):
        with patch.object(pipeline, "_start") as start:
            pipeline.resume_web("chat-test", {"mode": "web"})
        self.assertEqual(start.call_args.args[2].mode, "chatgpt")

    def test_failure_preserves_project_and_releases_chatgpt_lock(self):
        job = pipeline.Job("test", "chatgpt")
        with patch.object(pipeline, "step_generate_web_images", side_effect=RuntimeError("quota")), \
                patch.object(pipeline, "step_render") as render:
            pipeline.run_web(job, pipeline.JobOptions(mode="chatgpt"), "chat-test")
        self.assertEqual(job.status, "error")
        self.assertEqual(job.project, "chat-test")
        render.assert_not_called()
        self.assertFalse(chatgpt_web.JOB_LOCK.locked())

    def test_busy_chatgpt_does_not_consume_gemini_lock(self):
        chatgpt_web.JOB_LOCK.acquire()
        try:
            job = pipeline.Job("test", "chatgpt")
            with patch.object(pipeline, "step_generate_web_images") as generate:
                pipeline.run_web(job, pipeline.JobOptions(mode="chatgpt"), "chat-test")
            self.assertEqual(job.status, "error")
            generate.assert_not_called()
            self.assertFalse(gemini_web.JOB_LOCK.locked())
        finally:
            chatgpt_web.JOB_LOCK.release()

    def test_missing_images_rejected_before_tts(self):
        with patch.object(pipeline.tts, "synthesize") as tts:
            with self.assertRaisesRegex(RuntimeError, "ChatGPT web"):
                pipeline.step_render(pipeline.Job("test", "render"), self.pdir,
                                     pipeline.JobOptions(mode="chatgpt"), 0)
        tts.assert_not_called()

    def test_valid_chat_urls(self):
        self.assertTrue(chatgpt_web.valid_chat_url("https://chatgpt.com/c/abc-123"))
        for url in ("http://chatgpt.com/c/abc", "https://chatgpt.com.evil/c/abc",
                    "https://user@chatgpt.com/c/abc", "https://chatgpt.com/",
                    "https://chatgpt.com/c/", "https://chatgpt.com/c/a/extra"):
            self.assertFalse(chatgpt_web.valid_chat_url(url))

    def test_missing_composer_reports_progress_and_fails_after_30_seconds(self):
        logs = []
        session = chatgpt_web.ChatGPTWeb(logs.append)
        session.page = MagicMock()
        session._signed_out = MagicMock(return_value=False)
        session._composer = MagicMock()
        session._composer.return_value.is_visible.return_value = False
        elapsed = [0.0]
        session.page.wait_for_timeout.side_effect = lambda ms: elapsed.__setitem__(0, elapsed[0] + ms / 1000)
        with patch.object(gemini_web.time, "monotonic", side_effect=lambda: elapsed[0]):
            with self.assertRaisesRegex(RuntimeError, "COMPOSER_SELECTOR"):
                session.begin()
        self.assertEqual(elapsed[0], 30)
        self.assertGreaterEqual(len(logs), 4)

    def test_login_wait_logs_until_timeout(self):
        logs = []
        session = chatgpt_web.ChatGPTWeb(logs.append)
        session.page = MagicMock()
        session._signed_out = MagicMock(return_value=True)
        elapsed = [0.0]
        session.page.wait_for_timeout.side_effect = lambda ms: elapsed.__setitem__(0, elapsed[0] + ms / 1000)
        with patch.object(config, "CHATGPT_WEB_LOGIN_TIMEOUT", 3), \
                patch.object(gemini_web.time, "monotonic", side_effect=lambda: elapsed[0]):
            with self.assertRaisesRegex(RuntimeError, "chưa sẵn sàng"):
                session.begin()
        self.assertEqual(elapsed[0], 3)
        self.assertTrue(any("xác minh" in line for line in logs))

    def test_browser_profile_and_port_are_separate(self):
        with patch.object(gemini_web, "open_browser", return_value="endpoint") as opener:
            self.assertEqual(chatgpt_web.open_browser(), "endpoint")
        kwargs = opener.call_args.kwargs
        self.assertEqual(kwargs["profile"], config.CHATGPT_WEB_PROFILE)
        self.assertEqual(kwargs["port"], config.CHATGPT_WEB_PORT)
        self.assertNotEqual(config.CHATGPT_WEB_PROFILE, config.GEMINI_WEB_PROFILE)

    def test_download_records_chatgpt_chat_and_validates_image(self):
        session = chatgpt_web.ChatGPTWeb(lambda _: None)
        session.page = MagicMock()
        session.page.url = "https://chatgpt.com/c/saved-chat"
        responses = MagicMock()
        responses.count.side_effect = [1, 2]
        response = responses.last
        response.locator.return_value.filter.return_value.evaluate_all.return_value = True
        session._responses = MagicMock(return_value=responses)
        session._stop_button = MagicMock()
        session._stop_button.return_value.is_visible.return_value = False
        session._composer = MagicMock()
        session._download_button = MagicMock()
        session._send_prompt = MagicMock()
        session._reply_text = MagicMock(return_value="")
        download = session.page.expect_download.return_value.__enter__.return_value.value
        download.save_as.side_effect = lambda path: Image.new("RGB", (400, 600)).save(path, format="PNG")
        record = MagicMock()
        target = self.pdir / "images" / "00.jpg"
        session.generate("one", target, record)
        responses.nth.assert_not_called()
        record.assert_called_once_with("https://chatgpt.com/c/saved-chat")
        with Image.open(target) as img:
            self.assertEqual(img.size, (config.WIDTH, config.HEIGHT))
        self.assertFalse(target.with_suffix(".download").exists())

    def test_detached_response_is_retried_during_image_generation(self):
        from playwright.sync_api import Error
        session = chatgpt_web.ChatGPTWeb(lambda _: None)
        session.page = MagicMock()
        session.page.url = "https://chatgpt.com/c/saved-chat"
        responses = MagicMock()
        responses.count.side_effect = [1, 2, 2]
        responses.last.scroll_into_view_if_needed.side_effect = [
            Error("Element is not attached to the DOM"), None]
        session._responses = MagicMock(return_value=responses)
        session._stop_button = MagicMock()
        session._stop_button.return_value.is_visible.return_value = False
        session._fill_prompt = MagicMock()
        session._send_prompt = MagicMock()
        session._reply_text = MagicMock(return_value="")
        session._download_button = MagicMock()
        download = session.page.expect_download.return_value.__enter__.return_value.value
        download.save_as.side_effect = lambda path: Image.new("RGB", (400, 600)).save(path, format="PNG")
        target = self.pdir / "images" / "00.jpg"
        session.generate("one", target, MagicMock())
        self.assertTrue(target.exists())
        session._send_prompt.assert_called_once()
        self.assertEqual(responses.last.scroll_into_view_if_needed.call_count, 2)

    def test_failure_messages_use_observed_evidence(self):
        session = chatgpt_web.ChatGPTWeb(lambda _: None)
        for text, code in (
            ("You've reached your image generation limit. Try again tomorrow.", "QUOTA"),
            ("I can't generate this image because it violates our policies.", "REFUSED"),
            ("Please verify you are human", "VERIFY"),
            ("Something went wrong while creating your image", "SERVICE"),
        ):
            with self.subTest(code=code):
                message = session._failure_message(text)
                self.assertIn(f"[{code}]", message)
                self.assertIn(text, message)
        self.assertEqual(session._failure_message("Creating your image. Please wait."), "")

    def test_image_timeout_logs_observed_state_without_guessing_quota(self):
        from playwright.sync_api import TimeoutError
        logs = []
        session = chatgpt_web.ChatGPTWeb(logs.append)
        session.page = MagicMock()
        session.page.url = "https://chatgpt.com/c/saved-chat"
        session._responses = MagicMock()
        session._responses.return_value.count.return_value = 0
        session._stop_button = MagicMock()
        session._stop_button.return_value.is_visible.return_value = False
        session._fill_prompt = MagicMock()
        session._send_prompt = MagicMock()
        elapsed = [0.0]
        session.page.wait_for_timeout.side_effect = lambda ms: elapsed.__setitem__(0, elapsed[0] + ms / 1000)
        with patch.object(config, "CHATGPT_WEB_TIMEOUT", 3), \
                patch.object(gemini_web.time, "monotonic", side_effect=lambda: elapsed[0]):
            with self.assertRaisesRegex(RuntimeError, r"\[TIMEOUT\].*3s") as error:
                session.generate("scene", self.pdir / "images" / "00.jpg", MagicMock())
        self.assertNotIn("quota", str(error.exception))
        self.assertTrue(any("lượt phản hồi 0" in line for line in logs))

    def test_virtualized_dom_detects_new_turn_via_marker(self):
        session = chatgpt_web.ChatGPTWeb(lambda _: None)
        session.page = MagicMock()
        session.page.url = "https://chatgpt.com/c/saved-chat"
        responses = MagicMock()
        # DOM stays at 5 turns due to unmounting earlier items
        responses.count.return_value = 5
        session._responses = MagicMock(return_value=responses)
        session._last_turn_marker = MagicMock(side_effect=["old-turn-id", "new-turn-id"])
        session._stop_button = MagicMock()
        session._stop_button.return_value.is_visible.return_value = False
        session._fill_prompt = MagicMock()
        session._send_prompt = MagicMock()
        session._reply_text = MagicMock(return_value="")
        session._image_ready = MagicMock(return_value=True)
        session._download_button = MagicMock()
        download = session.page.expect_download.return_value.__enter__.return_value.value
        download.save_as.side_effect = lambda path: Image.new("RGB", (400, 600)).save(path, format="PNG")
        target = self.pdir / "images" / "04.jpg"
        session.generate("scene 5", target, MagicMock())
        self.assertTrue(target.exists())
        self.assertEqual(session._last_turn_marker.call_count, 2)

    def test_close_viewer_called_before_and_after_generation(self):
        session = chatgpt_web.ChatGPTWeb(lambda _: None)
        session.page = MagicMock()
        session.page.url = "https://chatgpt.com/c/saved-chat"
        responses = MagicMock()
        responses.count.side_effect = [1, 2]
        session._responses = MagicMock(return_value=responses)
        session._stop_button = MagicMock()
        session._stop_button.return_value.is_visible.return_value = False
        session._fill_prompt = MagicMock()
        session._send_prompt = MagicMock()
        session._reply_text = MagicMock(return_value="")
        session._image_ready = MagicMock(return_value=True)
        session._close_viewer = MagicMock()
        session._download_button = MagicMock()
        download = session.page.expect_download.return_value.__enter__.return_value.value
        download.save_as.side_effect = lambda path: Image.new("RGB", (400, 600)).save(path, format="PNG")
        target = self.pdir / "images" / "00.jpg"
        session.generate("scene", target, MagicMock())
        # Called at beginning and in finally after download
        self.assertGreaterEqual(session._close_viewer.call_count, 2)


class WebAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_only_web_modes_can_create_jobs(self):
        for mode in ("manual", "auto"):
            with patch.object(pipeline, "start_web") as start:
                response = self.client.post("/api/jobs", json={"script": "Hello", "mode": mode})
            self.assertEqual(response.status_code, 400)
            start.assert_not_called()
        for mode in ("web", "chatgpt"):
            with patch.object(pipeline, "start_web", return_value=pipeline.Job("test", mode)) as start:
                response = self.client.post("/api/jobs", json={"script": "Hello", "mode": mode})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(start.call_args.args[0].mode, mode)

    def test_remote_origin_cannot_open_chatgpt(self):
        with patch.object(chatgpt_web, "open_browser") as opener:
            response = self.client.post("/api/chatgpt-web/open", headers={"Origin": "https://example.com"})
        self.assertEqual(response.status_code, 403)
        opener.assert_not_called()

    def test_local_chatgpt_login_endpoint(self):
        with patch.object(chatgpt_web, "open_browser") as opener:
            response = self.client.post("/api/chatgpt-web/open")
        self.assertEqual(response.status_code, 200)
        opener.assert_called_once()


if __name__ == "__main__":
    unittest.main()
