#!/usr/bin/env python3
"""Step 2: assemble a random montage from video clips + master audio.

Default layout (when run with no args):
- <workdir>/audio        : one master track (.wav/.flac/.mp3)
- <workdir>/video_clips  : source clips (.mp4/.mov)
- output_automated       : output video
"""

from __future__ import annotations

import argparse
import random
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

AUDIO_EXTS = {".wav", ".flac", ".mp3"}
VIDEO_EXTS = {".mp4", ".mov"}
TRANSITIONS = (
    "fade",
    "distance",
    "smoothleft",
    "smoothright",
    "smoothup",
    "smoothdown",
)

BASE_DIR = Path(__file__).parent
DEFAULT_INPUT_ROOT = BASE_DIR / "input_automated"
DEFAULT_OUTPUT_ROOT = BASE_DIR / "output_automated"
DEFAULT_AUDIO_DIRNAME = "audio"
DEFAULT_VIDEO_DIRNAME = "video_clips"
DEFAULT_WATERMARK_REL_PATH = Path("watermark") / "watermark.mp4"


@dataclass(frozen=True)
class Clip:
    path: Path
    duration: float


@dataclass(frozen=True)
class Transition:
    name: str
    duration: float
    offset: float


def run_cmd(cmd: Sequence[str], capture_output: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, capture_output=capture_output)


def _encoder_args_supported(args: list[str]) -> bool:
    test_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=128x128:rate=30",
        "-frames:v",
        "1",
        *args,
        "-an",
        "-f",
        "null",
        "-",
    ]
    try:
        run_cmd(test_cmd, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _platform_hw_encoder_order() -> list[str]:
    """Return hardware encoder preference order for the current platform."""
    if sys.platform == "darwin":
        return ["h264_videotoolbox", "h264_nvenc", "h264_qsv", "h264_amf"]
    if sys.platform.startswith("win"):
        return ["h264_nvenc", "h264_amf", "h264_qsv", "h264_videotoolbox"]
    return ["h264_nvenc", "h264_qsv", "h264_amf", "h264_videotoolbox"]


def detect_best_encoder(crf: int, preset: str, fps: int, maxrate: str, bufsize: str) -> tuple[str, list[str]]:
    gop = fps * 2

    encoder_candidates: dict[str, list[str]] = {
        "h264_videotoolbox": [
            "-c:v",
            "h264_videotoolbox",
            "-q:v",
            "60",
            "-realtime",
            "true",
            "-pix_fmt",
            "yuv420p",
            "-g",
            str(gop),
        ],
        "h264_amf": [
            "-c:v",
            "h264_amf",
            "-quality",
            "quality",
            "-rc",
            "vbr_peak",
            "-b:v",
            maxrate,
            "-maxrate",
            maxrate,
            "-bufsize",
            bufsize,
            "-g",
            str(gop),
            "-pix_fmt",
            "yuv420p",
        ],
        "h264_nvenc": [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p5",
            "-rc",
            "vbr",
            "-cq",
            str(crf),
            "-b:v",
            "0",
            "-maxrate",
            maxrate,
            "-bufsize",
            bufsize,
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "high",
            "-g",
            str(gop),
        ],
        "h264_qsv": [
            "-c:v",
            "h264_qsv",
            "-global_quality",
            str(crf),
            "-b:v",
            "0",
            "-maxrate",
            maxrate,
            "-bufsize",
            bufsize,
            "-profile:v",
            "high",
            "-g",
            str(gop),
        ],
    }

    ordered_hw_names: list[str] = []
    for enc_name in _platform_hw_encoder_order():
        if enc_name in encoder_candidates and enc_name not in ordered_hw_names:
            ordered_hw_names.append(enc_name)
    for enc_name in encoder_candidates:
        if enc_name not in ordered_hw_names:
            ordered_hw_names.append(enc_name)

    for enc_name in ordered_hw_names:
        enc_args = encoder_candidates[enc_name]
        if _encoder_args_supported(enc_args):
            return enc_name, enc_args

    return "libx264", [
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-preset",
        preset,
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-level:v",
        "4.1",
        "-g",
        str(gop),
        "-keyint_min",
        str(gop),
        "-sc_threshold",
        "0",
        "-maxrate",
        maxrate,
        "-bufsize",
        bufsize,
    ]


def detect_best_audio_encoder(audio_bitrate: str, audio_vbr: int) -> tuple[str, list[str]]:
    test_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:sample_rate=48000:duration=0.1",
        "-c:a",
        "libfdk_aac",
        "-vbr",
        str(audio_vbr),
        "-f",
        "null",
        "-",
    ]
    try:
        run_cmd(test_cmd, capture_output=True)
        return "libfdk_aac", [
            "-c:a",
            "libfdk_aac",
            "-vbr",
            str(audio_vbr),
            "-profile:a",
            "aac_low",
            "-ar",
            "48000",
            "-ac",
            "2",
        ]
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return "aac", [
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-profile:a",
        "aac_low",
        "-ar",
        "48000",
        "-ac",
        "2",
    ]


