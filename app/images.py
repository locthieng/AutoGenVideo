"""Tạo prompt hình ảnh và tạo ảnh AI cho từng cảnh."""
from __future__ import annotations

import base64
import io
import json
import random
import re
import time
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config

NO_TEXT = "vertical 9:16 composition, no text, no letters, no captions, no watermark, no logo"


# ---------------------------------------------------------------- prompts

PROMPT_INSTRUCTIONS = """You are the visual director of a viral YouTube Shorts channel.
The narration script is split into {n} scenes (below). For EACH scene write ONE image-generation prompt
that visually illustrates what is being said, so the viewer instantly understands it.

Rules:
- Visual style for every image: {style}
- First decide on recurring characters/places and describe them IDENTICALLY every time they appear
  (age, ethnicity, clothing, hair...) so images stay consistent.
- Be concrete: subject, action, setting, camera angle (close-up, wide shot, low angle...), lighting, mood.
- Make the first image extremely eye-catching (it is the hook).
- Never ask for text, captions, signs, logos or watermarks in the image.
- Avoid gore, nudity, real celebrities' names; describe people generically instead.
- Each prompt 25-60 words, in English.
{extra}
Return ONLY JSON: {{"prompts": ["prompt for scene 1", "prompt for scene 2", ...]}} with exactly {n} items.

Scenes:
{scenes}
"""


def _parse_prompts(raw: str, n: int, *, strict: bool = False) -> list[str] | None:
    raw = raw.strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    prompts = data.get("prompts") if isinstance(data, dict) else None
    if not isinstance(prompts, list) or not prompts:
        return None
    if strict and (len(prompts) != n or any(not isinstance(p, str) or not p.strip() for p in prompts)):
        return None
    prompts = [str(p).strip() for p in prompts]
    if len(prompts) < n:
        prompts += prompts[-1:] * (n - len(prompts))
    return prompts[:n]


