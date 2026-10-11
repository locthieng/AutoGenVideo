import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from app import config, gemini_web, images, pipeline


class WebPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="test-web-", dir=config.OUTPUT_DIR)
        self.root = Path(self.tmp.name)
        self.output = patch.object(config, "OUTPUT_DIR", self.root)
        self.output.start()
        self.pdir = self.root / "test-project"
        (self.pdir / "images").mkdir(parents=True)
        (self.pdir / "audio").mkdir()
        pipeline.save_project(self.pdir, {
            "title": "test", "options": {"mode": "web"}, "gemini_web_prompts_ready": True,
            "scenes": [{"text": "Scene one", "prompt": "one"},
                       {"text": "Scene two", "prompt": "two"}],
        })

    def tearDown(self):
        self.output.stop()
        self.tmp.cleanup()

    def test_resume_skips_saved_images_and_keeps_chat(self):
        Image.new("RGB", (20, 30)).save(self.pdir / "images" / "00.jpg")
        session = MagicMock()

        def generate(prompt, path, record):
            self.assertEqual(prompt, "two")
            Image.new("RGB", (20, 30)).save(path)
            record("https://gemini.google.com/app/abc")

        session.generate.side_effect = generate
        with patch.object(gemini_web, "GeminiWeb") as adapter:
            adapter.return_value.__enter__.return_value = session
            pipeline.step_generate_web_images(pipeline.Job("test", "web"), self.pdir)
        self.assertEqual(session.generate.call_count, 1)
        self.assertEqual(pipeline.load_project("test-project")["gemini_web_chat"],
                         "https://gemini.google.com/app/abc")

    def test_image_failure_stops_before_render_and_preserves_project(self):
        job = pipeline.Job("test", "web")
        with patch.object(pipeline, "step_generate_web_images", side_effect=RuntimeError("quota")), \
                patch.object(pipeline, "step_render") as renderer:
            pipeline.run_web(job, pipeline.JobOptions(mode="web"), "test-project")
        self.assertEqual(job.status, "error")
        self.assertEqual(job.project, "test-project")
        self.assertTrue((self.pdir / "project.json").exists())
        renderer.assert_not_called()
        self.assertFalse(gemini_web.JOB_LOCK.locked())

    def test_render_rejects_missing_web_images(self):
        Image.new("RGB", (20, 30)).save(self.pdir / "images" / "00.jpg")
        with patch.object(pipeline.tts, "synthesize") as tts:
            with self.assertRaisesRegex(RuntimeError, "chưa có đủ ảnh"):
                pipeline.step_render(pipeline.Job("test", "render"), self.pdir,
                                     pipeline.JobOptions(mode="web"), 0)
        tts.assert_not_called()

    def test_browser_is_not_opened_when_all_images_exist(self):
        for i in range(2):
            Image.new("RGB", (20, 30)).save(self.pdir / "images" / f"{i:02d}.jpg")
        with patch.object(gemini_web, "GeminiWeb") as adapter:
            pipeline.step_generate_web_images(pipeline.Job("test", "web"), self.pdir)
        adapter.assert_not_called()

    def test_busy_browser_rejects_second_job(self):
        gemini_web.JOB_LOCK.acquire()
        try:
            job = pipeline.Job("test", "web")
            with patch.object(pipeline, "step_generate_web_images") as generate:
                pipeline.run_web(job, pipeline.JobOptions(mode="web"), "test-project")
            self.assertEqual(job.status, "error")
            generate.assert_not_called()
            self.assertTrue(gemini_web.JOB_LOCK.locked())
        finally:
            gemini_web.JOB_LOCK.release()

    def test_resume_cannot_change_mode_or_use_manual_project(self):
        with patch.object(pipeline, "_start", return_value="job") as start:
            pipeline.resume_web("test-project", {"mode": "manual", "rate": 20})
        self.assertEqual(start.call_args.args[2].mode, "web")
        self.assertEqual(start.call_args.args[2].rate, 20)
        data = pipeline.load_project("test-project")
        data["options"]["mode"] = "manual"
        pipeline.save_project(self.pdir, data)
        with self.assertRaises(ValueError):
            pipeline.resume_web("test-project", {})

    def test_chat_urls_restricted_to_gemini(self):
        self.assertTrue(gemini_web.valid_chat_url("https://gemini.google.com/app/abc"))
        for url in ("http://gemini.google.com/app/abc", "https://gemini.google.com.evil/app/abc",
                    "https://user@gemini.google.com/app/abc", "https://gemini.google.com/app"):
            self.assertFalse(gemini_web.valid_chat_url(url))

    def _download_session(self, valid=True):
        session = gemini_web.GeminiWeb(lambda _: None)
        session.page = MagicMock()
        session.page.url = "https://gemini.google.com/app/abc"
        responses = MagicMock()
        responses.count.side_effect = [1, 2]
        response = responses.last
        response.locator.return_value.filter.return_value.evaluate_all.return_value = True
        stop = MagicMock()
        stop.is_visible.return_value = False
        session._stop_button = MagicMock(return_value=stop)
        session._responses = MagicMock(return_value=responses)
        session._composer = MagicMock()
        session._reply_text = MagicMock(return_value="")

        def save_as(path):
            if valid:
                Image.new("RGB", (400, 600)).save(path, format="PNG")
            else:
                Path(path).write_bytes(b"not an image")

        session.page.expect_download.return_value.__enter__.return_value.value.save_as.side_effect = save_as
        return session, responses

    def test_download_saves_valid_image_and_ignores_previous_response(self):
        session, responses = self._download_session()
        target = self.pdir / "images" / "00.jpg"
        record = MagicMock()
        session.generate("one", target, record)
        responses.nth.assert_not_called()
        with Image.open(target) as image:
            self.assertEqual(image.size, (config.WIDTH, config.HEIGHT))
        record.assert_called_once_with("https://gemini.google.com/app/abc")
        self.assertFalse(target.with_suffix(".download").exists())

    def test_invalid_download_is_not_saved_as_completed_scene(self):
        session, _ = self._download_session(valid=False)
        target = self.pdir / "images" / "00.jpg"
        with self.assertRaises(ValueError):
            session.generate("one", target, MagicMock())
        self.assertFalse(target.exists())
        self.assertFalse(target.with_suffix(".tmp.jpg").exists())
        self.assertFalse(target.with_suffix(".download").exists())

    def test_web_prompts_require_exact_count_and_nonempty_strings(self):
        for raw in ('{"prompts":["one"]}', '{"prompts":["one", ""]}', '{"prompts":["one", 2]}'):
            self.assertIsNone(images._parse_prompts(raw, 2, strict=True))
        self.assertEqual(images._parse_prompts('{"prompts":["one", "two"]}', 2, strict=True), ["one", "two"])

    def test_web_prompt_generation_updates_saved_prompts(self):
        data = pipeline.load_project("test-project")
        data.pop("gemini_web_prompts_ready")
        pipeline.save_project(self.pdir, data)
        session = MagicMock()
        session.ask.return_value = '{"prompts":["new one", "new two"]}'
        with patch.object(gemini_web, "GeminiWeb") as adapter:
            adapter.return_value.__enter__.return_value = session
            pipeline.step_generate_web_images(pipeline.Job("test", "web"), self.pdir)
        self.assertEqual([call.args[0] for call in session.generate.call_args_list], ["new one", "new two"])
        self.assertTrue(pipeline.load_project("test-project")["gemini_web_prompts_ready"])

    def test_browser_reconnects_after_parent_process_exits(self):
        profile = self.root / "profile"
        profile.mkdir()
        endpoint = f"http://127.0.0.1:{config.GEMINI_WEB_PORT}"
        import json
        (profile / "debug-session.json").write_text(json.dumps({"endpoint": endpoint, "id": "known-session"}))
        with patch.object(config, "GEMINI_WEB_PROFILE", profile), \
                patch.object(gemini_web, "_version", return_value={"webSocketDebuggerUrl": "known-session"}), \
                patch.object(gemini_web.subprocess, "Popen") as launch:
            self.assertEqual(gemini_web.open_browser(), endpoint)
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
