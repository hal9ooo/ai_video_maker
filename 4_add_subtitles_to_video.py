#!/usr/bin/env python3
"""
add_subtitles_to_video.py
─────────────────────────
Burns styled subtitles into a video using FFmpeg + ASS/SRT.

Requirements
────────────
• FFmpeg with libass support.
  On macOS (Homebrew) the standard `ffmpeg` formula does NOT include libass.
  Install the full build:
      brew install ffmpeg-full
  The script auto-detects /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg if present,
  otherwise falls back to the `ffmpeg` in PATH.

Features
────────
• Reads existing .srt / .ass / word-timing .json from input_automated/subtitles/
• Applies one of 4 visual style presets (or custom overrides)
• Automatically positions subtitles above the Instagram / TikTok / Reels UI bar
• Calculates safe bottom margin based on video resolution
• Handles text wrapping so long lines never overflow the frame
• Optional karaoke word-highlight mode (uses word-timing JSON)
• Outputs to output_automated/

Style presets
─────────────
  neon_night      Cyan glow on dark – electronic / night vibes  (default)
  warm_cinematic  Warm amber – cinematic / acoustic / indie
  bold_impact     White Impact with thick outline – viral / meme
  clean_white     Minimal clean white – works on any background

Usage examples
──────────────
  # Burn with default style (neon_night) using existing .ass
  python3 add_subtitles_to_video.py

  # Choose a style preset
  python3 add_subtitles_to_video.py --style warm_cinematic

  # Use SRT instead of ASS (style is re-generated from SRT)
  python3 add_subtitles_to_video.py --style clean_white --use-srt

  # Karaoke word-highlight mode (requires word-timing JSON)
  python3 add_subtitles_to_video.py --style neon_night --karaoke

  # Custom overrides
  python3 add_subtitles_to_video.py --style bold_impact \\
      --font-size 62 --primary-color "#FF4488" --margin-bottom 320

  # Target TikTok safe zone
  python3 add_subtitles_to_video.py --style neon_night --platform tiktok

  # Full custom paths
  python3 add_subtitles_to_video.py \\
      --video path/to/video.mp4 \\
      --srt   path/to/lyrics.srt \\
      --json  path/to/word_timing.json \\
      --output path/to/output.mp4 \\
      --style neon_night

  # List all styles
  python3 add_subtitles_to_video.py --list-styles

  # Dry run (generate ASS only, no encoding)
  python3 add_subtitles_to_video.py --style bold_impact --dry-run --keep-ass
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DEFAULT_INPUT_ROOT = BASE_DIR / "input_automated"
DEFAULT_OUTPUT_ROOT = BASE_DIR / "output_automated"
# These are subfolders within the project folder or root
DEFAULT_SUB_DIRNAME = "subtitles"

# ─────────────────────────────────────────────────────────────────────────────
# Social-media safe zones (fraction of video height from bottom)
# These are the UI bars that cover the bottom of the frame on each platform.
# We position subtitles ABOVE the tallest bar so they are never hidden.
# ─────────────────────────────────────────────────────────────────────────────
PLATFORM_SAFE_ZONES: dict[str, float] = {
    # fraction of total height reserved by the platform UI at the bottom.
    # Values are intentionally conservative to stay clear of like/comment/share
    # buttons, caption overlay, username, and music bar across all device sizes.
    "instagram_reels": 0.30,   # ~30 % – action bar + caption + username strip
    "tiktok":          0.28,   # ~28 % – action buttons + caption + music bar
    "youtube_shorts":  0.25,   # ~25 % – subscribe button + caption overlay
    "generic":         0.22,   # safe default for unknown vertical-video platforms
}

# Extra padding above the safe zone (pixels, at 1920-height reference)
SAFE_ZONE_EXTRA_PAD_REF = 60   # pixels at 1920 height


# ─────────────────────────────────────────────────────────────────────────────
# Style presets
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SubStyle:
    """Complete visual description of a subtitle style."""
    name: str
    font: str
    font_size: int          # at 1920-height reference
    primary_color: str      # hex RGB  e.g. "#FFD47A"
    outline_color: str      # hex RGB
    back_color: str         # hex RGB  (semi-transparent box behind text)
    back_alpha: int         # 0=opaque … 255=invisible
    bold: bool
    italic: bool
    outline_width: float
    shadow_depth: float
    letter_spacing: float   # ASS Spacing field
    # karaoke highlight color (word currently being sung)
    karaoke_color: str      # hex RGB
    # Gaussian blur radius applied to text edges (anti-aliasing against background).
    # 0 = off, 1.0 = subtle, 2.0 = soft, 3.0 = very soft. Mapped to ASS \blur tag.
    blur: float = 1.5
    # description shown in --list-styles
    description: str = ""


STYLE_PRESETS: dict[str, SubStyle] = {
    # ── 1. Neon Night ────────────────────────────────────────────────────────
    # Font: "Helvetica Neue" is available on macOS; "Bahnschrift SemiBold" on Windows.
    # libass will fall back gracefully if the font is not found.
    "neon_night": SubStyle(
        name="neon_night",
        font="Helvetica Neue",
        font_size=72,
        primary_color="#7DEBFF",
        outline_color="#041927",
        back_color="#020D14",
        back_alpha=160,
        bold=True,
        italic=False,
        outline_width=2.6,
        shadow_depth=1.8,
        letter_spacing=0.5,
        karaoke_color="#FFFFFF",
        description="Cyan glow on dark – perfect for electronic / night vibes",
    ),
    # ── 2. Warm Cinematic ────────────────────────────────────────────────────
    "warm_cinematic": SubStyle(
        name="warm_cinematic",
        font="Georgia",
        font_size=70,
        primary_color="#F6C374",
        outline_color="#1C140D",
        back_color="#0A0806",
        back_alpha=140,
        bold=True,
        italic=False,
        outline_width=2.2,
        shadow_depth=1.4,
        letter_spacing=0.3,
        karaoke_color="#FFFFFF",
        description="Warm amber on dark – cinematic / acoustic / indie feel",
    ),
    # ── 3. Bold Impact ───────────────────────────────────────────────────────
    "bold_impact": SubStyle(
        name="bold_impact",
        font="Impact",
        font_size=80,
        primary_color="#FFFFFF",
        outline_color="#000000",
        back_color="#000000",
        back_alpha=200,
        bold=False,   # Impact is already heavy
        italic=False,
        outline_width=3.2,
        shadow_depth=0.0,
        letter_spacing=1.0,
        karaoke_color="#FFD700",
        description="Classic white Impact with thick black outline – viral / meme energy",
    ),
    # ── 4. Clean White ───────────────────────────────────────────────────────
    "clean_white": SubStyle(
        name="clean_white",
        font="Arial",
        font_size=68,
        primary_color="#F6F7FA",
        outline_color="#121212",
        back_color="#000000",
        back_alpha=180,
        bold=False,
        italic=False,
        outline_width=2.0,
        shadow_depth=1.2,
        letter_spacing=0.0,
        karaoke_color="#FFD47A",
        description="Minimal clean white – works on any background",
    ),
}

DEFAULT_STYLE = "neon_night"
DEFAULT_PLATFORM = "instagram_reels"

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# FFmpeg binary detection
# ─────────────────────────────────────────────────────────────────────────────
# Prefer ffmpeg-full (has libass) over the standard Homebrew ffmpeg build.
_FFMPEG_FULL_PATH = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
_FFPROBE_FULL_PATH = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")


def _find_ffmpeg() -> str:
    """Return the path to an FFmpeg binary that supports libass (ass filter)."""
    if _FFMPEG_FULL_PATH.exists():
        return str(_FFMPEG_FULL_PATH)
    return "ffmpeg"


def _find_ffprobe() -> str:
    """Return the path to the matching ffprobe binary."""
    if _FFPROBE_FULL_PATH.exists():
        return str(_FFPROBE_FULL_PATH)
    return "ffprobe"


FFMPEG = _find_ffmpeg()
FFPROBE = _find_ffprobe()


def _encoder_args_supported(args: list[str]) -> bool:
    """Return True if a set of FFmpeg encoder args is usable on this system."""
    test_cmd = [
        FFMPEG, "-hide_banner",
        "-f", "lavfi", "-i", "testsrc2=size=128x128:rate=30",
        "-frames:v", "1",
        *args,
        "-an", "-f", "null", "-",
    ]
    try:
        subprocess.run(test_cmd, check=True, capture_output=True, text=True)
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


def _video_encoder_args(name: str, crf: int, preset: str) -> list[str]:
    if name == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-q:v", "65", "-pix_fmt", "yuv420p"]
    if name == "h264_nvenc":
        return [
            "-c:v", "h264_nvenc",
            "-preset", "p5",
            "-rc", "vbr",
            "-cq", str(crf),
            "-b:v", "0",
            "-pix_fmt", "yuv420p",
        ]
    if name == "h264_amf":
        return [
            "-c:v", "h264_amf",
            "-quality", "quality",
            "-rc", "vbr_peak",
            "-b:v", "6M",
            "-maxrate", "10M",
            "-bufsize", "12M",
            "-pix_fmt", "yuv420p",
        ]
    if name == "h264_qsv":
        return [
            "-c:v", "h264_qsv",
            "-global_quality", str(crf),
        ]
    if name == "libx264":
        return ["-c:v", "libx264", "-crf", str(crf), "-preset", preset]
    raise ValueError(f"Unsupported encoder: {name}")


def _select_video_encoder(
    crf: int,
    preset: str,
    hwaccel: bool | None,
) -> tuple[str, list[str], str]:
    """
    Resolve encoder selection.
    hwaccel=None  -> auto-detect best platform hardware encoder, fallback libx264.
    hwaccel=True  -> require hardware encoder (raise if unavailable).
    hwaccel=False -> force libx264.
    """
    if hwaccel is False:
        args = _video_encoder_args("libx264", crf, preset)
        return "libx264", args, "libx264 (forced)"

    for encoder_name in _platform_hw_encoder_order():
        args = _video_encoder_args(encoder_name, crf, preset)
        if _encoder_args_supported(args):
            mode = "forced" if hwaccel is True else "auto"
            return encoder_name, args, f"{encoder_name} ({mode})"

    if hwaccel is True:
        raise RuntimeError(
            "No supported hardware H.264 encoder found (tried: "
            + ", ".join(_platform_hw_encoder_order())
            + ")."
        )

    args = _video_encoder_args("libx264", crf, preset)
    return "libx264", args, "libx264 (auto)"


def run(cmd: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=True,
        text=True,
        capture_output=capture,
        encoding="utf-8",
        errors="replace",
    )


def ffprobe_video_info(path: Path) -> tuple[int, int, float]:
    """Return (width, height, duration_seconds)."""
    cmd = [
        FFPROBE, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ]
    res = run(cmd, capture=True)
    data = json.loads(res.stdout)
    streams = data.get("streams", [{}])
    fmt = data.get("format", {})
    w = int(streams[0].get("width", 1080))
    h = int(streams[0].get("height", 1920))
    dur = float(fmt.get("duration", 0.0))
    return w, h, dur


def hex_to_ass_color(hex_rgb: str, alpha: int = 0) -> str:
    """Convert #RRGGBB + alpha (0=opaque, 255=transparent) to ASS &HAABBGGRR."""
    text = hex_rgb.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"Invalid hex color: {hex_rgb!r}")
    rr, gg, bb = text[0:2], text[2:4], text[4:6]
    aa = f"{max(0, min(255, alpha)):02X}"
    return f"&H{aa}{bb}{gg}{rr}"


