"""ffmpeg helpers: loudness, joining segments, rendering the clip, grabbing frames."""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Sequence
from pathlib import Path

_MEAN_VOLUME = re.compile(r"mean_volume:\s*(-?[\d.]+) dB")

VERTICAL_FILTER = (
    "[0:v]split=2[bg][fg];"
    # the background is blurred at a quarter of the size, which is several times cheaper
    "[bg]scale=180:320:force_original_aspect_ratio=increase,crop=180:320,"
    "boxblur=6:2,scale=720:1280,eq=brightness=-0.12[b];"
    "[fg]scale=720:-2[f];"
    "[b][f]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
)


class FFmpegError(RuntimeError):
    pass


async def _run(args: Sequence[str], timeout: float) -> tuple[bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        tail = err.decode("utf-8", errors="replace").strip().splitlines()[-3:]
        raise FFmpegError(f"ffmpeg exited with {proc.returncode}: {' | '.join(tail)}")
    return out, err


async def mean_volume(path: Path) -> float | None:
    """Average loudness of a file in dB (0 is the maximum, quieter is more negative)."""
    try:
        _, err = await _run(
            ["-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"], timeout=30
        )
    except (FFmpegError, TimeoutError):
        return None
    m = _MEAN_VOLUME.search(err.decode("utf-8", errors="replace"))
    return float(m.group(1)) if m else None


def concat_segments(paths: Sequence[Path], out: Path) -> None:
    """MPEG-TS can be joined by plain concatenation, no re-encoding needed."""
    with out.open("wb") as dst:
        for p in paths:
            with p.open("rb") as src:
                shutil.copyfileobj(src, dst, 1024 * 1024)


async def render(raw: Path, out: Path, offset: float, duration: float, vertical: bool) -> None:
    # -ss goes after -i on purpose: MPEG-TS has no index, so seeking on input lands
    # between keyframes and the video is lost until the next one. Decoding from the
    # start of the file costs a few seconds of video at most.
    args = ["-y", "-i", str(raw), "-ss", f"{offset:.2f}", "-t", f"{duration:.2f}"]
    if vertical:
        args += ["-filter_complex", VERTICAL_FILTER, "-map", "[v]", "-map", "0:a:0?"]
    else:
        args += ["-vf", "scale=-2:720,format=yuv420p"]
    args += [
        "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
        "-maxrate", "3M", "-bufsize", "6M",  # keeps even a 100-second clip under 50 MB
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out),
    ]  # fmt: skip
    await _run(args, timeout=600)


def split_jpegs(data: bytes) -> list[bytes]:
    """Split a stream of concatenated JPEG images (each runs from FFD8 to FFD9)."""
    frames: list[bytes] = []
    start = data.find(b"\xff\xd8")
    while start != -1:
        nxt = data.find(b"\xff\xd9\xff\xd8", start)
        if nxt == -1:
            frames.append(data[start:])
            break
        frames.append(data[start : nxt + 2])
        start = nxt + 2
    return frames


async def grab_frames(raw: Path, offset: float, duration: float, count: int = 3) -> list[bytes]:
    """JPEG stills spread evenly over the clip, for the neural network to look at."""
    step = duration / count
    graph = (
        f"trim=start={offset + step / 2:.2f}:duration={duration - step / 2:.2f},"
        f"setpts=PTS-STARTPTS,fps=1/{step:.3f},scale=960:-2"
    )
    try:
        out, _ = await _run(
            [
                "-i",
                str(raw),
                "-an",
                "-vf",
                graph,
                "-frames:v",
                str(count),
                "-q:v",
                "6",
                "-f",
                "image2pipe",
                "-c:v",
                "mjpeg",
                "pipe:1",
            ],  # fmt: skip
            timeout=180,
        )
    except (FFmpegError, TimeoutError):
        return []
    return split_jpegs(out)
