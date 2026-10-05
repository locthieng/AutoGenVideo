"""Giọng đọc bằng Edge TTS (miễn phí) + thời gian từng từ cho phụ đề."""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from pathlib import Path

import edge_tts


@dataclass
class Word:
    text: str
    start: float  # giây, tính từ đầu đoạn audio
    end: float


def _make_communicate(text: str, voice: str, rate: str, pitch: str) -> edge_tts.Communicate:
    kwargs = {"rate": rate, "pitch": pitch}
    # edge-tts >= 7 mặc định chỉ trả SentenceBoundary, cần yêu cầu WordBoundary
    if "boundary" in inspect.signature(edge_tts.Communicate.__init__).parameters:
        kwargs["boundary"] = "WordBoundary"
    return edge_tts.Communicate(text, voice, **kwargs)


async def _synth(text: str, voice: str, rate: str, pitch: str, out_path: Path) -> list[Word]:
    comm = _make_communicate(text, voice, rate, pitch)
    words: list[Word] = []
    with open(out_path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 10_000_000
                dur = chunk["duration"] / 10_000_000
                words.append(Word(chunk["text"], start, start + dur))
    return words


def synthesize(text: str, voice: str, out_path: Path, rate: str = "+0%", pitch: str = "+0Hz",
               retries: int = 3) -> list[Word]:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            words = asyncio.run(_synth(text, voice, rate, pitch, out_path))
            if out_path.exists() and out_path.stat().st_size > 0:
                return words
            raise RuntimeError("Edge TTS không trả về audio")
        except Exception as e:  # mạng chập chờn -> thử lại
            last_err = e
    raise RuntimeError(f"Lỗi tạo giọng đọc: {last_err}")