def escape_ass(text: str) -> str:
    return (
        text.replace("\\", r"\\")
            .replace("{", r"\{")
            .replace("}", r"\}")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\n", r"\N")
    )


def srt_time_to_seconds(ts: str) -> float:
    """'00:01:23,456' → 83.456"""
    ts = ts.replace(",", ".")
    h, m, rest = ts.split(":")
    s, ms = rest.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def seconds_to_ass_time(sec: float) -> str:
    """83.456 → '0:01:23.46'"""
    sec = max(0.0, sec)
    total_cs = int(round(sec * 100.0))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# ─────────────────────────────────────────────────────────────────────────────
# SRT parser
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class SrtEntry:
    index: int
    start: float
    end: float
    text: str   # raw multi-line text


def parse_srt(path: Path, offset: float = 0.0) -> list[SrtEntry]:
    """Parse an SRT file, optionally shifting all timestamps by `offset` seconds."""
    content = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n{2,}", content.strip())
    entries: list[SrtEntry] = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        m = re.match(
            r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})",
            lines[1].strip(),
        )
        if not m:
            continue
        start = max(0.0, srt_time_to_seconds(m.group(1)) + offset)
        end = max(start + 0.1, srt_time_to_seconds(m.group(2)) + offset)
        text = " ".join(lines[2:]).strip()
        entries.append(SrtEntry(index=idx, start=start, end=end, text=text))
    return entries


