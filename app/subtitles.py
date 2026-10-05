"""Tạo phụ đề kiểu Shorts (hiện 1-3 từ, tô màu từ đang đọc) dưới dạng file .ass."""
from __future__ import annotations

import re
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import config
from .tts import Word

FONT_URL = "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf"


@dataclass
class SubWord:
    text: str
    start: float
    end: float


def ensure_font(log=print) -> tuple[str, Path]:
    """Trả về (tên font, đường dẫn file). Tải font Anton nếu chưa có, lỗi thì dùng Arial Black."""
    anton = config.FONTS_DIR / "Anton-Regular.ttf"
    if not anton.exists():
        try:
            log("Đang tải font Anton cho phụ đề...")
            urllib.request.urlretrieve(FONT_URL, anton)
        except Exception as e:
            log(f"⚠️ Không tải được font Anton ({e}), dùng font Windows.")
            anton.unlink(missing_ok=True)
    if anton.exists() and anton.stat().st_size > 10_000:
        return "Anton", anton
    for name, file in (("Arial Black", "ariblk.ttf"), ("Arial", "arialbd.ttf")):
        src = Path("C:/Windows/Fonts") / file
        if src.exists():
            dst = config.FONTS_DIR / file
            if not dst.exists():
                shutil.copy(src, dst)
            return name, dst
    raise RuntimeError("Không tìm thấy font cho phụ đề. Hãy bỏ 1 file .ttf vào assets/fonts.")


def align_words(scene_text: str, words: list[Word]) -> list[SubWord]:
    """Ghép mốc thời gian của TTS với chữ gốc trong kịch bản (giữ số, ký hiệu $, %...)."""
    out: list[SubWord] = []
    src = scene_text
    low = src.lower()
    cursor = 0
    for i, w in enumerate(words):
        token = w.text.strip()
        idx = low.find(token.lower(), cursor) if token else -1
        if idx == -1 or idx - cursor > 40:
            display = token
        else:
            # gồm cả phần chữ bị bỏ qua phía trước (vd: ký hiệu TTS không đọc) và dấu câu phía sau
            end = idx + len(token)
            while end < len(src) and not src[end].isspace():
                end += 1
            lead = src[cursor:idx].strip()
            display = (lead + " " if lead and out == [] else "") + src[idx:end]
            if lead and out:
                out[-1].text = f"{out[-1].text} {lead}"
            cursor = end
        if display:
            out.append(SubWord(display, w.start, w.end))
    # chữ còn lại cuối câu chưa khớp
    rest = src[cursor:].strip()
    if rest and out and len(rest) < 25:
        out[-1].text = f"{out[-1].text} {rest}"
    return out


def _clean_display(text: str, uppercase: bool) -> str:
    text = text.strip().strip("\"“”‘’()[]")
    text = re.sub(r"[,.;:…—–]+$", "", text)
    text = text.replace("{", "(").replace("}", ")").replace("\\", "")
    return text.upper() if uppercase else text


def _chunks(words: list[SubWord], max_words: int, max_chars: int) -> list[list[SubWord]]:
    chunks: list[list[SubWord]] = []
    cur: list[SubWord] = []
    for w in words:
        if cur and (len(cur) >= max_words or len(" ".join(x.text for x in cur + [w])) > max_chars
                    or w.start - cur[-1].end > 0.6):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.!?;:,]$", w.text):  # ngắt chunk ở dấu câu
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass(words: list[SubWord], font_name: str, out_path: Path, total_duration: float,
              uppercase: bool = True, max_words: int = 3, highlight: str = "&H0000FFFF&",
              font_size: int = 110, position: float = 0.30) -> Path:
    """position: khoảng cách từ đáy màn hình (tỉ lệ chiều cao)."""
    margin_v = int(config.HEIGHT * position)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {config.WIDTH}
PlayResY: {config.HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{font_name},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,7,4,2,80,80,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines: list[str] = []
    chunks = _chunks(words, max_words=max_words, max_chars=22 if uppercase else 26)
    for ci, chunk in enumerate(chunks):
        next_start = chunks[ci + 1][0].start if ci + 1 < len(chunks) else total_duration
        chunk_end = min(next_start, chunk[-1].end + 0.35)
        texts = [_clean_display(w.text, uppercase) for w in chunk]
        for wi, w in enumerate(chunk):
            start = w.start
            end = chunk[wi + 1].start if wi + 1 < len(chunk) else chunk_end
            if end <= start:
                continue
            parts = []
            for j, t in enumerate(texts):
                parts.append(f"{{\\c{highlight}}}{t}{{\\c&H00FFFFFF&}}" if j == wi else t)
            pop = "{\\fscx85\\fscy85\\t(0,90,\\fscx100\\fscy100)}" if wi == 0 else ""
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Main,,0,0,0,,{pop}{' '.join(parts)}")
    out_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return out_path
