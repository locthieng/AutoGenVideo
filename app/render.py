"""Ghép ảnh + giọng đọc + phụ đề + nhạc nền thành video bằng ffmpeg."""
from __future__ import annotations

import random
import subprocess
import wave
from pathlib import Path
from typing import Callable

import imageio_ffmpeg

from . import config

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_ffmpeg(args: list[str], cwd: Path | None = None) -> None:
    proc = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
                          cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg lỗi: {proc.stderr[-1500:]}")


def mp3_to_wav(src: Path, dst: Path, start: float | None = None, end: float | None = None) -> float:
    """Chuyển sang WAV (cắt bớt khoảng lặng nếu có start/end). Trả về độ dài (giây)."""
    args = ["-i", str(src)]
    if start is not None:
        args += ["-ss", f"{max(0.0, start):.3f}"]
    if end is not None:
        args += ["-to", f"{end:.3f}"]
    args += ["-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(dst)]
    run_ffmpeg(args)
    return wav_duration(dst)


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def concat_wavs(paths: list[Path], dst: Path, gap: float = 0.0) -> list[float]:
    """Nối các file WAV, trả về danh sách độ dài thực tế của từng đoạn (gồm gap)."""
    durations: list[float] = []
    with wave.open(str(paths[0]), "rb") as first:
        params = first.getparams()
    silence = b"\x00" * (int(params.framerate * gap) * params.sampwidth * params.nchannels)
    with wave.open(str(dst), "wb") as out:
        out.setparams(params)
        for i, p in enumerate(paths):
            with wave.open(str(p), "rb") as w:
                frames = w.readframes(w.getnframes())
                out.writeframes(frames)
                n = w.getnframes()
            if gap and i < len(paths) - 1:
                out.writeframes(silence)
                n += int(params.framerate * gap)
            durations.append(n / params.framerate)
    return durations


# Hiệu ứng chuyển động cho ảnh tĩnh (Ken Burns). on = số frame hiện tại, F = tổng frame.
_EFFECTS = {
    "zoom_in": ("1+0.18*on/{F}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    "zoom_out": ("1.18-0.18*on/{F}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    "pan_right": ("1.15", "(iw-iw/zoom)*on/{F}", "ih/2-(ih/zoom/2)"),
    "pan_left": ("1.15", "(iw-iw/zoom)*(1-on/{F})", "ih/2-(ih/zoom/2)"),
    "pan_down": ("1.15", "iw/2-(iw/zoom/2)", "(ih-ih/zoom)*on/{F}"),
    "pan_up": ("1.15", "iw/2-(iw/zoom/2)", "(ih-ih/zoom)*(1-on/{F})"),
}


def pick_effects(n: int) -> list[str]:
    seq = ["zoom_in"]  # cảnh đầu luôn zoom vào cho cuốn hút
    names = list(_EFFECTS)
    while len(seq) < n:
        choice = random.choice(names)
        if choice != seq[-1]:
            seq.append(choice)
    return seq


def render_video(job_dir: Path, images: list[Path], voice_wav: Path, durations: list[float],
                 subs_ass: Path, font_file: Path, out_path: Path, music: Path | None,
                 music_volume: float, progress: Callable[[float], None]) -> Path:
    fps = config.FPS
    w, h = config.WIDTH, config.HEIGHT

    # Đưa font vào thư mục job để dùng đường dẫn tương đối (tránh lỗi dấu ":" của Windows trong ffmpeg)
    fonts_dir = job_dir / "fonts"
    fonts_dir.mkdir(exist_ok=True)
    (fonts_dir / font_file.name).write_bytes(font_file.read_bytes())

    # số frame mỗi cảnh, tính theo mốc thời gian cộng dồn để không bị lệch tiếng
    frames: list[int] = []
    t = 0.0
    for d in durations:
        frames.append(round((t + d) * fps) - round(t * fps))
        t += d
    total = sum(durations)

    effects = pick_effects(len(images))
    inputs: list[str] = []
    filters: list[str] = []
    for i, (img, f, eff) in enumerate(zip(images, frames, effects)):
        inputs += ["-i", img.relative_to(job_dir).as_posix()]
        z, x, y = (e.format(F=max(1, f - 1)) for e in _EFFECTS[eff])
        filters.append(
            f"[{i}:v]scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,"
            f"crop={w * 2}:{h * 2},setsar=1,"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={f}:s={w}x{h}:fps={fps},"
            f"scale=out_range=tv,setsar=1,format=yuv420p[v{i}]"
        )
    n = len(images)
    vi = n
    inputs += ["-i", voice_wav.relative_to(job_dir).as_posix()]
    concat_in = "".join(f"[v{i}]" for i in range(n))
    filters.append(f"{concat_in}concat=n={n}:v=1:a=0[vc]")
    filters.append(f"[vc]subtitles={subs_ass.relative_to(job_dir).as_posix()}:fontsdir=fonts[vout]")

    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        filters.append(
            f"[{vi + 1}:a]volume={music_volume},atrim=0:{total:.3f},"
            f"afade=t=out:st={max(0.0, total - 1.5):.3f}:d=1.5[m]"
        )
        filters.append(f"[{vi}:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]")
        amap = "[aout]"
    else:
        amap = f"{vi}:a"

    args = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", amap,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}",
        "-movflags", "+faststart", "-progress", "pipe:1", "-nostats",
        out_path.relative_to(job_dir).as_posix(),
    ]
    (job_dir / "ffmpeg_cmd.txt").write_text(subprocess.list2cmdline(args), encoding="utf-8")

    proc = subprocess.Popen(args, cwd=job_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                us = int(line.split("=", 1)[1])
                progress(min(1.0, us / 1_000_000 / total))
            except ValueError:
                pass
    stderr = proc.stderr.read() if proc.stderr else ""
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg lỗi khi render: {stderr[-2000:]}")
    progress(1.0)
    return out_path