# ─────────────────────────────────────────────────────────────────────────────
# Word-timing JSON parser
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WordTiming:
    word: str
    start: float   # seconds
    end: float     # seconds


def parse_word_timing_json(path: Path, offset: float = 0.0) -> list[WordTiming]:
    """
    Parse a word-timing JSON file. Supports multiple formats:
      - Flat list:      [{"word": "...", "start": 0.5, "end": 0.8}, ...]
      - Top-level key:  {"words": [...]}
      - Whisper-style:  {"segments": [{"words": [...]}]}
    The `offset` (seconds) is added to every timestamp (same as SRT offset).
    """
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))

    raw: list[dict] = []
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        if "words" in data:
            raw = data["words"]
        elif "segments" in data:
            for seg in data["segments"]:
                raw.extend(seg.get("words", []))

    result: list[WordTiming] = []
    for item in raw:
        word = str(item.get("word", item.get("text", ""))).strip()
        start = max(0.0, float(item.get("start", 0.0)) + offset)
        end = float(item.get("end", start + 0.1)) + offset
        end = max(start + 0.05, end)
        if word:
            result.append(WordTiming(word=word, start=start, end=end))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Text wrapping
# ─────────────────────────────────────────────────────────────────────────────

def wrap_subtitle_text(
    text: str,
    video_width: int,
    font_size: int,
    margin_lr: int,
    max_lines: int = 2,
) -> list[str]:
    """
    Wrap subtitle text to fit within the usable video width.
    Returns a list of wrapped lines (max `max_lines`).
    Uses a character-width heuristic based on font_size.
    """
    # Approximate character width: ~0.55× font_size for proportional fonts
    approx_char_w = max(10.0, font_size * 0.55)
    usable_w = max(200, video_width - margin_lr * 2)
    max_chars = max(12, int(usable_w / approx_char_w))

    # First try: use textwrap
    wrapped = textwrap.wrap(text, width=max_chars)
    if not wrapped:
        return [text]

    if len(wrapped) <= max_lines:
        return wrapped

    # Too many lines: merge tail into last allowed line
    head = wrapped[: max_lines - 1]
    tail_words = " ".join(wrapped[max_lines - 1 :])
    return [*head, tail_words]


# ─────────────────────────────────────────────────────────────────────────────
# Safe-zone margin calculation
# ─────────────────────────────────────────────────────────────────────────────

def compute_safe_margin_v(
    video_height: int,
    platform: str,
    extra_pad: int = 0,
) -> int:
    """
    Return the ASS MarginV (pixels from bottom) that keeps subtitles
    above the platform UI bar.
    """
    zone_frac = PLATFORM_SAFE_ZONES.get(platform, PLATFORM_SAFE_ZONES["generic"])
    # Scale the reference extra-pad to the actual video height
    scaled_pad = int(SAFE_ZONE_EXTRA_PAD_REF * video_height / 1920) + extra_pad
    margin = int(video_height * zone_frac) + scaled_pad
    return margin


# ─────────────────────────────────────────────────────────────────────────────
# ASS file generation
# ─────────────────────────────────────────────────────────────────────────────