def build_prompts(scenes: list[str], style: str, extra: str, provider: str,
                  log: Callable[[str], None], cwd: Path | None = None) -> list[str]:
    """Dùng LLM viết prompt ảnh; nếu lỗi thì dùng luôn câu thoại + style.

    provider: gemini-cli | gemini | openai | none
    """
    fallback = [f"{s.rstrip('.!?… ')}. {style}" for s in scenes]
    if provider in ("placeholder", "none", ""):
        return fallback

    text = PROMPT_INSTRUCTIONS.format(
        n=len(scenes),
        style=style,
        extra=f"- Extra direction from the creator: {extra}" if extra.strip() else "",
        scenes="\n".join(f"{i + 1}. {s}" for i, s in enumerate(scenes)),
    )
    try:
        if provider == "gemini-cli":
            from . import gemini_cli

            log("Đang nhờ Gemini (tài khoản của bạn) viết mô tả ảnh...")
            raw = gemini_cli.ask(text, cwd or config.OUTPUT_DIR)
        elif provider == "gemini":
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=config.GEMINI_API_KEY)
            resp = client.models.generate_content(
                model=config.GEMINI_TEXT_MODEL,
                contents=text,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            raw = resp.text or ""
        elif provider == "openai":
            from openai import OpenAI

            client = OpenAI(api_key=config.OPENAI_API_KEY)
            resp = client.chat.completions.create(
                model=config.OPENAI_TEXT_MODEL,
                messages=[{"role": "user", "content": text}],
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or ""
        else:
            return fallback
        prompts = _parse_prompts(raw, len(scenes))
        if prompts:
            return prompts
        log("⚠️ LLM trả về sai định dạng, dùng câu thoại làm prompt.")
    except Exception as e:
        log(f"⚠️ Không viết được prompt bằng AI ({e}); dùng câu thoại làm prompt.")
    return fallback


# ---------------------------------------------------------------- images

def _fit_cover(img: Image.Image, w: int = config.WIDTH, h: int = config.HEIGHT) -> Image.Image:
    img = img.convert("RGB")
    scale = max(w / img.width, h / img.height)
    nw, nh = round(img.width * scale), round(img.height * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def save_upload(data: bytes, out_path: Path) -> Path:
    """Lưu ảnh người dùng tải lên (png/jpg/webp, mọi kích thước) thành 1080x1920 jpg."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise ValueError(f"File không phải ảnh hợp lệ: {e}")
    _fit_cover(img).save(out_path, quality=95)
    return out_path


def _gemini_image(prompt: str) -> bytes:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    model = config.GEMINI_IMAGE_MODEL

    if model.startswith("imagen"):
        resp = client.models.generate_images(
            model=model,
            prompt=prompt,
            config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio="9:16"),
        )
        if not resp.generated_images:
            raise RuntimeError("Imagen không trả về ảnh (có thể bị chặn bởi bộ lọc an toàn)")
        return resp.generated_images[0].image.image_bytes

    cfg_kwargs: dict = {"response_modalities": ["IMAGE"]}
    if hasattr(types, "ImageConfig"):
        cfg_kwargs["image_config"] = types.ImageConfig(aspect_ratio="9:16")
    resp = client.models.generate_content(
        model=model, contents=prompt, config=types.GenerateContentConfig(**cfg_kwargs)
    )
    for cand in resp.candidates or []:
        for part in (cand.content.parts if cand.content else []) or []:
            if getattr(part, "inline_data", None) and part.inline_data.data:
                data = part.inline_data.data
                return base64.b64decode(data) if isinstance(data, str) else data
    raise RuntimeError("Gemini không trả về ảnh (có thể bị chặn bởi bộ lọc an toàn)")


def _openai_image(prompt: str) -> bytes:
    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    model = config.OPENAI_IMAGE_MODEL
    if model.startswith("dall-e"):
        resp = client.images.generate(model=model, prompt=prompt, size="1024x1792",
                                      response_format="b64_json", n=1)
    else:
        resp = client.images.generate(model=model, prompt=prompt, size="1024x1536",
                                      quality=config.OPENAI_IMAGE_QUALITY, n=1)
    return base64.b64decode(resp.data[0].b64_json)


def _placeholder_image(prompt: str, index: int) -> Image.Image:
    rnd = random.Random(index * 7919 + len(prompt))
    c1 = tuple(rnd.randint(20, 120) for _ in range(3))
    c2 = tuple(rnd.randint(100, 230) for _ in range(3))
    w, h = config.WIDTH, config.HEIGHT
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        draw.line([(0, y), (w, y)], fill=tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)))
    for _ in range(14):
        r = rnd.randint(60, 260)
        x, y = rnd.randint(0, w), rnd.randint(0, h)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=tuple(min(255, v + 40) for v in c2))
    img = img.filter(ImageFilter.GaussianBlur(40))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 64)
    except OSError:
        font = ImageFont.load_default()
    draw.text((w // 2, 260), f"SCENE {index + 1}", fill="white", font=font, anchor="mm")
    return img


def generate_image(prompt: str, index: int, out_path: Path, provider: str,
                   log: Callable[[str], None], retries: int = 3) -> Path:
    full_prompt = f"{prompt}. {NO_TEXT}"
    if provider == "placeholder":
        _placeholder_image(prompt, index).save(out_path, quality=95)
        return out_path

    gen = _gemini_image if provider == "gemini" else _openai_image
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            p = full_prompt if attempt < retries - 1 else (
                # lần cuối: prompt nhẹ nhàng hơn để tránh bộ lọc an toàn
                f"{prompt.split('.')[0]}. Safe, family-friendly, non-graphic. {NO_TEXT}"
            )
            data = gen(p)
            img = _fit_cover(Image.open(io.BytesIO(data)))
            img.save(out_path, quality=95)
            return out_path
        except Exception as e:
            last_err = e
            msg = str(e)
            wait = 20 if ("429" in msg or "rate" in msg.lower() or "quota" in msg.lower()) else 3
            log(f"⚠️ Ảnh cảnh {index + 1} lỗi (lần {attempt + 1}): {msg[:200]}")
            time.sleep(wait * (attempt + 1))
    raise RuntimeError(f"Không tạo được ảnh cảnh {index + 1}: {last_err}")
