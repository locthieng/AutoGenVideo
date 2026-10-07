"""Quy trình chính.

Mỗi video là một "project" (thư mục trong output/) gồm project.json + images/ + audio/.

Chế độ tự động (API key):   chuẩn bị cảnh + prompt -> tạo ảnh AI -> dựng video
Chế độ bán tự động (Pro):   chuẩn bị cảnh + prompt (Gemini CLI) -> người dùng tự thêm ảnh -> dựng video
Ở cả hai chế độ đều có thể thay ảnh từng cảnh rồi dựng lại.
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

from . import config, gemini_cli, gemini_web, images, render, subtitles, tts
from .script_splitter import split_scenes

IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")


@dataclass
class JobOptions:
    script: str = ""
    mode: str = "auto"  # auto | manual | web
    voice: str = "en-US-AndrewMultilingualNeural"
    rate: int = 10  # % tốc độ đọc, Shorts nên nhanh hơn một chút
    style: str = "cinematic"
    custom_style: str = ""
    extra_direction: str = ""
    provider: str = config.IMAGE_PROVIDER  # nhà cung cấp ẢNH cho chế độ tự động
    prompt_provider: str = "auto"  # auto | gemini-cli | gemini | openai | none
    music: str = ""  # tên file trong assets/music, "random" hoặc ""
    music_volume: float = 0.12
    uppercase: bool = True
    words_per_caption: int = 3
    title: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "JobOptions":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class Job:
    id: str
    kind: str  # auto | prepare | render
    status: str = "queued"  # queued | running | done | error
    step: str = ""
    progress: float = 0.0
    logs: list[str] = field(default_factory=list)
    project: str = ""
    video: str = ""
    error: str = ""
    created: float = field(default_factory=time.time)

    def log(self, msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{stamp}] {msg}")
        try:
            print(f"[job {self.id}] {msg}", flush=True)
        except UnicodeEncodeError:
            print(f"[job {self.id}] {msg}".encode("ascii", "replace").decode(), flush=True)

    def to_dict(self) -> dict:
        return asdict(self)


JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()


# ------------------------------------------------------------------ project helpers

def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "short"


def project_dir(pid: str) -> Path:
    if not re.fullmatch(r"[\w\-]+", pid or ""):
        raise ValueError("Mã project không hợp lệ")
    d = config.OUTPUT_DIR / pid
    if not (d / "project.json").exists():
        raise FileNotFoundError("Không tìm thấy project")
    return d


def load_project(pid: str) -> dict:
    return json.loads((project_dir(pid) / "project.json").read_text(encoding="utf-8"))


def save_project(pdir: Path, data: dict) -> None:
    (pdir / "project.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def scene_image(pdir: Path, i: int) -> Path | None:
    for ext in IMG_EXTS:
        p = pdir / "images" / f"{i:02d}{ext}"
        if p.exists():
            return p
    return None


def thumb_image(pdir: Path) -> Path | None:
    p = pdir / "thumb.jpg"
    return p if p.exists() else None


def project_info(pid: str) -> dict:
    """Thông tin project cho giao diện: cảnh, prompt, ảnh đã có, video."""
    pdir = project_dir(pid)
    data = load_project(pid)
    scenes = []
    for i, sc in enumerate(data["scenes"]):
        img = scene_image(pdir, i)
        url = f"/output/{pid}/images/{img.name}?v={int(img.stat().st_mtime)}" if img else ""
        scenes.append({**sc, "index": i, "image": url})
    video = data.get("video", "")
    if video and (config.OUTPUT_DIR / video).exists():
        mt = int((config.OUTPUT_DIR / video).stat().st_mtime)
        video_url = f"/output/{video}?v={mt}"
    else:
        video_url = ""
    th = thumb_image(pdir)
    thumb_url = f"/output/{pid}/{th.name}?v={int(th.stat().st_mtime)}" if th else ""
    return {"id": pid, "title": data.get("title", pid), "mode": data["options"].get("mode", "auto"),
            "options": data["options"], "scenes": scenes, "video": video_url, "thumbnail": thumb_url,
            "thumb_seconds": config.THUMB_SECONDS}


def save_scene_image(pid: str, i: int, data: bytes) -> str:
    pdir = project_dir(pid)
    proj = load_project(pid)
    if not 0 <= i < len(proj["scenes"]):
        raise ValueError("Số cảnh không hợp lệ")
    for ext in IMG_EXTS:  # xóa ảnh cũ
        (pdir / "images" / f"{i:02d}{ext}").unlink(missing_ok=True)
    out = images.save_upload(data, pdir / "images" / f"{i:02d}.jpg")
    return f"/output/{pid}/images/{out.name}?v={int(out.stat().st_mtime)}"


def save_thumbnail(pid: str, data: bytes) -> str:
    pdir = project_dir(pid)
    out = images.save_upload(data, pdir / "thumb.jpg")
    return f"/output/{pid}/{out.name}?v={int(out.stat().st_mtime)}"


def delete_thumbnail(pid: str) -> None:
    (project_dir(pid) / "thumb.jpg").unlink(missing_ok=True)


def update_prompt(pid: str, i: int, prompt: str) -> None:
    pdir = project_dir(pid)
    proj = load_project(pid)
    proj["scenes"][i]["prompt"] = prompt
    save_project(pdir, proj)


def list_projects(limit: int = 30) -> list[dict]:
    items = []
    for pj in sorted(config.OUTPUT_DIR.glob("*/project.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            d = json.loads(pj.read_text(encoding="utf-8"))
        except Exception:
            continue
        pdir = pj.parent
        n = len(d.get("scenes", []))
        have = sum(1 for i in range(n) if scene_image(pdir, i))
        items.append({"id": pdir.name, "title": d.get("title", pdir.name), "mode": d["options"].get("mode"),
                      "scenes": n, "images": have, "has_video": bool(d.get("video"))})
        if len(items) >= limit:
            break
    return items


def _pick_music(name: str) -> Path | None:
    files = [p for p in config.MUSIC_DIR.iterdir() if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg")]
    if not name or not files:
        return None
    if name == "random":
        return random.choice(files)
    p = config.MUSIC_DIR / name
    return p if p.exists() else None


def check_image_provider(provider: str) -> None:
    if provider == "gemini" and not config.GEMINI_API_KEY:
        raise RuntimeError("Chưa có GEMINI_API_KEY trong file .env")
    if provider == "openai" and not config.OPENAI_API_KEY:
        raise RuntimeError("Chưa có OPENAI_API_KEY trong file .env")
    if provider not in ("gemini", "openai", "placeholder"):
        raise RuntimeError(f"Nhà cung cấp ảnh không hợp lệ: {provider}")


def resolve_prompt_provider(o: JobOptions) -> str:
    if o.mode == "web":
        return "none"  # Prompt sẽ được viết trong chính conversation Gemini web.
    if o.prompt_provider and o.prompt_provider != "auto":
        return o.prompt_provider
    if o.mode == "auto" and o.provider in ("gemini", "openai"):
        return o.provider
    if gemini_cli.status()["logged_in"]:
        return "gemini-cli"
    if config.GEMINI_API_KEY:
        return "gemini"
    if config.OPENAI_API_KEY:
        return "openai"
    return "none"


# ------------------------------------------------------------------ steps

def step_prepare(job: Job, o: JobOptions) -> Path:
    """Chia cảnh + viết prompt ảnh, tạo thư mục project."""
    job.step = "Chia cảnh & viết mô tả ảnh"
    scenes = split_scenes(o.script)
    if not scenes:
        raise RuntimeError("Kịch bản trống")
    title = o.title.strip() or " ".join(scenes[0].split()[:6])
    pdir = config.OUTPUT_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{_slug(title)}"
    (pdir / "images").mkdir(parents=True, exist_ok=True)
    (pdir / "audio").mkdir(exist_ok=True)
    job.project = pdir.name
    job.log(f"Đã chia kịch bản thành {len(scenes)} cảnh. Thư mục: {pdir.name}")
    (pdir / "script.txt").write_text(o.script, encoding="utf-8")

    style = o.custom_style.strip() or config.STYLES.get(o.style, config.STYLES["cinematic"])
    pp = resolve_prompt_provider(o)
    if pp == "none" and o.mode != "web":
        job.log("⚠️ Chưa có Gemini CLI/API key nên dùng câu thoại làm mô tả ảnh.")
    prompts = images.build_prompts(scenes, style, o.extra_direction, pp, job.log, cwd=pdir)
    if o.mode == "manual":  # thêm yêu cầu khung dọc để dán thẳng vào app Gemini
        prompts = [p if "9:16" in p else f"{p.rstrip('. ')}. Vertical 9:16 aspect ratio, no text."
                   for p in prompts]
    save_project(pdir, {
        "title": title,
        "created": datetime.now().isoformat(timespec="seconds"),
        "options": {k: v for k, v in asdict(o).items() if k != "script"},
        "scenes": [{"text": s, "prompt": p} for s, p in zip(scenes, prompts)],
    })
    job.log("✅ Đã chia cảnh; sẽ viết prompt trên Gemini web" if o.mode == "web" else "✅ Đã viết xong mô tả ảnh")
    return pdir


def step_generate_images(job: Job, pdir: Path, o: JobOptions, p0: float, p1: float) -> None:
    job.step = "Tạo ảnh AI"
    proj = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
    scenes = proj["scenes"]
    n = len(scenes)

    def do_img(i: int) -> None:
        images.generate_image(scenes[i]["prompt"], i, pdir / "images" / f"{i:02d}.jpg", o.provider, job.log)

    workers = 1 if o.provider == "placeholder" else config.IMAGE_CONCURRENCY
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(do_img, i): i for i in range(n)}
        for k, f in enumerate(as_completed(futs)):
            i = futs[f]
            try:
                f.result()
                job.log(f"🖼️ Ảnh {i + 1}/{n} xong")
            except Exception as e:
                job.log(f"❌ {e}")
            job.progress = p0 + (p1 - p0) * (k + 1) / n


def step_generate_web_images(job: Job, pdir: Path) -> None:
    job.step = "Tạo ảnh trên Gemini web"
    proj = load_project(pdir.name)
    scenes = proj["scenes"]
    missing = [i for i in range(len(scenes)) if scene_image(pdir, i) is None]
    job.progress = 0.1 + 0.5 * (len(scenes) - len(missing)) / len(scenes)
    if not missing:
        return

    def record_chat(url: str) -> None:
        # Đọc lại để không ghi đè prompt đã chỉnh trong UI.
        current = load_project(pdir.name)
        current["gemini_web_chat"] = url
        save_project(pdir, current)

    with gemini_web.GeminiWeb(job.log) as session:
        session.begin(proj.get("gemini_web_chat", ""))
        if not proj.get("gemini_web_prompts_ready"):
            job.step = "Gemini web: viết prompt cho các cảnh"
            opts = JobOptions.from_dict(proj["options"])
            style = opts.custom_style.strip() or config.STYLES.get(opts.style, config.STYLES["cinematic"])
            instruction = images.PROMPT_INSTRUCTIONS.format(
                n=len(scenes), style=style,
                extra=f"- Extra direction from the creator: {opts.extra_direction}" if opts.extra_direction.strip() else "",
                scenes="\n".join(f"{i + 1}. {s['text']}" for i, s in enumerate(scenes)))
            raw = session.ask(instruction, record_chat)
            prompts = images._parse_prompts(raw, len(scenes), strict=True)
            if not prompts:
                raise RuntimeError("Gemini trả về prompt sai định dạng. Bấm Tiếp tục để thử lại.")
            current = load_project(pdir.name)
            for i, prompt in enumerate(prompts):
                if i in missing:
                    current["scenes"][i]["prompt"] = prompt
            current["gemini_web_prompts_ready"] = True
            save_project(pdir, current)
            job.log("✅ Gemini web đã viết prompt cho các cảnh")
        for k, i in enumerate(missing):
            job.step = f"Gemini web: tạo ảnh cảnh {i + 1}/{len(scenes)}"
            prompt = load_project(pdir.name)["scenes"][i]["prompt"]
            session.generate(prompt, pdir / "images" / f"{i:02d}.jpg", record_chat)
            job.log(f"🖼️ Đã lưu ảnh cảnh {i + 1}/{len(scenes)}")
            job.progress = 0.1 + 0.5 * (len(scenes) - len(missing) + k + 1) / len(scenes)


def step_render(job: Job, pdir: Path, o: JobOptions, p0: float) -> Path:
    """Giọng đọc + phụ đề + ghép ảnh có sẵn trong project thành video."""
    proj = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
    scenes = [s["text"] for s in proj["scenes"]]
    n = len(scenes)

    # ảnh: cảnh thiếu ảnh -> dùng ảnh cảnh gần nhất
    img_paths: list[Path | None] = [scene_image(pdir, i) for i in range(n)]
    have = [i for i, p in enumerate(img_paths) if p]
    if o.mode == "web" and len(have) != n:
        raise RuntimeError("Gemini web chưa có đủ ảnh. Bấm Tiếp tục Gemini web trước khi dựng video.")
    if not have:
        raise RuntimeError("Chưa có ảnh nào. Hãy thêm ảnh cho các cảnh trước khi dựng video.")
    for i in range(n):
        if img_paths[i] is None:
            donor = min(have, key=lambda j: (abs(j - i), j > i))
            img_paths[i] = img_paths[donor]
            job.log(f"⚠️ Cảnh {i + 1} chưa có ảnh, dùng tạm ảnh cảnh {donor + 1}")

    font_name, font_file = subtitles.ensure_font(job.log)

    # 1) giọng đọc
    job.step = "Tạo giọng đọc"
    rate = f"{o.rate:+d}%"
    scene_words: list[list[tts.Word]] = [[] for _ in scenes]

    def do_tts(i: int) -> None:
        scene_words[i] = tts.synthesize(scenes[i], o.voice, pdir / "audio" / f"{i:02d}.mp3", rate=rate)

    span = 1.0 - p0
    with ThreadPoolExecutor(4) as ex:
        futs = [ex.submit(do_tts, i) for i in range(n)]
        for k, f in enumerate(as_completed(futs)):
            f.result()
            job.progress = p0 + span * 0.25 * (k + 1) / n
    job.log("✅ Xong giọng đọc")

    # cắt bớt khoảng lặng đầu/cuối mỗi cảnh cho nhịp nhanh, rồi nối lại
    wavs: list[Path] = []
    offsets: list[float] = []
    for i, words in enumerate(scene_words):
        mp3 = pdir / "audio" / f"{i:02d}.mp3"
        wav = pdir / "audio" / f"{i:02d}.wav"
        if words:
            start: float | None = max(0.0, words[0].start - 0.05)
            end: float | None = words[-1].end + 0.25
        else:
            start, end = None, None
        render.mp3_to_wav(mp3, wav, start, end)
        offsets.append(start or 0.0)
        wavs.append(wav)
    voice_wav = pdir / "audio" / "voice.wav"
    durations = render.concat_wavs(wavs, voice_wav)
    total = sum(durations)
    job.log(f"Độ dài video: {total:.1f}s" + ("  ⚠️ Shorts nên dưới 60s" if total > 60 else ""))

    # 2) phụ đề theo mốc thời gian toàn video
    job.step = "Tạo phụ đề"
    all_words: list[subtitles.SubWord] = []
    t0 = 0.0
    for i, words in enumerate(scene_words):
        for sw in subtitles.align_words(scenes[i], words):
            s = sw.start - offsets[i] + t0
            e = min(sw.end - offsets[i] + t0, t0 + durations[i])
            all_words.append(subtitles.SubWord(sw.text, max(t0, s), e))
        t0 += durations[i]
    subs_path = subtitles.build_ass(all_words, font_name, pdir / "subs.ass", total,
                                    uppercase=o.uppercase, max_words=o.words_per_caption)

    for sc, d in zip(proj["scenes"], durations):
        sc["duration"] = round(d, 2)

    # 3) dựng video
    job.step = "Dựng video"
    music = _pick_music(o.music)
    if music:
        job.log(f"🎵 Nhạc nền: {music.name}")
    out = pdir / f"{_slug(proj['title'])}.mp4"
    p_render = p0 + span * 0.3

    def prog(p: float) -> None:
        job.progress = p_render + (1.0 - p_render) * p

    thumb = thumb_image(pdir)
    if thumb:
        job.log(f"🖼️ Chèn thumbnail {config.THUMB_SECONDS:g}s ở đầu video")
    render.render_video(pdir, [p for p in img_paths if p], voice_wav, durations, subs_path, font_file, out,
                        music, o.music_volume, prog, thumb=thumb, thumb_seconds=config.THUMB_SECONDS)

    proj["video"] = out.relative_to(config.OUTPUT_DIR).as_posix()
    proj["options"] = {k: v for k, v in asdict(o).items() if k != "script"}
    save_project(pdir, proj)
    return out


# ------------------------------------------------------------------ jobs

def _run(job: Job, fn) -> None:
    job.status = "running"
    try:
        fn()
        job.progress = 1.0
        job.status = "done"
        if job.step != "Hoàn thành":
            job.step = "Hoàn thành"
    except Exception as e:
        job.status = "error"
        job.error = str(e)
        job.log(f"❌ Lỗi: {e}")
        traceback.print_exc()


def _finish_video(job: Job, out: Path) -> None:
    job.video = out.relative_to(config.OUTPUT_DIR).as_posix()
    job.log(f"🎉 Xong! {out.name}")


def run_auto(job: Job, o: JobOptions) -> None:
    def fn():
        check_image_provider(o.provider)
        pdir = step_prepare(job, o)
        job.progress = 0.1
        step_generate_images(job, pdir, o, 0.1, 0.6)
        _finish_video(job, step_render(job, pdir, o, 0.6))
    _run(job, fn)


def run_prepare(job: Job, o: JobOptions) -> None:
    def fn():
        step_prepare(job, o)
        job.log("👉 Giờ hãy tạo ảnh cho từng cảnh trên app Gemini rồi thêm vào đây.")
    _run(job, fn)


def run_web(job: Job, o: JobOptions, pid: str = "") -> None:
    def fn():
        if not gemini_web.JOB_LOCK.acquire(blocking=False):
            raise RuntimeError("Đang có job Gemini web khác chạy. Chờ job đó hoàn tất rồi thử lại.")
        try:
            if pid:
                pdir = project_dir(pid)
                job.project = pid
            else:
                pdir = step_prepare(job, o)
            step_generate_web_images(job, pdir)
            _finish_video(job, step_render(job, pdir, o, 0.6))
        finally:
            gemini_web.JOB_LOCK.release()
    _run(job, fn)


def run_render(job: Job, pid: str, overrides: dict) -> None:
    def fn():
        pdir = project_dir(pid)
        proj = load_project(pid)
        o = JobOptions.from_dict({**proj["options"], **overrides})
        job.project = pid
        _finish_video(job, step_render(job, pdir, o, 0.0))
    _run(job, fn)


def _start(kind: str, target, *args) -> Job:
    job = Job(id=uuid.uuid4().hex[:8], kind=kind)
    with _LOCK:
        JOBS[job.id] = job
    threading.Thread(target=target, args=(job, *args), daemon=True).start()
    return job


def start_auto(o: JobOptions) -> Job:
    return _start("auto", run_auto, o)


def start_prepare(o: JobOptions) -> Job:
    return _start("prepare", run_prepare, o)


def start_web(o: JobOptions) -> Job:
    o.mode = "web"
    return _start("web", run_web, o)


def resume_web(pid: str, overrides: dict) -> Job:
    proj = load_project(pid)
    if proj["options"].get("mode") != "web":
        raise ValueError("Chỉ tiếp tục Gemini web cho dự án tạo bằng chế độ web")
    o = JobOptions.from_dict({**proj["options"], **overrides, "mode": "web"})
    return _start("web", run_web, o, pid)


def start_render(pid: str, overrides: dict) -> Job:
    project_dir(pid)  # kiểm tra tồn tại
    return _start("render", run_render, pid, overrides)