def build_ass_from_srt(
    entries: list[SrtEntry],
    style: SubStyle,
    video_width: int,
    video_height: int,
    margin_v: int,
    margin_lr: int = 80,
) -> str:
    """Generate a complete ASS subtitle file string from SRT entries."""
    primary = hex_to_ass_color(style.primary_color, alpha=0)
    outline = hex_to_ass_color(style.outline_color, alpha=0)
    back = hex_to_ass_color(style.back_color, alpha=style.back_alpha)
    bold_flag = -1 if style.bold else 0
    italic_flag = -1 if style.italic else 0

    # Scale font size to actual video height (preset is calibrated for 1920)
    scaled_font_size = max(20, int(style.font_size * video_height / 1920))

    lines: list[str] = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {video_width}",
        f"PlayResY: {video_height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        (
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
            "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,"
            "ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
            "Alignment,MarginL,MarginR,MarginV,Encoding"
        ),
        (
            f"Style: Default,{style.font},{scaled_font_size},"
            f"{primary},{primary},{outline},{back},"
            f"{bold_flag},{italic_flag},0,0,"
            f"100,100,{style.letter_spacing:.1f},0,"
            f"1,{style.outline_width:.1f},{style.shadow_depth:.1f},"
            f"2,{margin_lr},{margin_lr},{margin_v},1"
        ),
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]

    blur_tag = f"{{\\blur{style.blur:.1f}}}" if style.blur > 0 else ""

    for entry in entries:
        wrapped = wrap_subtitle_text(
            entry.text,
            video_width=video_width,
            font_size=scaled_font_size,
            margin_lr=margin_lr,
        )
        ass_text = r"\N".join(escape_ass(ln) for ln in wrapped)
        t_start = seconds_to_ass_time(entry.start)
        t_end = seconds_to_ass_time(entry.end)
        lines.append(
            f"Dialogue: 0,{t_start},{t_end},Default,,0,0,0,,{blur_tag}{ass_text}"
        )

    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# Karaoke ASS generation (word-by-word highlight)
# ─────────────────────────────────────────────────────────────────────────────

def group_words_by_srt(
    words: list[WordTiming],
    srt_entries: list[SrtEntry],
) -> list[tuple[SrtEntry, list[WordTiming]]]:
    """
    Assign each word to the SRT entry whose time window best covers it,
    then sort words within each group by start time.
    Words that fall exactly inside an entry's window are preferred; ties are
    broken by minimum distance from the entry boundaries.
    """
    groups: dict[int, list[WordTiming]] = {i: [] for i in range(len(srt_entries))}

    for word in words:
        mid = (word.start + word.end) / 2.0
        best_idx = 0
        best_dist = float("inf")
        for i, entry in enumerate(srt_entries):
            if entry.start <= mid <= entry.end:
                best_idx = i
                best_dist = 0.0
                break
            dist = min(abs(mid - entry.start), abs(mid - entry.end))
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        groups[best_idx].append(word)

    return [
        (srt_entries[i], sorted(groups[i], key=lambda w: w.start))
        for i in range(len(srt_entries))
    ]


def build_ass_karaoke(
    groups: list[tuple[SrtEntry, list[WordTiming]]],
    style: SubStyle,
    video_width: int,
    video_height: int,
    margin_v: int,
    margin_lr: int = 80,
) -> str:
    """
    Generate an ASS subtitle file with karaoke word-highlight timing.

    Uses the ASS \\kf (karaoke fill) tag for a smooth left-to-right sweep effect.

    Color semantics:
      PrimaryColour   = karaoke_color  – bright highlight, applied to sung/past words
      SecondaryColour = primary_color  – normal style color, applied to upcoming words

    The \\kf sweep transitions each word from Secondary → Primary as its time arrives,
    so future words show in the normal subtitle color and light up as the music plays.

    Lines without word timing (empty groups) fall back to plain ASS text.
    """
    primary_ass   = hex_to_ass_color(style.karaoke_color,  alpha=0)   # sung / active
    secondary_ass = hex_to_ass_color(style.primary_color,  alpha=0)   # upcoming
    outline_ass   = hex_to_ass_color(style.outline_color,  alpha=0)
    back_ass      = hex_to_ass_color(style.back_color,     alpha=style.back_alpha)
    bold_flag   = -1 if style.bold   else 0
    italic_flag = -1 if style.italic else 0
    scaled_font_size = max(20, int(style.font_size * video_height / 1920))

    lines: list[str] = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {video_width}",
        f"PlayResY: {video_height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        (
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
            "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,"
            "ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
            "Alignment,MarginL,MarginR,MarginV,Encoding"
        ),
        (
            f"Style: Default,{style.font},{scaled_font_size},"
            f"{primary_ass},{secondary_ass},{outline_ass},{back_ass},"
            f"{bold_flag},{italic_flag},0,0,"
            f"100,100,{style.letter_spacing:.1f},0,"
            f"1,{style.outline_width:.1f},{style.shadow_depth:.1f},"
            f"2,{margin_lr},{margin_lr},{margin_v},1"
        ),
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]

    blur_tag = f"{{\\blur{style.blur:.1f}}}" if style.blur > 0 else ""

    for entry, words in groups:
        t_start = seconds_to_ass_time(entry.start)
        t_end   = seconds_to_ass_time(entry.end)

        if not words:
            # No word timing for this line – fall back to plain text
            wrapped = wrap_subtitle_text(
                entry.text,
                video_width=video_width,
                font_size=scaled_font_size,
                margin_lr=margin_lr,
            )
            ass_text = r"\N".join(escape_ass(ln) for ln in wrapped)
            lines.append(f"Dialogue: 0,{t_start},{t_end},Default,,0,0,0,,{blur_tag}{ass_text}")
            continue

        # Build \kf tags.
        # Each tag's centisecond count spans from the previous word's end
        # (or the line's start) to the current word's end, so the fill sweep
        # completes exactly when the word finishes in the audio.
        parts: list[str] = []
        cursor = entry.start
        for word in words:
            dur_cs = max(1, int(round((word.end - cursor) * 100)))
            parts.append(f"{{\\kf{dur_cs}}}{escape_ass(word.word.strip())}")
            cursor = word.end

        ass_text = " ".join(parts)
        lines.append(f"Dialogue: 0,{t_start},{t_end},Default,,0,0,0,,{blur_tag}{ass_text}")

    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# FFmpeg burn
