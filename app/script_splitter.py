"""Chia kịch bản thành các cảnh (scene)."""
from __future__ import annotations

import re

# Mỗi cảnh nên dài khoảng 2.5 - 6 giây giọng đọc (~8 - 20 từ tiếng Anh)
MIN_WORDS = 7
MAX_WORDS = 20

_SENTENCE_RE = re.compile(r"(?<=[.!?…])[\"')\]]*\s+(?=[A-Z0-9\"“'(])")


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Bỏ các chỉ dẫn kiểu [Music], (pause), dòng "Scene 1:" ...
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"(?im)^\s*(scene|cảnh|hook|outro|intro)\s*\d*\s*[:\-–]\s*", "", text)
    return text


def split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for para in _clean(text).split("\n"):
        para = re.sub(r"\s+", " ", para).strip()
        if not para:
            continue
        for s in _SENTENCE_RE.split(para):
            s = s.strip()
            if s:
                sentences.append(s)
    return sentences


def _split_long(sentence: str) -> list[str]:
    """Câu quá dài -> cắt tại dấu phẩy/chấm phẩy, nếu không có thì cắt theo số từ."""
    words = sentence.split()
    if len(words) <= MAX_WORDS:
        return [sentence]
    parts = [p.strip() for p in re.split(r"(?<=[,;:—–])\s+", sentence) if p.strip()]
    out: list[str] = []
    cur: list[str] = []
    for p in parts:
        pw = p.split()
        if cur and len(cur) + len(pw) > MAX_WORDS:
            out.append(" ".join(cur))
            cur = []
        cur.extend(pw)
    if cur:
        out.append(" ".join(cur))
    final: list[str] = []
    for chunk in out:
        w = chunk.split()
        while len(w) > MAX_WORDS:
            final.append(" ".join(w[:MAX_WORDS]))
            w = w[MAX_WORDS:]
        if w:
            final.append(" ".join(w))
    return final


def split_scenes(text: str) -> list[str]:
    pieces: list[str] = []
    for s in split_sentences(text):
        pieces.extend(_split_long(s))

    scenes: list[str] = []
    cur = ""
    for p in pieces:
        if not cur:
            cur = p
        elif len(cur.split()) < MIN_WORDS and len(cur.split()) + len(p.split()) <= MAX_WORDS:
            cur = f"{cur} {p}"
        else:
            scenes.append(cur)
            cur = p
    if cur:
        if scenes and len(cur.split()) < 4 and len(scenes[-1].split()) + len(cur.split()) <= MAX_WORDS + 5:
            scenes[-1] = f"{scenes[-1]} {cur}"
        else:
            scenes.append(cur)
    return scenes
