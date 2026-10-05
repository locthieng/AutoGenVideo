"""Web server: mở http://localhost:8000, dán kịch bản và bấm Tạo video."""
from __future__ import annotations

import webbrowser
from threading import Timer

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles

from . import config, gemini_cli, pipeline
from .pipeline import JOBS, JobOptions
from .script_splitter import split_scenes

app = FastAPI(title="AI Shorts Maker")


class NoCacheStatic(StaticFiles):
    """Ảnh/video có thể bị thay thế -> không cho trình duyệt cache."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-store"
        return resp


app.mount("/output", NoCacheStatic(directory=config.OUTPUT_DIR), name="output")


class CreateJob(BaseModel):
    script: str
    mode: str = "auto"
    title: str = ""
    voice: str = "en-US-AndrewMultilingualNeural"
    rate: int = 10
    style: str = "cinematic"
    custom_style: str = ""
    extra_direction: str = ""
    provider: str = config.IMAGE_PROVIDER
    prompt_provider: str = "auto"
    music: str = ""
    music_volume: float = 0.12
    uppercase: bool = True
    words_per_caption: int = 3


class RenderReq(BaseModel):
    voice: str | None = None
    rate: int | None = None
    music: str | None = None
    music_volume: float | None = None
    uppercase: bool | None = None
    words_per_caption: int | None = None


class SplitReq(BaseModel):
    script: str


class PromptReq(BaseModel):
    prompt: str


def _err(e: Exception):
    if isinstance(e, FileNotFoundError):
        raise HTTPException(404, str(e))
    raise HTTPException(400, str(e))


@app.get("/")
def index():
    return FileResponse(config.STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/options")
def options():
    providers = []
    if config.GEMINI_API_KEY:
        providers.append({"id": "gemini", "name": f"Gemini API ({config.GEMINI_IMAGE_MODEL})"})
    if config.OPENAI_API_KEY:
        providers.append({"id": "openai", "name": f"OpenAI API ({config.OPENAI_IMAGE_MODEL})"})
    providers.append({"id": "placeholder", "name": "Ảnh test (miễn phí, không cần key)"})
    music = sorted(p.name for p in config.MUSIC_DIR.iterdir()
                   if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg"))
    default_provider = config.IMAGE_PROVIDER if any(p["id"] == config.IMAGE_PROVIDER for p in providers) \
        else providers[0]["id"]
    return {
        "voices": [{"id": v, "name": n} for v, n in config.VOICES],
        "styles": [{"id": k, "prompt": v} for k, v in config.STYLES.items()],
        "providers": providers,
        "default_provider": default_provider,
        "has_api_key": bool(config.GEMINI_API_KEY or config.OPENAI_API_KEY),
        "gemini_cli": gemini_cli.status(),
        "music": music,
    }


@app.post("/api/split")
def split(req: SplitReq):
    return {"scenes": split_scenes(req.script)}


@app.post("/api/jobs")
def create_job(req: CreateJob):
    if not req.script.strip():
        raise HTTPException(400, "Kịch bản trống")
    o = JobOptions.from_dict(req.model_dump())
    job = pipeline.start_prepare(o) if o.mode == "manual" else pipeline.start_auto(o)
    return job.to_dict()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Không tìm thấy job")
    return job.to_dict()


@app.get("/api/projects")
def projects():
    return pipeline.list_projects()


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    try:
        return pipeline.project_info(pid)
    except Exception as e:
        _err(e)


@app.post("/api/projects/{pid}/images/{index}")
async def upload_image(pid: str, index: int, request: Request):
    data = await request.body()
    if not data:
        raise HTTPException(400, "Không có dữ liệu ảnh")
    if len(data) > 40 * 1024 * 1024:
        raise HTTPException(400, "Ảnh quá lớn (>40MB)")
    try:
        return {"image": pipeline.save_scene_image(pid, index, data)}
    except Exception as e:
        _err(e)


@app.put("/api/projects/{pid}/prompts/{index}")
def edit_prompt(pid: str, index: int, req: PromptReq):
    try:
        pipeline.update_prompt(pid, index, req.prompt)
        return {"ok": True}
    except Exception as e:
        _err(e)


@app.post("/api/projects/{pid}/render")
def render_project(pid: str, req: RenderReq):
    try:
        overrides = {k: v for k, v in req.model_dump().items() if v is not None}
        return pipeline.start_render(pid, overrides).to_dict()
    except Exception as e:
        _err(e)


def main():
    url = f"http://localhost:{config.PORT}"
    print(f"\n  AI Shorts Maker dang chay tai: {url}\n  (Nhan Ctrl+C de tat)\n", flush=True)
    Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