# ─────────────────────────────────────────────────────────────────────────────

def _ass_filter_path(p: Path) -> str:
    """
    Escape a path for use as the value of the FFmpeg `ass=` filter option.
    FFmpeg's lavfi filter-graph parser uses ':' as option separator and
    '\\' as escape character. We quote the final path and escape ':' so
    Windows drive letters (e.g. C:) are parsed correctly.
    """
    s = str(p).replace("\\", "/")
    # Inside single quotes, escaping ':' is enough for Windows drive letters.
    s = s.replace(":", "\\:")
    s = s.replace("'", "\\'")
    return f"'{s}'"


def burn_subtitles(
    video_path: Path,
    ass_path: Path,
    output_path: Path,
    crf: int = 18,
    preset: str = "slow",
    audio_copy: bool = True,
    hwaccel: bool | None = None,
) -> None:
    """
    Burn ASS subtitles into video using FFmpeg (requires libass build).
    hwaccel=None  → auto-detect best hardware encoder by platform, fallback libx264.
    hwaccel=True  → require hardware encoder (fail if none is available).
    hwaccel=False → force libx264.
    """
    import shutil as _shutil

    output_path.parent.mkdir(parents=True, exist_ok=True)

    _, encoder_args, encoder_label = _select_video_encoder(
        crf=crf,
        preset=preset,
        hwaccel=hwaccel,
    )
    print(f"   Encoder:  {encoder_label}")

    # Copy ASS to a temporary file with a simple name to avoid path-escaping issues
    with tempfile.NamedTemporaryFile(prefix="ffmpeg_subs_", suffix=".ass", delete=False) as tmp_ass_file:
        safe_ass = Path(tmp_ass_file.name)
    _shutil.copy2(str(ass_path), str(safe_ass))

    audio_codec = ["-c:a", "copy"] if audio_copy else ["-c:a", "aac", "-b:a", "192k"]
    ass_filter = f"ass={_ass_filter_path(safe_ass)}"

    cmd = [
        FFMPEG,
        "-hide_banner",
        "-loglevel", "info",
        "-y",
        "-i", str(video_path),
        "-vf", ass_filter,
    ]

    cmd.extend(encoder_args)

    cmd.extend([*audio_codec, str(output_path)])

    print(f"\n▶ Running FFmpeg ({FFMPEG})…")
    print("  " + " ".join(cmd))

    try:
        run(cmd)
    finally:
        safe_ass.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# File discovery helpers
# ─────────────────────────────────────────────────────────────────────────────

def find_file(directory: Path, suffixes: list[str], prefer_name: str = "") -> Optional[Path]:
    """Find the first file in `directory` matching one of the given suffixes."""
    candidates = sorted(
        [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in suffixes]
    )
    if not candidates:
        return None
    if prefer_name:
        preferred = [p for p in candidates if prefer_name.lower() in p.name.lower()]
        if preferred:
            return preferred[0]
    return candidates[0]


def find_video(directory: Path) -> Optional[Path]:
    return find_file(directory, [".mp4", ".mov", ".mkv", ".avi", ".webm"])


def find_srt(directory: Path) -> Optional[Path]:
    return find_file(directory, [".srt"], prefer_name="lyrics")


def find_ass(directory: Path) -> Optional[Path]:
    return find_file(directory, [".ass"], prefer_name="lyrics")