def detect_crop(path: Path, num_frames: int = 10) -> str | None:
    """Run cropdetect on the first N frames and return crop params like '1280:508:0:106', or None."""
    cmd = [
        "ffmpeg", "-hide_banner",
        "-i", str(path),
        "-vf", f"cropdetect=limit=24:round=2:skip=0",
        "-frames:v", str(num_frames),
        "-f", "null", "-",
    ]
    try:
        res = run_cmd(cmd, capture_output=True)
        # cropdetect output goes to stderr
        stderr = res.stderr or ""
        matches = [m.group(1) for m in (
            __import__("re").search(r"crop=(\d+:\d+:\d+:\d+)", line)
            for line in stderr.splitlines()
        ) if m]
        return matches[-1] if matches else None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    res = run_cmd(cmd, capture_output=True)
    raw = res.stdout.strip()
    if not raw:
        raise ValueError(f"Unable to read duration for {path}")
    duration = float(raw)
    if duration <= 0:
        raise ValueError(f"Non-positive duration for {path}: {duration}")
    return duration


def ensure_layout_dirs(audio_dir: Path, video_dir: Path, output_dir: Path) -> None:
    for d in (audio_dir, video_dir, output_dir):
        d.mkdir(parents=True, exist_ok=True)


def discover_audio(audio_dir: Path) -> Path:
    audio_files = sorted([p for p in audio_dir.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS])
    if not audio_files:
        raise RuntimeError(f"No master audio found in {audio_dir} (.wav/.flac/.mp3)")

    best_audio: Path | None = None
    best_duration = -1.0
    for p in audio_files:
        try:
            d = ffprobe_duration(p)
            if d > best_duration:
                best_duration = d
                best_audio = p
        except Exception:
            continue
    if best_audio is None:
        raise RuntimeError(f"No readable master audio found in {audio_dir}")
    return best_audio


def discover_videos(video_dir: Path) -> list[Path]:
    videos = [p for p in sorted(video_dir.iterdir()) if p.is_file() and p.suffix.lower() in VIDEO_EXTS]
    if not videos:
        raise RuntimeError(f"No video clips found in {video_dir} (.mp4/.mov)")
    if len(videos) < 2:
        raise RuntimeError("At least 2 video clips are required (anti-repeat)")
    return videos


def clip_group_key(path: Path) -> str:
    """Strip _v<N> variant suffix so foo.mp4 and foo_v2.mp4 share the same heat bucket."""
    import re as _re
    return _re.sub(r"_v\d+$", "", path.stem)


def choose_next_clip_weighted(
    pool: list[Clip],
    prev: Clip,
    heat: dict,
    rng: random.Random,
    heat_penalty: float,
    heat_decay: float,
) -> Clip:
    """Weighted random choice: recently used clips have lower probability via heat decay.

    Clips that are variants of the same scene (foo.mp4 / foo_v2.mp4) share a heat
    bucket and are also excluded from back-to-back selection together.
    """
    prev_group = clip_group_key(prev.path)
    choices = [c for c in pool if clip_group_key(c.path) != prev_group]
    if not choices:
        raise RuntimeError("No valid next clip available")
    weights = [1.0 / (1.0 + heat[clip_group_key(c.path)]) for c in choices]
    selected = rng.choices(choices, weights=weights, k=1)[0]
    # decay all heat, then penalize the selected clip's group
    for key in heat:
        heat[key] *= heat_decay
    heat[clip_group_key(selected.path)] += heat_penalty
    return selected


def safe_transition_duration(base_duration: float, prev_dur: float, next_dur: float, min_duration: float) -> float:
    d = min(base_duration, prev_dur * 0.45, next_dur * 0.45)
    return max(min_duration, d)


