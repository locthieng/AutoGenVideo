"""Cấu hình chung, đọc từ file .env."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

OUTPUT_DIR = ROOT / "output"
ASSETS_DIR = ROOT / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
MUSIC_DIR = ASSETS_DIR / "music"
STATIC_DIR = Path(__file__).resolve().parent / "static"

for _d in (OUTPUT_DIR, FONTS_DIR, MUSIC_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Video
WIDTH = 1080
HEIGHT = 1920
FPS = 30


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


IMAGE_PROVIDER = env("IMAGE_PROVIDER", "gemini").lower()

GEMINI_API_KEY = env("GEMINI_API_KEY")
GEMINI_IMAGE_MODEL = env("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
GEMINI_TEXT_MODEL = env("GEMINI_TEXT_MODEL", "gemini-2.5-flash")

OPENAI_API_KEY = env("OPENAI_API_KEY")
OPENAI_IMAGE_MODEL = env("OPENAI_IMAGE_MODEL", "gpt-image-1")
OPENAI_IMAGE_QUALITY = env("OPENAI_IMAGE_QUALITY", "medium")
OPENAI_TEXT_MODEL = env("OPENAI_TEXT_MODEL", "gpt-4.1-mini")

IMAGE_CONCURRENCY = max(1, int(env("IMAGE_CONCURRENCY", "3") or 3))
GEMINI_WEB_PORT = int(env("GEMINI_WEB_PORT", "9223") or 9223)
GEMINI_WEB_TIMEOUT = max(30, int(env("GEMINI_WEB_TIMEOUT", "240") or 240))
GEMINI_WEB_LOGIN_TIMEOUT = max(30, int(env("GEMINI_WEB_LOGIN_TIMEOUT", "300") or 300))
GEMINI_WEB_PROFILE = ROOT / ".browser-profile"
CHATGPT_WEB_PORT = int(env("CHATGPT_WEB_PORT", "9224") or 9224)
CHATGPT_WEB_TIMEOUT = max(30, int(env("CHATGPT_WEB_TIMEOUT", "360") or 360))
CHATGPT_WEB_LOGIN_TIMEOUT = max(30, int(env("CHATGPT_WEB_LOGIN_TIMEOUT", "300") or 300))
CHATGPT_WEB_PROFILE = ROOT / ".chatgpt-browser-profile"
PORT = int(env("PORT", "8000") or 8000)
# Thời lượng hiện ảnh thumbnail ở đầu video (giây)
THUMB_SECONDS = max(0.0, float(env("THUMB_SECONDS", "0.5") or 0.5))

VOICES = [
    ("en-US-AndrewMultilingualNeural", "Andrew (Nam, US) - ấm, kể chuyện"),
    ("en-US-BrianMultilingualNeural", "Brian (Nam, US) - trẻ, năng động"),
    ("en-US-ChristopherNeural", "Christopher (Nam, US) - trầm, nghiêm túc"),
    ("en-US-GuyNeural", "Guy (Nam, US) - tin tức"),
    ("en-GB-RyanNeural", "Ryan (Nam, UK)"),
    ("en-US-AvaMultilingualNeural", "Ava (Nữ, US) - tự nhiên"),
    ("en-US-EmmaMultilingualNeural", "Emma (Nữ, US) - vui vẻ"),
    ("en-US-JennyNeural", "Jenny (Nữ, US)"),
    ("en-GB-SoniaNeural", "Sonia (Nữ, UK)"),
]

STYLES = {
    "cinematic": "cinematic photorealistic, dramatic lighting, shallow depth of field, 35mm film look, highly detailed",
    "history": "epic historical painting, cinematic realism, period-accurate clothing and architecture, dramatic lighting",
    "horror": "dark eerie horror atmosphere, foggy, desaturated colors, cinematic, unsettling, highly detailed",
    "3d": "3D animated movie style, Pixar-like, soft lighting, vibrant colors, cute and expressive",
    "anime": "high quality anime illustration, Studio Ghibli inspired, vibrant colors, detailed background",
    "scifi": "futuristic sci-fi concept art, neon lighting, cinematic, ultra detailed, epic scale",
    "documentary": "National Geographic documentary photography, natural light, realistic, sharp detail",
}