def find_json(directory: Path) -> Optional[Path]:
    return find_file(directory, [".json"], prefer_name="word_timing")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Burn styled subtitles into a video (Instagram/TikTok safe zones)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Style presets
            ─────────────
            neon_night      Cyan glow on dark – electronic / night vibes
            warm_cinematic  Warm amber – cinematic / acoustic / indie
            bold_impact     White Impact with thick outline – viral / meme
            clean_white     Minimal clean white – works on any background

            Platform safe zones (--platform)
            ─────────────────────────────────
            instagram_reels  (default) – 22 % bottom reserved
            tiktok           – 20 % bottom reserved
            youtube_shorts   – 18 % bottom reserved
            generic          – 15 % bottom reserved
        """),
    )

    # ── Input / output ────────────────────────────────────────────────────────
    parser.add_argument(
        "--project", help="Project name (subfolder in input_automated and output_automated)"
    )
    parser.add_argument(
        "--video", default=None,
        help="Input video file (default: auto-detect in <output_root>/<project>/)",
    )
    parser.add_argument(
        "--srt", default=None,
        help="SRT subtitle file (default: auto-detect in <input_root>/<project>/)",
    )
    parser.add_argument(
        "--ass", default=None,
        help="Pre-built ASS file to use directly (skips style generation)",
    )
    parser.add_argument(
        "--json", default=None,
        help="Word-timing JSON file for karaoke mode (default: auto-detect in <input_root>/<project>/)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output video path (default: <output_root>/<project>/output_subtitled.mp4)",
    )

    # ── Style ─────────────────────────────────────────────────────────────────
    parser.add_argument(
        "--style",
        choices=list(STYLE_PRESETS.keys()),
        default=DEFAULT_STYLE,
        help=f"Visual style preset (default: {DEFAULT_STYLE})",
    )
    parser.add_argument(
        "--list-styles", action="store_true",
        help="Print available style presets and exit",
    )
    parser.add_argument(
        "--use-srt", action="store_true",
        help="Force re-generation of ASS from SRT even if an .ass file exists",
    )
    parser.add_argument(
        "--karaoke", action="store_true",
        help=(
            "Enable karaoke word-highlight mode: words light up one by one "
            "with a fill-sweep effect as the music plays. "
            "Requires a word-timing JSON (--json) and an SRT file."
        ),
    )

    # ── Style overrides ───────────────────────────────────────────────────────
    parser.add_argument("--font", default=None, help="Override font name")
    parser.add_argument("--font-size", type=int, default=None, help="Override font size (at 1920 height)")
    parser.add_argument("--primary-color", default=None, help="Override primary text color (#RRGGBB)")
    parser.add_argument("--outline-color", default=None, help="Override outline color (#RRGGBB)")
    parser.add_argument("--bold", action="store_true", default=None, help="Force bold")
    parser.add_argument("--no-bold", dest="bold", action="store_false", help="Force no bold")
    parser.add_argument("--italic", action="store_true", default=None, help="Force italic")
    parser.add_argument("--outline-width", type=float, default=None, help="Override outline width")
    parser.add_argument("--shadow-depth", type=float, default=None, help="Override shadow depth")
    parser.add_argument(
        "--blur", type=float, default=None,
        help="Gaussian blur radius for text edge anti-aliasing (default: 1.5; 0 = off)",
    )

    # ── Positioning ───────────────────────────────────────────────────────────
    parser.add_argument(
        "--platform",
        choices=list(PLATFORM_SAFE_ZONES.keys()),
        default=DEFAULT_PLATFORM,
        help=f"Target platform for safe-zone calculation (default: {DEFAULT_PLATFORM})",
    )
    parser.add_argument(
        "--margin-bottom", type=int, default=None,
        help="Override bottom margin in pixels (overrides --platform safe-zone)",
    )
    parser.add_argument(
        "--margin-lr", type=int, default=80,
        help="Left/right margin in pixels (default: 80)",
    )
    parser.add_argument(
        "--extra-pad", type=int, default=0,
        help="Extra padding above the platform safe zone in pixels (default: 0)",
    )

    # ── Timing ────────────────────────────────────────────────────────────────
    parser.add_argument(
        "--subtitle-offset", type=float, default=0.0,
        help=(
            "Shift all subtitle timestamps by this many seconds "
            "(positive = later, negative = earlier). "
            "Use this when subtitles are out of sync with the video. "
            "Example: --subtitle-offset 60.5"
        ),
    )
    parser.add_argument(
        "--audio", default=None,
        help=(
            "Path to the original full audio file (used with --auto-offset to "
            "detect the vocal start time automatically)"
        ),
    )
    parser.add_argument(
        "--auto-offset", action="store_true",
        help=(
            "Automatically compute the subtitle offset by comparing the video audio "
            "silence pattern with the vocal stem silence pattern. "
            "Requires --audio (full mix) and the vocal stem to be auto-detected."
        ),
    )

    # ── Encoding ──────────────────────────────────────────────────────────────
    parser.add_argument("--crf", type=int, default=18, help="FFmpeg CRF quality (default: 18)")
    parser.add_argument(
        "--preset",
        choices=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"],
        default="slow",
        help="FFmpeg x264 preset (default: slow)",
    )
    parser.add_argument(
        "--no-audio-copy", action="store_true",
        help="Re-encode audio to AAC instead of copying stream",
    )
    parser.add_argument(
        "--hwaccel", dest="hwaccel", action="store_const", const=True, default=None,
        help="Require hardware H.264 encoding (auto-select by platform: nvenc/amf/qsv/videotoolbox).",
    )
    parser.add_argument(
        "--no-hwaccel", dest="hwaccel", action="store_const", const=False,
        help="Force libx264 (disable hardware encoder auto-detection).",
    )

    # ── Debug ─────────────────────────────────────────────────────────────────
    parser.add_argument(
        "--keep-ass", action="store_true",
        help="Keep the generated .ass file after encoding",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Generate the .ass file but do not run FFmpeg",
    )

    return parser.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def detect_first_audio_activity(audio_path: Path, noise_db: float = -40.0, min_duration: float = 0.3) -> float:
    """
    Use FFmpeg silencedetect to find when audio first becomes active.
    Returns the time (seconds) of the first non-silent moment.
    """
    cmd = [
        FFMPEG, "-i", str(audio_path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null", "-",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    output = res.stderr + res.stdout
    # Find first silence_end (= first time audio becomes active after initial silence)
    m = re.search(r"silence_end:\s*([\d.]+)", output)
    if m:
        return float(m.group(1))
    return 0.0


def compute_subtitle_offset(video_path: Path, vocal_stem_path: Optional[Path]) -> float:
    """
    Compute the time offset to apply to subtitles so they sync with the video.

    The subtitles were generated from the vocal stem audio.
    The vocal stem may have a different silence pattern than the video.

    Logic:
      - SRT timings are relative to the vocal stem timeline.
      - The vocal stem has N seconds of silence before the first vocal.
      - The video has M seconds before the same vocal.
      - To convert SRT time → video time: add (stem_vocal_start - video_vocal_start).
        e.g. stem starts at 14.8s, video starts at 0.66s → offset = +14.14s
             meaning: shift all subtitles forward by 14.14s.
    """
    # When vocals start in the video (full mix)
    video_vocal_start = detect_first_audio_activity(video_path)
    print(f"   Video audio starts at: {video_vocal_start:.3f}s")

    if vocal_stem_path is not None and vocal_stem_path.exists():
        # When vocals start in the vocal stem (used to generate subtitles)
        stem_vocal_start = detect_first_audio_activity(vocal_stem_path)
        print(f"   Vocal stem starts at:  {stem_vocal_start:.3f}s")
        # offset = stem_vocal_start - video_vocal_start
        # This shifts SRT timings so they align with the video timeline.
        offset = stem_vocal_start - video_vocal_start
    else:
        # No stem available: assume SRT timings start at 0 and video starts at video_vocal_start
        offset = video_vocal_start

    return offset


def check_ffmpeg_libass() -> bool:
    """Return True if the detected FFmpeg binary has the 'ass' filter (libass)."""
    try:
        res = subprocess.run(
            [FFMPEG, "-filters"],
            capture_output=True, text=True, check=False,
        )
        return " ass " in res.stdout
    except FileNotFoundError:
        return False


def main() -> int:
    args = parse_args()

    input_root = BASE_DIR / "input_automated"
    output_root = BASE_DIR / "output_automated"

    if args.project:
        current_input_dir = input_root / args.project
        current_output_dir = output_root / args.project
    else:
        current_input_dir = input_root
        current_output_dir = output_root

    # Ensure project directories exist
    current_input_dir.mkdir(parents=True, exist_ok=True)
    current_output_dir.mkdir(parents=True, exist_ok=True)

    # ── Check FFmpeg libass support ───────────────────────────────────────────
    if not args.dry_run:
        if not check_ffmpeg_libass():
            print(
                f"ERROR: FFmpeg at '{FFMPEG}' does not have the 'ass' filter (libass).\n"
                "  On macOS install the full build:  brew install ffmpeg-full\n"
                "  Then re-run this script.",
                file=sys.stderr,
            )
            return 1
        print(f"ℹ️  FFmpeg: {FFMPEG}  (libass ✓)")

    # ── Resolve input video ───────────────────────────────────────────────────
    if args.video:
        video_path = Path(args.video).resolve()
    else:
        # Default video path is in the output directory, named output.mp4
        default_video_path = current_output_dir / "output.mp4"
        if default_video_path.exists():
            video_path = default_video_path
        else:
            # Fallback to auto-detecting any video in the output directory
            video_path = find_video(current_output_dir)
            if video_path is None:
                print(f"ERROR: No video found in {current_output_dir}. Use --video.", file=sys.stderr)
                return 1

    if not video_path.exists():
        print(f"ERROR: Video not found: {video_path}", file=sys.stderr)
        return 1

    # ── Resolve subtitle source ───────────────────────────────────────────────
    ass_path_arg: Optional[Path] = Path(args.ass).resolve() if args.ass else None
    srt_path_arg: Optional[Path] = Path(args.srt).resolve() if args.srt else None

    # Auto-discovery subfolder
    subtitles_dir = current_input_dir / "subtitles"
    discovery_dir = subtitles_dir if subtitles_dir.exists() else current_input_dir

    # Auto-discover if not provided
    if ass_path_arg is None and not args.use_srt:
        ass_path_arg = find_ass(discovery_dir)
    if srt_path_arg is None:
        srt_path_arg = find_srt(discovery_dir)

    # ── Output path ───────────────────────────────────────────────────────────
    if args.output:
        output_path = Path(args.output).resolve()
    else:
        stem = video_path.stem
        output_path = current_output_dir / f"{stem}_subtitled.mp4"

    # ── Probe video ───────────────────────────────────────────────────────────
    print(f"\n📹 Video:    {video_path}")
    vid_w, vid_h, vid_dur = ffprobe_video_info(video_path)
    print(f"   Size:     {vid_w}×{vid_h}  Duration: {vid_dur:.1f}s")

    # ── Compute safe margin ───────────────────────────────────────────────────
    if args.margin_bottom is not None:
        margin_v = args.margin_bottom
        print(f"   Margin V: {margin_v}px (manual override)")
    else:
        margin_v = compute_safe_margin_v(vid_h, args.platform, extra_pad=args.extra_pad)
        zone_pct = PLATFORM_SAFE_ZONES.get(args.platform, 0.15) * 100
        print(f"   Margin V: {margin_v}px  (platform={args.platform}, safe-zone={zone_pct:.0f}%)")

    # ── Build / select style ──────────────────────────────────────────────────
    style = STYLE_PRESETS[args.style]
    # Apply overrides
    overrides: dict = {}
    if args.font:
        overrides["font"] = args.font
    if args.font_size:
        overrides["font_size"] = args.font_size
    if args.primary_color:
        overrides["primary_color"] = args.primary_color
    if args.outline_color:
        overrides["outline_color"] = args.outline_color
    if args.bold is not None:
        overrides["bold"] = args.bold
    if args.italic is not None:
        overrides["italic"] = args.italic
    if args.outline_width is not None:
        overrides["outline_width"] = args.outline_width
    if args.shadow_depth is not None:
        overrides["shadow_depth"] = args.shadow_depth
    if args.blur is not None:
        overrides["blur"] = args.blur
    if overrides:
        style = replace(style, **overrides)

    print(f"\n🎨 Style:    {style.name}")
    print(f"   Font:     {style.font}  {style.font_size}pt")
    print(f"   Color:    {style.primary_color}  outline={style.outline_color}")
    print(f"   Bold:     {style.bold}  Italic: {style.italic}")

    # ── Compute subtitle timing offset ───────────────────────────────────────
    subtitle_offset = args.subtitle_offset
    if args.auto_offset:
        # Find the vocal stem for offset detection
        vocal_stem_path: Optional[Path] = None
        stem_dir = current_input_dir / "vocal_stems"
        if stem_dir.exists():
            for ext in (".wav", ".mp3"):
                candidates = list(stem_dir.glob(f"*{ext}"))
                if candidates:
                    vocal_stem_path = candidates[0]
                    break
        print(f"\n🔍 Auto-detecting subtitle offset…")
        auto_off = compute_subtitle_offset(video_path, vocal_stem_path)
        subtitle_offset += auto_off
        print(f"   Auto offset: {auto_off:+.3f}s  →  total offset: {subtitle_offset:+.3f}s")
    elif subtitle_offset != 0.0:
        print(f"\n⏱  Subtitle offset: {subtitle_offset:+.3f}s")

    # ── Decide which ASS to use ───────────────────────────────────────────────
    # Karaoke mode always requires regeneration (word-level timing).
    use_existing_ass = (
        not args.karaoke
        and ass_path_arg is not None
        and ass_path_arg.exists()
        and not args.use_srt
        and not overrides
        and args.margin_bottom is None
        and subtitle_offset == 0.0   # offset requires re-generation from SRT
    )

    tmp_ass: Optional[Path] = None  # will be cleaned up unless --keep-ass

    if use_existing_ass:
        final_ass_path = ass_path_arg
        print(f"\n📄 Using existing ASS: {final_ass_path}")
    else:
        # Generate ASS content – karaoke mode or normal SRT mode
        if args.karaoke:
            # ── Karaoke word-highlight mode ───────────────────────────────────
            json_path = Path(args.json).resolve() if args.json else find_json(discovery_dir)
            if json_path is None or not json_path.exists():
                print(
                    "ERROR: Karaoke mode requires a word-timing JSON file.\n"
                    f"  Use --json <path> or place a .json in {discovery_dir}/",
                    file=sys.stderr,
                )
                return 1
            if srt_path_arg is None or not srt_path_arg.exists():
                print(
                    "ERROR: Karaoke mode also requires an SRT file for line grouping.\n"
                    f"  Use --srt <path> or place a .srt in {discovery_dir}/",
                    file=sys.stderr,
                )
                return 1

            print(f"\n🎤 Karaoke:  {json_path}")
            word_timings = parse_word_timing_json(json_path, offset=subtitle_offset)
            print(f"   Words:    {len(word_timings)}")

            print(f"\n📄 SRT:      {srt_path_arg}")
            srt_entries_k = parse_srt(srt_path_arg, offset=subtitle_offset)
            print(f"   Entries:  {len(srt_entries_k)}")

            groups = group_words_by_srt(word_timings, srt_entries_k)
            ass_content = build_ass_karaoke(
                groups=groups,
                style=style,
                video_width=vid_w,
                video_height=vid_h,
                margin_v=margin_v,
                margin_lr=args.margin_lr,
            )
        else:
            # ── Normal SRT mode ───────────────────────────────────────────────
            if srt_path_arg is None or not srt_path_arg.exists():
                print("ERROR: No SRT file found. Provide --srt or place a .srt in input_automated/subtitles/", file=sys.stderr)
                return 1

            print(f"\n📄 SRT:      {srt_path_arg}")
            srt_entries = parse_srt(srt_path_arg, offset=subtitle_offset)
            print(f"   Entries:  {len(srt_entries)}")

            ass_content = build_ass_from_srt(
                entries=srt_entries,
                style=style,
                video_width=vid_w,
                video_height=vid_h,
                margin_v=margin_v,
                margin_lr=args.margin_lr,
            )

        # Write ASS to a temp file (or keep it if --keep-ass)
        if args.keep_ass:
            ass_out_name = output_path.stem + "_subtitles.ass"
            final_ass_path = output_path.parent / ass_out_name
            final_ass_path.write_text(ass_content, encoding="utf-8")
            print(f"💾 ASS saved: {final_ass_path}")
        else:
            # Use a temp file that we'll delete after encoding
            tmp_fd, tmp_name = tempfile.mkstemp(suffix=".ass", prefix="subs_")
            import os
            os.close(tmp_fd)
            final_ass_path = Path(tmp_name)
            final_ass_path.write_text(ass_content, encoding="utf-8")
            tmp_ass = final_ass_path

    # ── Dry run ───────────────────────────────────────────────────────────────
    if args.dry_run:
        print(f"\n✅ Dry run complete. ASS file: {final_ass_path}")
        print("   (FFmpeg not executed – use without --dry-run to encode)")
        return 0

    # ── Burn subtitles ────────────────────────────────────────────────────────
    print(f"\n🎬 Output:   {output_path}")
    try:
        burn_subtitles(
            video_path=video_path,
            ass_path=final_ass_path,
            output_path=output_path,
            crf=args.crf,
            preset=args.preset,
            audio_copy=not args.no_audio_copy,
            hwaccel=args.hwaccel,
        )
    finally:
        if tmp_ass is not None and tmp_ass.exists():
            tmp_ass.unlink(missing_ok=True)

    print(f"\n✅ Done! Output: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