def build_timeline(
    clip_pool: list[Clip],
    target_duration: float,
    base_transition_duration: float,
    min_transition_duration: float,
    rng: random.Random,
    heat_penalty: float = 10.0,
    heat_decay: float = 0.0,
) -> tuple[list[Clip], list[Transition], float]:
    # Auto-scale decay: 0.0 means "choose automatically".
    # Target: heat retains ~20% of its value after all N group buckets have been
    # selected once, so recent clips are still penalised but old ones recover.
    group_keys = list({clip_group_key(c.path) for c in clip_pool})
    n_groups = max(1, len(group_keys))
    if heat_decay <= 0.0:
        heat_decay = 0.2 ** (1.0 / n_groups)  # e.g. N=27 → ≈0.941
    heat: dict = {k: 0.0 for k in group_keys}
    first = rng.choice(clip_pool)
    heat[clip_group_key(first.path)] = heat_penalty
    selected = [first]
    transitions: list[Transition] = []
    timeline_duration = first.duration
    prev = first

    while timeline_duration < target_duration:
        nxt = choose_next_clip_weighted(clip_pool, prev, heat, rng, heat_penalty, heat_decay)
        tr_dur = safe_transition_duration(base_transition_duration, prev.duration, nxt.duration, min_transition_duration)
        tr_name = rng.choice(TRANSITIONS)
        tr_offset = max(0.0, timeline_duration - tr_dur)

        transitions.append(Transition(name=tr_name, duration=tr_dur, offset=tr_offset))
        selected.append(nxt)
        timeline_duration = timeline_duration + nxt.duration - tr_dur
        prev = nxt

    return selected, transitions, timeline_duration


def fmt_sec(value: float) -> str:
    return f"{value:.3f}"


def build_filter_complex(
    num_clips: int,
    transitions: list[Transition],
    output_w: int,
    output_h: int,
    fps: int,
    duration: float,
    watermark_index: int | None = None,
    wm_width: int = 0,
    wm_margin: int = 20,
    wm_chroma_color: str = "0x00FF00",
    wm_chroma_similarity: float = 0.3,
    wm_chroma_blend: float = 0.05,
    wm_crop: str | None = None,
) -> str:
    if num_clips < 1:
        raise ValueError("num_clips must be >= 1")
    if len(transitions) != max(0, num_clips - 1):
        raise ValueError("transitions count mismatch")

    parts: list[str] = []
    for i in range(num_clips):
        parts.append(
            f"[{i}:v]settb=AVTB,"
            f"scale={output_w}:{output_h}:force_original_aspect_ratio=increase,"
            f"crop={output_w}:{output_h},"
            f"fps={fps},format=yuv420p[v{i}]"
        )

    current = "v0"
    for i in range(1, num_clips):
        tr = transitions[i - 1]
        out = f"vx{i}"
        parts.append(
            f"[{current}][v{i}]xfade="
            f"transition={tr.name}:duration={fmt_sec(tr.duration)}:offset={fmt_sec(tr.offset)}"
            f"[{out}]"
        )
        current = out

    if watermark_index is not None:
        effective_wm_w = wm_width if wm_width > 0 else output_w // 2
        parts.append(
            f"[{current}]trim=duration={fmt_sec(duration)},setpts=PTS-STARTPTS[vtrimmed]"
        )
        crop_filter = f"crop={wm_crop}," if wm_crop else ""
        parts.append(
            f"[{watermark_index}:v]"
            f"{crop_filter}"
            f"chromakey=color={wm_chroma_color}:similarity={wm_chroma_similarity}:blend={wm_chroma_blend},"
            f"scale={effective_wm_w}:-1,format=rgba[wm]"
        )
        parts.append(
            f"[vtrimmed][wm]overlay=(W-w)/2:{wm_margin},format=yuv420p[vout]"
        )
    else:
        parts.append(
            f"[{current}]trim=duration={fmt_sec(duration)},setpts=PTS-STARTPTS[vout]"
        )
    return ";".join(parts)


