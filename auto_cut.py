#!/usr/bin/env python3
"""Auto cut detector for gameplay videos.

Pipeline:
  1. Extract mono PCM audio via ffmpeg.
  2. Compute per-second RMS energy.
  3. Find "combat" regions (sustained loud segments).
  4. Run PySceneDetect for scene boundaries.
  5. Clip combat regions to scene bounds (round boundaries respected).
  6. Merge close regions, drop too-short ones, pad each clip.
  7. If total length exceeds target, keep highest-energy clips.
  8. Emit a CMX3600 EDL for DaVinci Resolve.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scenedetect import ContentDetector, detect


SAMPLE_RATE = 16000


@dataclass
class Clip:
    start: float       # seconds in source
    end: float         # seconds in source
    energy: float      # mean RMS (for prioritization)

    @property
    def duration(self) -> float:
        return self.end - self.start


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, **kw)


def probe(video: Path) -> tuple[float, float]:
    """Return (duration_seconds, fps)."""
    out = run([
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate:format=duration",
        "-of", "json", str(video),
    ]).stdout
    data = json.loads(out)
    duration = float(data["format"]["duration"])
    num, den = data["streams"][0]["r_frame_rate"].split("/")
    fps = float(num) / float(den)
    return duration, fps


def extract_audio_rms(video: Path, window_sec: float = 1.0) -> np.ndarray:
    """Stream mono PCM through ffmpeg and return per-window RMS array."""
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(video),
        "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "s16le", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    raw = proc.stdout.read()
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.read().decode(errors="ignore"))

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    win = int(SAMPLE_RATE * window_sec)
    n_windows = len(samples) // win
    samples = samples[: n_windows * win].reshape(n_windows, win)
    rms = np.sqrt(np.mean(samples ** 2, axis=1))
    return rms


def detect_combat_regions(
    rms: np.ndarray,
    threshold_percentile: float,
    min_duration: float,
) -> list[tuple[float, float, float]]:
    """Return list of (start_sec, end_sec, mean_rms) where audio is sustained loud."""
    if len(rms) == 0:
        return []
    threshold = np.percentile(rms, threshold_percentile)
    above = rms > threshold
    regions: list[tuple[float, float, float]] = []
    in_region = False
    start = 0
    for i, val in enumerate(above):
        if val and not in_region:
            start, in_region = i, True
        elif not val and in_region:
            if i - start >= min_duration:
                mean_e = float(rms[start:i].mean())
                regions.append((float(start), float(i), mean_e))
            in_region = False
    if in_region and len(rms) - start >= min_duration:
        mean_e = float(rms[start:].mean())
        regions.append((float(start), float(len(rms)), mean_e))
    return regions


def get_scene_bounds(video: Path, threshold: float) -> list[tuple[float, float]]:
    """Return list of (start_sec, end_sec) per scene."""
    scenes = detect(str(video), ContentDetector(threshold=threshold))
    if not scenes:
        return []
    return [(s.get_seconds(), e.get_seconds()) for s, e in scenes]


def clip_to_scenes(
    regions: list[tuple[float, float, float]],
    scenes: list[tuple[float, float]],
    duration: float,
) -> list[Clip]:
    """Intersect each combat region with each scene; emit one clip per intersection."""
    if not scenes:
        scenes = [(0.0, duration)]

    clips: list[Clip] = []
    for r_start, r_end, energy in regions:
        for s_start, s_end in scenes:
            a = max(r_start, s_start)
            b = min(r_end, s_end)
            if b - a > 0:
                clips.append(Clip(a, b, energy))
    return clips


def merge_close(clips: list[Clip], merge_gap: float) -> list[Clip]:
    if not clips:
        return []
    clips = sorted(clips, key=lambda c: c.start)
    merged = [clips[0]]
    for c in clips[1:]:
        last = merged[-1]
        if c.start - last.end <= merge_gap:
            last.end = max(last.end, c.end)
            last.energy = max(last.energy, c.energy)
        else:
            merged.append(c)
    return merged


def pad_clips(
    clips: list[Clip],
    pad_before: float,
    pad_after: float,
    duration: float,
) -> list[Clip]:
    out = []
    for c in clips:
        out.append(Clip(
            start=max(0.0, c.start - pad_before),
            end=min(duration, c.end + pad_after),
            energy=c.energy,
        ))
    return merge_close(out, merge_gap=0.0)


def trim_to_target(clips: list[Clip], target_sec: float) -> list[Clip]:
    """If total exceeds target, drop lowest-energy clips first."""
    total = sum(c.duration for c in clips)
    if total <= target_sec:
        return clips
    ranked = sorted(clips, key=lambda c: c.energy, reverse=True)
    kept: list[Clip] = []
    acc = 0.0
    for c in ranked:
        if acc + c.duration <= target_sec:
            kept.append(c)
            acc += c.duration
    kept.sort(key=lambda c: c.start)
    return kept


def sec_to_tc(seconds: float, fps: float) -> str:
    """Non-drop-frame timecode HH:MM:SS:FF."""
    total_frames = int(round(seconds * fps))
    fps_int = int(round(fps))
    f = total_frames % fps_int
    s = (total_frames // fps_int) % 60
    m = (total_frames // (fps_int * 60)) % 60
    h = total_frames // (fps_int * 3600)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def write_edl(clips: list[Clip], video: Path, fps: float, out: Path) -> None:
    title = out.stem.upper()[:60]
    lines = [f"TITLE: {title}", "FCM: NON-DROP FRAME", ""]
    rec_cursor = 0.0
    reel = "AX"
    clip_name = video.name
    for i, c in enumerate(clips, start=1):
        src_in = sec_to_tc(c.start, fps)
        src_out = sec_to_tc(c.end, fps)
        rec_in = sec_to_tc(rec_cursor, fps)
        rec_out = sec_to_tc(rec_cursor + c.duration, fps)
        rec_cursor += c.duration
        lines.append(
            f"{i:03d}  {reel:<8} V     C        "
            f"{src_in} {src_out} {rec_in} {rec_out}"
        )
        lines.append(f"* FROM CLIP NAME: {clip_name}")
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")


@dataclass
class Config:
    target_minutes: float = 20.0
    rms_percentile: float = 70.0
    min_loud: float = 3.0
    merge_gap: float = 3.0
    min_clip: float = 5.0
    pad_before: float = 2.0
    pad_after: float = 3.0
    scene_threshold: float = 27.0
    fps_override: float | None = None


@contextmanager
def _ticker(log, label: str, interval: float = 5.0):
    """While the wrapped block runs, periodically log elapsed time."""
    stop = threading.Event()
    start = time.time()

    def run():
        while not stop.wait(interval):
            elapsed = int(time.time() - start)
            mm, ss = divmod(elapsed, 60)
            stamp = f"{mm}분 {ss}초" if mm else f"{ss}초"
            log(f"      \u21b3 {label}: {stamp} 경과...")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=1.0)


def run_pipeline(
    video: Path,
    output: Path,
    config: Config,
    log=print,
) -> dict:
    """Run the full pipeline. Returns summary dict.

    `log` is called with a single string per progress message; pass a
    custom callable from a GUI to capture output.
    """
    log(f"[1/5] 영상 분석 중: {video.name}")
    duration, fps = probe(video)
    if config.fps_override:
        fps = config.fps_override
    log(f"      길이 {duration:.1f}초 / fps {fps:.3f}")

    log("[2/5] 오디오 추출\u00b7RMS 계산 중...")
    with _ticker(log, "오디오 분석"):
        rms = extract_audio_rms(video)
    log(f"      윈도우 {len(rms)}개  평균 {rms.mean():.4f}  최대 {rms.max():.4f}")

    log("[3/5] 교전 구간 감지 중...")
    regions = detect_combat_regions(rms, config.rms_percentile, config.min_loud)
    log(f"      {len(regions)}개 구간")

    log("[4/5] 장면 전환 감지 중... (영상 길이에 따라 수십 초~수 분 소요)")
    with _ticker(log, "장면 감지"):
        scenes = get_scene_bounds(video, config.scene_threshold)
    log(f"      {len(scenes)}개 장면")

    clips = clip_to_scenes(regions, scenes, duration)
    clips = merge_close(clips, config.merge_gap)
    clips = [c for c in clips if c.duration >= config.min_clip]
    clips = pad_clips(clips, config.pad_before, config.pad_after, duration)
    clips = trim_to_target(clips, config.target_minutes * 60.0)

    total = sum(c.duration for c in clips)
    log(f"[5/5] 클립 {len(clips)}개 / 총 {total/60:.1f}분")

    write_edl(clips, video, fps, output)
    log(f"      \u2192 저장: {output}")

    return {
        "clips": len(clips),
        "total_seconds": total,
        "fps": fps,
        "duration": duration,
        "output": str(output),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("video", type=Path, help="Input video file")
    p.add_argument("-o", "--output", type=Path, default=None,
                   help="Output EDL path (default: <input>.edl)")
    p.add_argument("--target-minutes", type=float, default=20.0)
    p.add_argument("--rms-percentile", type=float, default=70.0)
    p.add_argument("--min-loud", type=float, default=3.0)
    p.add_argument("--merge-gap", type=float, default=3.0)
    p.add_argument("--min-clip", type=float, default=5.0)
    p.add_argument("--pad-before", type=float, default=2.0)
    p.add_argument("--pad-after", type=float, default=3.0)
    p.add_argument("--scene-threshold", type=float, default=27.0)
    p.add_argument("--fps-override", type=float, default=None)
    args = p.parse_args()

    if not args.video.exists():
        print(f"error: not found: {args.video}", file=sys.stderr)
        return 2
    output = args.output or args.video.with_suffix(".edl")

    config = Config(
        target_minutes=args.target_minutes,
        rms_percentile=args.rms_percentile,
        min_loud=args.min_loud,
        merge_gap=args.merge_gap,
        min_clip=args.min_clip,
        pad_before=args.pad_before,
        pad_after=args.pad_after,
        scene_threshold=args.scene_threshold,
        fps_override=args.fps_override,
    )
    run_pipeline(args.video, output, config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