def build_ffmpeg_command(
    selected_clips: list[Clip],
    transitions: list[Transition],
    audio_path: Path,
    duration: float,
    output_path: Path,
    output_w: int,
    output_h: int,
    fps: int,
    encoder_args: list[str],
    audio_encoder_args: list[str],
    watermark_path: Path | None = None,
    wm_width: int = 0,
    wm_margin: int = 20,
    wm_chroma_color: str = "0x00FF00",
    wm_chroma_similarity: float = 0.3,
    wm_chroma_blend: float = 0.05,
    wm_crop: str | None = None,
) -> list[str]:
    cmd: list[str] = ["ffmpeg", "-hide_banner", "-y"]

    for clip in selected_clips:
        cmd.extend(["-i", str(clip.path)])

    audio_index = len(selected_clips)
    cmd.extend(["-i", str(audio_path)])

    watermark_index: int | None = None
    if watermark_path is not None:
        watermark_index = audio_index + 1
        cmd.extend(["-stream_loop", "-1", "-i", str(watermark_path)])

    filter_complex = build_filter_complex(
        num_clips=len(selected_clips),
        transitions=transitions,
        output_w=output_w,
        output_h=output_h,
        fps=fps,
        duration=duration,
        watermark_index=watermark_index,
        wm_width=wm_width,
        wm_margin=wm_margin,
        wm_chroma_color=wm_chroma_color,
        wm_chroma_similarity=wm_chroma_similarity,
        wm_chroma_blend=wm_chroma_blend,
        wm_crop=wm_crop,
    )

    cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            f"{audio_index}:a:0",
            *encoder_args,
            *audio_encoder_args,
            "-movflags",
            "+faststart",
            "-shortest",
            "-t",
            fmt_sec(duration),
            str(output_path),
        ]
    )
    return cmd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assemble random music video")
    parser.add_argument("--project", help="Project name (subfolder in input_automated and output_automated)")
    parser.add_argument("--workdir", default=None, help="Pipeline root directory (default: <input_root>/<project>)")
    parser.add_argument("--audio-dir", default=None, help="Audio dir (default: <workdir>/audio)")
    parser.add_argument("--video-dir", default=None, help="Video clips dir (default: <workdir>/video_clips)")
    parser.add_argument("--output-dir", default=None, help="Output dir (default: <output_root>/<project>)")
    parser.add_argument("--init-layout", action="store_true", help="Create default layout and exit")
    parser.add_argument("--output", default="output.mp4", help="Output filename")

    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--transition-duration", type=float, default=1.0, help="Base transition seconds")
    parser.add_argument("--min-transition-duration", type=float, default=0.25, help="Minimum transition seconds")
    parser.add_argument("--preview-seconds", type=float, default=0.0, help="Preview duration (0 = full)")

    parser.add_argument("--width", type=int, default=1080, help="Output width")
    parser.add_argument("--height", type=int, default=1920, help="Output height")
    parser.add_argument("--fps", type=int, default=30, help="Output fps")

    parser.add_argument("--crf", type=int, default=18, help="Quality parameter")
    parser.add_argument("--preset", default="slow", help="Preset for libx264 fallback")
    parser.add_argument("--maxrate", default="8M", help="Video max bitrate")
    parser.add_argument("--bufsize", default="16M", help="Video buffer size")

    parser.add_argument("--audio-bitrate", default="320k", help="Audio bitrate fallback")
    parser.add_argument("--audio-vbr", type=int, default=5, help="libfdk_aac VBR quality")

    parser.add_argument("--heat-penalty", type=float, default=10.0, help="Heat penalty added to a clip when selected (higher = stronger avoidance)")
    parser.add_argument("--heat-decay", type=float, default=0.0, help="Heat decay factor per clip selected (0-1, lower = longer cooldown; 0 = auto-scale based on pool size)")

    parser.add_argument("--watermark", default=None, help="Path to watermark video with green screen (default: auto-detect)")
    parser.add_argument("--no-watermark", action="store_true", help="Disable watermark even if auto-detected")
    parser.add_argument("--watermark-width", type=int, default=0, help="Watermark width in pixels (0 = 50%% of output width)")
    parser.add_argument("--watermark-margin", type=int, default=20, help="Watermark top margin in pixels")
    parser.add_argument("--watermark-chroma-color", default="0x00FF00", help="Chroma key color to remove (default: 0x00FF00 lime green)")
    parser.add_argument("--watermark-chroma-similarity", type=float, default=0.3, help="Chroma key similarity threshold (0-1)")
    parser.add_argument("--watermark-chroma-blend", type=float, default=0.05, help="Chroma key edge blend amount (0-1)")

    parser.add_argument("--dry-run", action="store_true", help="Print ffmpeg command only")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    input_root = DEFAULT_INPUT_ROOT
    output_root = DEFAULT_OUTPUT_ROOT

    if args.project:
        workdir = input_root / args.project
        output_dir = output_root / args.project
    else:
        workdir = input_root
        output_dir = output_root

    if args.workdir:
        workdir = Path(args.workdir).resolve()
    if args.output_dir:
        output_dir = Path(args.output_dir).resolve()

    workdir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    audio_dir = Path(args.audio_dir).resolve() if args.audio_dir else workdir / DEFAULT_AUDIO_DIRNAME
    video_dir = Path(args.video_dir).resolve() if args.video_dir else workdir / DEFAULT_VIDEO_DIRNAME

    ensure_layout_dirs(audio_dir=audio_dir, video_dir=video_dir, output_dir=output_dir)
    if args.init_layout:
        print(f"Created/verified layout under: {workdir}")
        print(f"  audio: {audio_dir}")
        print(f"  video_clips: {video_dir}")
        print(f"  output: {output_dir}")
        return 0

    output_path = output_dir / args.output
    rng = random.Random(args.seed)

    audio_path = discover_audio(audio_dir)
    audio_duration = ffprobe_duration(audio_path)

    video_paths = discover_videos(video_dir)
    clip_pool: list[Clip] = []
    skipped: list[str] = []
    for v in video_paths:
        try:
            clip_pool.append(Clip(path=v, duration=ffprobe_duration(v)))
        except Exception:
            skipped.append(v.name)

    if len(clip_pool) < 2:
        raise RuntimeError(
            f"Need at least 2 valid clips in {video_dir}. "
            f"Skipped unreadable: {', '.join(skipped) if skipped else 'none'}"
        )

    render_duration = audio_duration if args.preview_seconds <= 0 else min(audio_duration, args.preview_seconds)

    watermark_path: Path | None = None
    if not args.no_watermark:
        if args.watermark:
            watermark_path = Path(args.watermark).resolve()
            if not watermark_path.exists():
                raise RuntimeError(f"Watermark not found: {watermark_path}")
        elif (workdir / DEFAULT_WATERMARK_REL_PATH).exists():
            watermark_path = workdir / DEFAULT_WATERMARK_REL_PATH
            print(f"Auto-detected watermark: {watermark_path}")

    selected_clips, transitions, generated_duration = build_timeline(
        clip_pool=clip_pool,
        target_duration=render_duration,
        base_transition_duration=args.transition_duration,
        min_transition_duration=args.min_transition_duration,
        rng=rng,
        heat_penalty=args.heat_penalty,
        heat_decay=args.heat_decay,
    )

    print(f"Workdir: {workdir}")
    print(f"Audio dir: {audio_dir}")
    print(f"Video dir: {video_dir}")
    print(f"Output dir: {output_dir}")
    print(f"Master audio: {audio_path.name} ({fmt_sec(audio_duration)}s)")
    print(f"Render duration: {fmt_sec(render_duration)}s")
    print(f"Video pool size: {len(clip_pool)} valid / {len(video_paths)} found")
    if skipped:
        print(f"Skipped unreadable clips: {len(skipped)}")
    print(f"Selected clips: {len(selected_clips)}")
    print(f"Generated timeline before trim: {fmt_sec(generated_duration)}s")

    encoder_name, encoder_args = detect_best_encoder(
        crf=args.crf,
        preset=args.preset,
        fps=args.fps,
        maxrate=args.maxrate,
        bufsize=args.bufsize,
    )
    print(f"Selected video encoder: {encoder_name}")

    audio_encoder_name, audio_encoder_args = detect_best_audio_encoder(
        audio_bitrate=args.audio_bitrate,
        audio_vbr=args.audio_vbr,
    )
    print(f"Selected audio encoder: {audio_encoder_name}")

    wm_crop: str | None = None
    if watermark_path:
        wm_crop = detect_crop(watermark_path)
        print(f"Watermark: {watermark_path.name} (chroma key: {args.watermark_chroma_color})")
        if wm_crop:
            print(f"Watermark crop (black bars removed): {wm_crop}")

    cmd = build_ffmpeg_command(
        selected_clips=selected_clips,
        transitions=transitions,
        audio_path=audio_path,
        duration=render_duration,
        output_path=output_path,
        output_w=max(16, args.width),
        output_h=max(16, args.height),
        fps=max(1, args.fps),
        encoder_args=encoder_args,
        audio_encoder_args=audio_encoder_args,
        watermark_path=watermark_path,
        wm_width=args.watermark_width,
        wm_margin=args.watermark_margin,
        wm_chroma_color=args.watermark_chroma_color,
        wm_chroma_similarity=args.watermark_chroma_similarity,
        wm_chroma_blend=args.watermark_chroma_blend,
        wm_crop=wm_crop,
    )

    if args.dry_run:
        print("\nDry run command:")
        print(" ".join(cmd))
        return 0

    run_cmd(cmd)
    print(f"\nDone: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
