#!/usr/bin/env python3
"""Automatic lyrics subtitles with Groq Whisper (audio + known lyrics)."""

from __future__ import annotations

import argparse
import difflib
import json
import math
import mimetypes
import re
import shutil
import subprocess
import unicodedata
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

BASE_DIR = Path(__file__).parent
DEFAULT_INPUT_ROOT = BASE_DIR / "input_automated"
DEFAULT_OUTPUT_ROOT = BASE_DIR / "output_automated"
DEFAULT_STEM_DIRNAME = "vocal_stems"
DEFAULT_LYRICS_DIRNAME = "lyrics"
DEFAULT_SUB_OUTPUT_DIRNAME = "subtitles"
GROQ_TRANSCRIPT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
DEFAULT_MODEL = "auto"
DEFAULT_PROVIDER = "groq"
DEFAULT_GROQ_MODEL = "whisper-large-v3"
DEFAULT_CONFIG_PATH = BASE_DIR / "groq.conf"
WORD_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)?", re.UNICODE)
STEM_EXTS = {".mp3", ".wav"}
LYRICS_EXTS = {".txt", ".lrc"}


@dataclass(frozen=True)
class Chunk:
    index: int
    start: float
    end: float
    path: Path

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class WordTiming:
    word: str
    norm: str
    start: float
    end: float
    confidence: float | None = None


@dataclass(frozen=True)
class LyricToken:
    text: str
    norm: str
    line_index: int


@dataclass(frozen=True)
class LyricLine:
    text: str
    start_token: int
    end_token: int


@dataclass(frozen=True)
class SubtitleMoodStyle:
    font: str
    font_size: int
    primary_hex: str
    secondary_hex: str
    outline_hex: str
    back_hex: str
    bold: bool
    italic: bool
    outline: float
    shadow: float
    margin_v: int


SUBTITLE_MOOD_PRESETS: dict[str, SubtitleMoodStyle] = {
    "driving_rain_sunset": SubtitleMoodStyle(
        font="Segoe UI Semibold",
        font_size=58,
        primary_hex="#FFD47A",
        secondary_hex="#FFD47A",
        outline_hex="#0B1B2B",
        back_hex="#05090F",
        bold=True,
        italic=False,
        outline=2.6,
        shadow=1.8,
        margin_v=300,
    ),
    "neon_night": SubtitleMoodStyle(
        font="Bahnschrift SemiBold",
        font_size=56,
        primary_hex="#7DEBFF",
        secondary_hex="#7DEBFF",
        outline_hex="#041927",
        back_hex="#02070D",
        bold=True,
        italic=False,
        outline=2.4,
        shadow=1.6,
        margin_v=304,
    ),
    "warm_cinematic": SubtitleMoodStyle(
        font="Trebuchet MS Bold",
        font_size=56,
        primary_hex="#F6C374",
        secondary_hex="#F6C374",
        outline_hex="#1C140D",
        back_hex="#0A0806",
        bold=True,
        italic=False,
        outline=2.2,
        shadow=1.4,
        margin_v=296,
    ),
    "clean_white": SubtitleMoodStyle(
        font="Segoe UI",
        font_size=54,
        primary_hex="#F6F7FA",
        secondary_hex="#F6F7FA",
        outline_hex="#121212",
        back_hex="#000000",
        bold=False,
        italic=False,
        outline=2.0,
        shadow=1.3,
        margin_v=292,
    ),
}


def run_cmd(cmd: list[str], capture_output: bool = False, encoding: str = "utf-8") -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=True,
        text=True,
        capture_output=capture_output,
        encoding=encoding,
        errors="replace",
    )


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


def ffprobe_video_size(path: Path) -> tuple[int, int]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0:s=x",
        str(path),
    ]
    res = run_cmd(cmd, capture_output=True)
    raw = res.stdout.strip()
    if "x" not in raw:
        raise ValueError(f"Unable to read video size for {path}")
    w_text, h_text = raw.split("x", 1)
    width = int(w_text)
    height = int(h_text)
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid video size for {path}: {raw}")
    return width, height


def get_real_duration(path: Path) -> float:
    """Accurately determine the duration of a media file by scanning it."""
    try:
        # Fast path first
        duration = ffprobe_duration(path)
        # Verify with a quick seek to the end if it's an MP3
        if path.suffix.lower() == ".mp3":
            # Deep scan if it might be a Suno-style MP3 with wrong header
            cmd = ["ffmpeg", "-i", str(path), "-f", "null", "-"]
            res = subprocess.run(cmd, text=True, capture_output=True, check=False)
            # Find the last time=HH:MM:SS.ms in stderr
            matches = re.findall(r"time=(\d+:\d+:\d+\.\d+)", res.stderr)
            if matches:
                last_time = matches[-1]
                h, m, s = last_time.split(":")
                real_dur = int(h) * 3600 + int(m) * 60 + float(s)
                if abs(real_dur - duration) > 1.0:
                    print(f"Correcting duration from {duration:.3f}s to {real_dur:.3f}s (deep scan)")
                    return real_dur
        return duration
    except Exception:
        return ffprobe_duration(path)


def fmt_sec(value: float) -> str:
    return f"{value:.3f}"


def parse_time_srt(seconds: float) -> str:
    value = max(0.0, seconds)
    total_ms = int(round(value * 1000.0))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_time_ass(seconds: float) -> str:
    value = max(0.0, seconds)
    total_cs = int(round(value * 100.0))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_color_from_hex(hex_rgb: str, alpha: int = 0) -> str:
    text = hex_rgb.strip().lstrip("#")
    if len(text) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in text):
        raise ValueError(f"Invalid HEX color: {hex_rgb}")
    rr = text[0:2]
    gg = text[2:4]
    bb = text[4:6]
    aa = f"{max(0, min(255, int(alpha))):02X}"
    return f"&H{aa}{bb}{gg}{rr}"


def escape_ass_text(text: str) -> str:
    return (
        text.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", r"\N")
    )


def resolve_subtitle_mood_style(
    mood_name: str,
    font_override: str,
    font_size_override: int | None,
    margin_v_override: int | None,
) -> SubtitleMoodStyle:
    preset = SUBTITLE_MOOD_PRESETS[mood_name]
    style = preset
    if font_override.strip():
        style = replace(style, font=font_override.strip())
    if font_size_override is not None and font_size_override > 0:
        style = replace(style, font_size=font_size_override)
    if margin_v_override is not None and margin_v_override >= 0:
        style = replace(style, margin_v=margin_v_override)
    return style


def normalize_word(word: str) -> str:
    text = unicodedata.normalize("NFKD", word)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("\u2019", "'").lower()
    text = "".join(ch for ch in text if ch.isalnum() or ch == "'")
    return text.strip("'")


def tokenize_text_words(text: str) -> list[str]:
    return WORD_RE.findall(text)


def load_lyrics_tokens(lyrics_path: Path) -> tuple[list[LyricToken], list[LyricLine], str]:
    content = lyrics_path.read_text(encoding="utf-8")
    raw_lines = [line.strip() for line in content.splitlines()]
    non_empty_lines = [line for line in raw_lines if line]
    if not non_empty_lines:
        raise RuntimeError(f"Lyrics file is empty: {lyrics_path}")

    tokens: list[LyricToken] = []
    lines: list[LyricLine] = []
    for line_idx, line in enumerate(non_empty_lines):
        words = tokenize_text_words(line)
        start_idx = len(tokens)
        for w in words:
            tokens.append(LyricToken(text=w, norm=normalize_word(w), line_index=line_idx))
        end_idx = len(tokens)
        lines.append(LyricLine(text=line, start_token=start_idx, end_token=end_idx))

    if not tokens:
        raise RuntimeError(f"No valid lyric words found in: {lyrics_path}")
    return tokens, lines, "\n".join(non_empty_lines)


def split_audio_chunks(
    input_audio: Path,
    duration: float,
    chunk_seconds: float,
    overlap_seconds: float,
    chunk_format: str,
    temp_dir: Path,
) -> list[Chunk]:
    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be > 0")
    if overlap_seconds < 0:
        raise ValueError("overlap_seconds must be >= 0")
    step = chunk_seconds - overlap_seconds
    if step <= 0:
        raise ValueError("chunk_seconds must be greater than overlap_seconds")

    chunks: list[Chunk] = []
    # Force conversion to WAV for reliable seeking in long/compressed files
    reliable_input = temp_dir / f"reliable_input_{uuid.uuid4().hex[:8]}.wav"
    print(f"Pre-converting to WAV for reliable chunking: {reliable_input.name}")
    run_cmd(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(input_audio), str(reliable_input)])

    start = 0.0
    idx = 0
    while start < duration - 0.01:
        end = min(duration, start + chunk_seconds)
        out = temp_dir / f"chunk_{idx:04d}.{chunk_format}"
        # Robust seeking: fast seek before input (-ss before -i)
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            fmt_sec(start),
            "-i",
            str(reliable_input),
            "-t",
            fmt_sec(end - start),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
        ]
        if chunk_format == "wav":
            cmd.extend(["-c:a", "pcm_s16le"])
        elif chunk_format == "mp3":
            cmd.extend(["-c:a", "libmp3lame", "-q:a", "2"])
        else:
            raise ValueError("chunk_format must be 'mp3' or 'wav'")
        cmd.append(str(out))
        run_cmd(cmd)
        
        # Verify chunk actually contains audio (size > 1KB for MP3)
        if out.exists() and out.stat().st_size < 1024:
            print(f"  Skipping phantom chunk {idx} (size {out.stat().st_size} bytes)")
            out.unlink(missing_ok=True)
            break

        chunks.append(Chunk(index=idx, start=start, end=end, path=out))
        idx += 1
        start += step
    return chunks


def transcript_text_to_word_timings(transcript: str, duration: float) -> list[WordTiming]:
    words = tokenize_text_words(transcript)
    if not words:
        return []
    slot = max(0.08, duration / max(1, len(words)))
    out: list[WordTiming] = []
    cursor = 0.0
    for raw in words:
        norm = normalize_word(raw)
        if not norm:
            cursor += slot
            continue
        st = cursor
        en = min(duration, max(st + 0.06, st + slot * 0.85))
        out.append(WordTiming(word=raw, norm=norm, start=st, end=en, confidence=None))
        cursor += slot
    return out


def guess_mime_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"

def groq_transcribe_full_audio(
    *,
    api_key: str,
    model: str,
    audio_path: Path,
    lyrics_text: str,
    language: str | None,
    timeout: int,
    audio_duration: float,
) -> list[WordTiming]:
    # Use curl for multipart upload; this has proven more reliable than manual multipart building.
    cmd = [
        "curl",
        "-sS",
        "-X",
        "POST",
        GROQ_TRANSCRIPT_URL,
        "-H",
        f"Authorization: Bearer {api_key}",
        "-F",
        f"file=@{audio_path}",
        "-F",
        f"model={model}",
        "-F",
        "response_format=verbose_json",
        "-F",
        "temperature=0",
        "-F",
        "timestamp_granularities[]=word",
        "-F",
        "timestamp_granularities[]=segment",
    ]
    prompt_val = lyrics_text.strip() if lyrics_text else ""
    if prompt_val and audio_duration >= 45.0:
        # Whisper prompt limit for Groq is strict (896 chars). 800 is a safe heuristic.
        cmd.extend(["-F", f"prompt={prompt_val[:800]}"])

    if language:
        cmd.extend(["-F", f"language={language}"])

    try:
        res = run_cmd(cmd, capture_output=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = stderr if stderr else stdout
        raise RuntimeError(f"Groq curl transcription failed: {details[:500]}") from exc

    try:
        stdout_text = res.stdout or ""
        payload = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Groq returned non-JSON response: {res.stdout[:600] if res.stdout else 'None'}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Groq transcription response root must be a JSON object")
    if "error" in payload:
        err = payload.get("error")
        if isinstance(err, dict):
            msg = str(err.get("message", "unknown error"))
            err_type = str(err.get("type", "unknown"))
            # If prompt failed, try one more time without prompt
            if "prompt" in msg.lower() and lyrics_text:
                print("Prompt too long or rejected, retrying without prompt...")
                return groq_transcribe_full_audio(
                    api_key=api_key,
                    model=model,
                    audio_path=audio_path,
                    lyrics_text="",
                    language=language,
                    timeout=timeout,
                    audio_duration=audio_duration,
                )
            raise RuntimeError(f"Groq API error ({err_type}): {msg}")
        raise RuntimeError(f"Groq API error: {payload}")

    words_value = payload.get("words")
    parsed_words: list[WordTiming] = []
    if isinstance(words_value, list):
        for item in words_value:
            if not isinstance(item, dict):
                continue
            raw_word = str(item.get("word", "")).strip()
            if not raw_word:
                continue
            try:
                start = float(item.get("start"))
                end = float(item.get("end"))
            except (TypeError, ValueError):
                continue
            if math.isnan(start) or math.isnan(end):
                continue
            if end < start:
                end = start + 0.08
            norm = normalize_word(raw_word)
            if not norm:
                continue
            parsed_words.append(
                WordTiming(
                    word=raw_word,
                    norm=norm,
                    start=max(0.0, start),
                    end=min(audio_duration, max(start + 0.06, end)),
                    confidence=None,
                )
            )
    if parsed_words:
        return parsed_words

    transcript = str(payload.get("text", "")).strip()
    return transcript_text_to_word_timings(transcript, audio_duration)


def detect_vocal_start(audio_path: Path, noise_db: float = -35.0, min_silence_dur: float = 0.3) -> float:
    """Return the time (seconds) when audio first becomes active after leading silence.
    Returns 0.0 if no leading silence is found (audio starts immediately)."""
    cmd = [
        "ffmpeg", "-hide_banner",
        "-i", str(audio_path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_dur}",
        "-f", "null", "-",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    output = res.stderr + res.stdout
    m = re.search(r"silence_end:\s*([\d.]+)", output)
    return float(m.group(1)) if m else 0.0


def merge_overlapping_words(words: list[WordTiming], duplicate_window: float = 0.16) -> list[WordTiming]:
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (w.start, w.end))
    merged: list[WordTiming] = [sorted_words[0]]
    for item in sorted_words[1:]:
        prev = merged[-1]
        same_word = item.norm == prev.norm
        close_start = abs(item.start - prev.start) <= duplicate_window
        close_end = abs(item.end - prev.end) <= duplicate_window
        if same_word and close_start and close_end:
            continue
        if same_word and item.start <= prev.end and item.end <= prev.end + 0.05:
            continue
        merged.append(item)
    return merged


def token_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(a=a, b=b).ratio()


def alignment_score(similarity: float) -> float:
    if similarity >= 0.98:
        return 2.4
    if similarity >= 0.90:
        return 1.8
    if similarity >= 0.80:
        return 1.1
    if similarity >= 0.68:
        return 0.2
    return -1.3


def align_lyrics_to_recognized(
    lyrics_tokens: list[LyricToken],
    recognized_words: list[WordTiming],
) -> list[int | None]:
    n = len(lyrics_tokens)
    m = len(recognized_words)
    if n == 0:
        return []
    if m == 0:
        return [None] * n

    gap = -1.05
    dp = [[0.0 for _ in range(m + 1)] for _ in range(n + 1)]
    trace = [[0 for _ in range(m + 1)] for _ in range(n + 1)]  # 0 diag, 1 up, 2 left

    for i in range(1, n + 1):
        dp[i][0] = i * gap
        trace[i][0] = 1
    for j in range(1, m + 1):
        dp[0][j] = j * gap
        trace[0][j] = 2

    for i in range(1, n + 1):
        lt = lyrics_tokens[i - 1].norm
        for j in range(1, m + 1):
            rw = recognized_words[j - 1].norm
            sim = token_similarity(lt, rw)
            diag = dp[i - 1][j - 1] + alignment_score(sim)
            up = dp[i - 1][j] + gap
            left = dp[i][j - 1] + gap

            best = diag
            direction = 0
            if up > best:
                best = up
                direction = 1
            if left > best:
                best = left
                direction = 2
            dp[i][j] = best
            trace[i][j] = direction

    mapping: list[int | None] = [None] * n
    i = n
    j = m
    while i > 0 and j > 0:
        direction = trace[i][j]
        if direction == 0:
            li = i - 1
            rj = j - 1
            sim = token_similarity(lyrics_tokens[li].norm, recognized_words[rj].norm)
            if sim >= 0.72:
                mapping[li] = rj
            i -= 1
            j -= 1
        elif direction == 1:
            i -= 1
        else:
            j -= 1

    return mapping


def median_word_duration(words: list[WordTiming]) -> float:
    durations = [max(0.06, w.end - w.start) for w in words if w.end > w.start]
    if not durations:
        return 0.24
    durations.sort()
    mid = len(durations) // 2
    if len(durations) % 2 == 1:
        med = durations[mid]
    else:
        med = (durations[mid - 1] + durations[mid]) / 2.0
    return min(0.65, max(0.10, med))


def generate_token_timings(
    lyrics_tokens: list[LyricToken],
    recognized_words: list[WordTiming],
    mapping: list[int | None],
    audio_duration: float,
) -> list[WordTiming]:
    n = len(lyrics_tokens)
    if n == 0:
        return []

    avg_dur = median_word_duration(recognized_words)
    gap = max(0.02, avg_dur * 0.18)
    starts: list[float | None] = [None] * n
    ends: list[float | None] = [None] * n

    for i, mapped in enumerate(mapping):
        if mapped is None:
            continue
        rec = recognized_words[mapped]
        starts[i] = max(0.0, rec.start)
        ends[i] = max(rec.start + 0.06, rec.end)

    anchors = [i for i in range(n) if starts[i] is not None and ends[i] is not None]

    if not anchors:
        # Last resort: uniform distribution across song.
        slot = max(0.08, audio_duration / max(1, n))
        out: list[WordTiming] = []
        cursor = 0.0
        for t in lyrics_tokens:
            st = cursor
            en = min(audio_duration, st + slot * 0.85)
            out.append(WordTiming(word=t.text, norm=t.norm, start=st, end=max(st + 0.06, en), confidence=None))
            cursor += slot
        return out

    first = anchors[0]
    if first > 0:
        anchor_time = starts[first]
        # Right-cluster pre-anchor tokens: place them just before the first
        # matched word instead of spreading backward from it.
        # Backward spread piles up at t=0 when many tokens precede the first
        # anchor (e.g. when Whisper misses the whole intro), causing subtitles
        # to appear at the very start of the song.
        for i in range(first):
            pos = first - i  # pos=1 = token immediately before anchor
            st = max(0.0, anchor_time - avg_dur * (pos - 0.5) - gap)
            en = st + avg_dur
            starts[i] = st
            ends[i] = max(st + 0.06, en)

    for left, right in zip(anchors, anchors[1:]):
        if right - left <= 1:
            continue
        assert ends[left] is not None
        assert starts[right] is not None
        fill_count = right - left - 1
        span_start = ends[left] + gap
        span_end = starts[right] - gap
        if span_end <= span_start + 0.03:
            span_end = span_start + avg_dur * fill_count
        step = (span_end - span_start) / max(1, fill_count)
        # If the available span is much larger than the time actually needed for
        # these words (indicating a long silence), cluster the tokens near the
        # RIGHT anchor instead of spreading them linearly from span_start.
        # Linear distribution places the first word of a lyric line during the
        # silence, causing the subtitle to appear before the singing starts.
        total_needed = avg_dur * fill_count
        use_right_cluster = (span_end - span_start) > total_needed * 2.0
        for pos in range(1, fill_count + 1):
            idx = left + pos
            if use_right_cluster:
                # Place tokens just before the right anchor, evenly compressed.
                st = max(span_start, span_end - avg_dur * (fill_count - pos + 1.5))
                en = st + avg_dur
            else:
                st = span_start + step * (pos - 1)
                en = st + max(0.08, min(avg_dur, step * 0.9))
            starts[idx] = st
            ends[idx] = en

    last = anchors[-1]
    for i in range(last + 1, n):
        assert ends[i - 1] is not None
        st = ends[i - 1] + gap
        en = st + avg_dur
        starts[i] = st
        ends[i] = en

    out_words: list[WordTiming] = []
    prev_start = 0.0
    for i, tok in enumerate(lyrics_tokens):
        st = starts[i] if starts[i] is not None else prev_start + 0.02
        en = ends[i] if ends[i] is not None else st + avg_dur
        st = max(0.0, st)
        if st < prev_start:
            st = prev_start + 0.01
        en = max(st + 0.06, en)
        if st > audio_duration:
            st = audio_duration
        if en > audio_duration:
            en = min(audio_duration, max(st + 0.06, audio_duration))
        prev_start = st
        conf = recognized_words[mapping[i]].confidence if mapping[i] is not None else None
        out_words.append(WordTiming(word=tok.text, norm=tok.norm, start=st, end=en, confidence=conf))
    return out_words


def build_srt_entries(lines: list[LyricLine], token_timings: list[WordTiming], audio_duration: float) -> list[tuple[int, float, float, str]]:
    entries: list[tuple[int, float, float, str]] = []
    for line in lines:
        if line.end_token <= line.start_token:
            continue
        st = token_timings[line.start_token].start
        en = token_timings[line.end_token - 1].end
        en = max(en, st + 0.40)
        en = min(audio_duration, en)
        entries.append((len(entries) + 1, st, en, line.text))

    for i in range(len(entries) - 1):
        idx, st, en, text = entries[i]
        next_st = entries[i + 1][1]
        if en >= next_st:
            en = max(st + 0.35, next_st - 0.05)
            entries[i] = (idx, st, en, text)
    return entries


def write_srt(path: Path, entries: list[tuple[int, float, float, str]]) -> None:
    out_lines: list[str] = []
    for idx, st, en, text in entries:
        out_lines.append(str(idx))
        out_lines.append(f"{parse_time_srt(st)} --> {parse_time_srt(en)}")
        out_lines.append(text)
        out_lines.append("")
    path.write_text("\n".join(out_lines).rstrip() + "\n", encoding="utf-8")


def write_ass(
    path: Path,
    entries: list[tuple[int, float, float, str]],
    style: SubtitleMoodStyle,
    playres_x: int,
    playres_y: int,
) -> None:
    def wrap_caption_text(text: str, max_chars: int, max_lines: int = 2) -> list[str]:
        words = text.split()
        if not words:
            return [text]
        lines_out: list[str] = []
        current: list[str] = []
        current_len = 0
        for word in words:
            extra = len(word) if not current else len(word) + 1
            if current and current_len + extra > max_chars:
                lines_out.append(" ".join(current))
                current = [word]
                current_len = len(word)
            else:
                current.append(word)
                current_len += extra
        if current:
            lines_out.append(" ".join(current))
        if len(lines_out) <= max_lines:
            return lines_out
        head = lines_out[: max_lines - 1]
        tail = " ".join(lines_out[max_lines - 1 :])
        return [*head, tail]

    primary = ass_color_from_hex(style.primary_hex, alpha=0)
    secondary = ass_color_from_hex(style.secondary_hex, alpha=0)
    outline = ass_color_from_hex(style.outline_hex, alpha=0)
    # Transparent-ish back color to avoid hard boxes while preserving depth.
    back = ass_color_from_hex(style.back_hex, alpha=120)
    bold = -1 if style.bold else 0
    italic = -1 if style.italic else 0
    margin_lr = 90
    render_w = max(320, playres_x)
    render_h = max(180, playres_y)
    usable_w = max(240, render_w - (margin_lr * 2))
    approx_char_w = max(10.0, style.font_size * 0.56)
    max_chars = max(14, min(42, int(usable_w / approx_char_w)))

    lines: list[str] = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {render_w}",
        f"PlayResY: {render_h}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        (
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
            "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
            "Alignment,MarginL,MarginR,MarginV,Encoding"
        ),
        (
            f"Style: Mood,{style.font},{style.font_size},{primary},{secondary},{outline},{back},"
            f"{bold},{italic},0,0,100,100,0,0,1,{style.outline:.1f},{style.shadow:.1f},2,{margin_lr},{margin_lr},{style.margin_v},1"
        ),
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    for _, st, en, text in entries:
        wrapped_lines = wrap_caption_text(text, max_chars=max_chars, max_lines=2)
        wrapped = r"\N".join(escape_ass_text(line) for line in wrapped_lines)
        lines.append(
            f"Dialogue: 0,{parse_time_ass(st)},{parse_time_ass(en)},Mood,,0,0,0,,{wrapped}"
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_json_word_timings(path: Path, words: list[WordTiming]) -> None:
    payload = {
        "words": [
            {
                "word": w.word,
                "start": round(w.start, 3),
                "end": round(w.end, 3),
                "confidence": None if w.confidence is None else round(w.confidence, 4),
            }
            for w in words
        ]
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_layout_dirs(
    stem_dir: Path,
    lyrics_dir: Path,
    subtitles_dir: Path,
) -> None:
    for d in (stem_dir, lyrics_dir, subtitles_dir):
        d.mkdir(parents=True, exist_ok=True)


def resolve_input_file(raw: str, default_dir: Path) -> Path:
    candidate = Path(raw)
    if candidate.exists():
        return candidate.resolve()
    in_default = (default_dir / raw)
    if in_default.exists():
        return in_default.resolve()
    return candidate.resolve()


def discover_default_stem(stem_dir: Path) -> Path | None:
    files = sorted([p for p in stem_dir.iterdir() if p.is_file() and p.suffix.lower() in STEM_EXTS])
    if not files:
        return None
    # Prefer WAV stems first for Groq stability and size predictability.
    preferred = [p for p in files if "vocal_stem" in p.stem.lower() and p.suffix.lower() == ".wav"]
    if not preferred:
        preferred = [p for p in files if "vocal_stem" in p.stem.lower()]
    return (preferred[0] if preferred else files[0]).resolve()


def discover_default_lyrics(lyrics_dir: Path) -> Path | None:
    files = sorted([p for p in lyrics_dir.iterdir() if p.is_file() and p.suffix.lower() in LYRICS_EXTS])
    if not files:
        return None
    preferred = [p for p in files if p.name.lower() == "lyrics.txt" or "lyrics" in p.stem.lower()]
    return (preferred[0] if preferred else files[0]).resolve()


def parse_conf_line(line: str) -> tuple[str, str] | None:
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    if "=" not in text:
        return None
    key, value = text.split("=", 1)
    key = key.strip()
    value = value.strip().strip('"').strip("'")
    if not key:
        return None
    return key, value


def load_api_key_from_config(
    config_path: Path,
    key_candidates: tuple[str, ...],
    expected_key_name: str,
) -> str:
    if not config_path.exists():
        raise RuntimeError(
            f"Config file not found: {config_path}. "
            f"Create it from {DEFAULT_CONFIG_PATH.with_suffix('.conf.example').name} "
            f"and set {expected_key_name}."
        )
    content = config_path.read_text(encoding="utf-8")
    values: dict[str, str] = {}
    for line in content.splitlines():
        parsed = parse_conf_line(line)
        if not parsed:
            continue
        key, value = parsed
        values[key] = value

    for candidate in key_candidates:
        value = values.get(candidate, "").strip()
        if value:
            return value
    raise RuntimeError(
        f"Config file {config_path} must include {expected_key_name}=<your_key>"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-sync lyrics subtitles (Groq Whisper)")
    parser.add_argument("--project", help="Project name (subfolder in input_automated and output_automated)")
    parser.add_argument("--workdir", default=None, help="Working directory (default: <input_root>/<project>)")
    parser.add_argument("--stem-dir", default=None, help=f"Vocal stem dir (default: <workdir>/{DEFAULT_STEM_DIRNAME})")
    parser.add_argument("--lyrics-dir", default=None, help=f"Lyrics dir (default: <workdir>/{DEFAULT_LYRICS_DIRNAME})")
    parser.add_argument("--subtitles-dir", default=None, help=f"Subtitles output dir (default: <workdir>/{DEFAULT_SUB_OUTPUT_DIRNAME})")
    parser.add_argument("--init-layout", action="store_true", help="Create recommended directory layout and exit")
    parser.add_argument("--vocal-stem", default=None, help="Path or filename of vocal-only stem audio (.mp3/.wav). If filename, searched in stem-dir.")
    parser.add_argument("--lyrics", default=None, help="Path or filename of lyrics text file. If filename, searched in lyrics-dir.")
    parser.add_argument("--output-srt", default=None, help="Output SRT path")
    parser.add_argument("--output-json", default=None, help="Output JSON word-timing path")
    parser.add_argument("--output-ass", default=None, help="Output ASS subtitle path (styled)")
    parser.add_argument("--subtitle-mood", choices=tuple(SUBTITLE_MOOD_PRESETS.keys()), default="driving_rain_sunset", help="Mood preset used for ASS styling")
    parser.add_argument("--subtitle-font", default="", help="Optional ASS font override")
    parser.add_argument("--subtitle-font-size", type=int, default=0, help="Optional ASS font size override (0 = use mood default)")
    parser.add_argument("--subtitle-margin-v", type=int, default=-1, help="Optional ASS vertical margin override in pixels (-1 = use mood default)")
    parser.add_argument("--ass-playres-x", type=int, default=1080, help="ASS script PlayResX")
    parser.add_argument("--ass-playres-y", type=int, default=1920, help="ASS script PlayResY")
    parser.add_argument("--no-ass", action="store_true", help="Disable ASS export")
    parser.add_argument("--model", default=DEFAULT_GROQ_MODEL, help="Groq model id")
    parser.add_argument("--groq-chunk-seconds", type=float, default=24.0, help="Groq fallback chunk length in seconds")
    parser.add_argument("--groq-overlap-seconds", type=float, default=0.5, help="Groq fallback chunk overlap in seconds")
    parser.add_argument("--no-chunking", action="store_true", help="Attempt full-audio Groq pass (likely to fail on large files/proxies)")
    parser.add_argument("--language", default=None, help="Language hint (e.g. it, en)")
    parser.add_argument("--temp-dir", default=None, help="Optional root directory for temporary chunk files")
    parser.add_argument("--keep-temp", action="store_true", help="Do not delete temporary chunk directory")
    parser.add_argument("--timeout", type=int, default=300, help="HTTP request timeout in seconds")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Config file path containing GROQ_API_KEY")
    parser.add_argument("--no-filter-early", action="store_true",
        help="Disable automatic filtering of Whisper words before the detected vocal start time. "
             "By default, words before vocal start are discarded to remove intro hallucinations.")
    parser.add_argument("--vocal-start-noise-db", type=float, default=-35.0,
        help="Noise threshold (dB) for vocal start detection via silencedetect (default: -35.0)")
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
    
    workdir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    stem_dir = Path(args.stem_dir).resolve() if args.stem_dir else workdir / DEFAULT_STEM_DIRNAME
    lyrics_dir = Path(args.lyrics_dir).resolve() if args.lyrics_dir else workdir / DEFAULT_LYRICS_DIRNAME
    subtitles_dir = Path(args.subtitles_dir).resolve() if args.subtitles_dir else workdir / DEFAULT_SUB_OUTPUT_DIRNAME

    ensure_layout_dirs(
        stem_dir=stem_dir,
        lyrics_dir=lyrics_dir,
        subtitles_dir=subtitles_dir,
    )
    if args.init_layout:
        print(f"Created/verified layout under: {workdir}")
        print(f"  vocal_stems: {stem_dir}")
        print(f"  lyrics: {lyrics_dir}")
        print(f"  subtitles: {subtitles_dir}")
        return 0

    vocal_stem = resolve_input_file(args.vocal_stem, stem_dir) if args.vocal_stem else discover_default_stem(stem_dir)
    lyrics_path = resolve_input_file(args.lyrics, lyrics_dir) if args.lyrics else discover_default_lyrics(lyrics_dir)
    if vocal_stem is None:
        raise RuntimeError(
            f"No vocal stem found. Put a .mp3/.wav in {stem_dir} or pass --vocal-stem."
        )
    if lyrics_path is None:
        raise RuntimeError(
            f"No lyrics file found. Put a .txt/.lrc in {lyrics_dir} or pass --lyrics."
        )
    if not vocal_stem.exists():
        raise RuntimeError(f"Vocal stem not found: {vocal_stem}")
    if not lyrics_path.exists():
        raise RuntimeError(f"Lyrics file not found: {lyrics_path}")

    if vocal_stem.suffix.lower() not in STEM_EXTS:
        raise RuntimeError(f"Unsupported vocal stem format: {vocal_stem.suffix}. Use .mp3 or .wav")

    config_path = Path(args.config).resolve()

    srt_path = Path(args.output_srt).resolve() if args.output_srt else subtitles_dir / f"{vocal_stem.stem}_lyrics.srt"
    json_path = Path(args.output_json).resolve() if args.output_json else subtitles_dir / f"{vocal_stem.stem}_word_timing.json"
    ass_path = Path(args.output_ass).resolve() if args.output_ass else subtitles_dir / f"{vocal_stem.stem}_lyrics.ass"

    song_duration = get_real_duration(vocal_stem)
    lyrics_tokens, lyric_lines, lyrics_text = load_lyrics_tokens(lyrics_path)

    print(f"Workdir: {workdir}")
    print(f"Stem dir: {stem_dir}")
    print(f"Lyrics dir: {lyrics_dir}")
    print(f"Subtitles dir: {subtitles_dir}")
    print(f"Vocal stem: {vocal_stem}")
    print(f"Lyrics: {lyrics_path}")
    print(f"Config: {config_path}")
    print(f"Song duration: {fmt_sec(song_duration)}s")
    print(f"Lyric tokens: {len(lyrics_tokens)}")
    api_key = load_api_key_from_config(
        config_path=config_path,
        key_candidates=("GROQ_API_KEY", "groq_api_key", "api_key"),
        expected_key_name="GROQ_API_KEY",
    )
    print(f"Model: {args.model}")

    all_recognized: list[WordTiming] = []
    full_pass_error: str | None = None
    
    # Try full audio pass ONLY if explicitly requested via --no-chunking
    if args.no_chunking:
        print("Attempting full-audio Groq pass as requested (unstable with large files/proxies)...")
        try:
            all_recognized = groq_transcribe_full_audio(
                api_key=api_key,
                model=args.model,
                audio_path=vocal_stem,
                lyrics_text=lyrics_text,
                language=args.language,
                timeout=args.timeout,
                audio_duration=song_duration,
            )
        except Exception as exc:
            full_pass_error = str(exc)
            print(f"Full-audio Groq pass failed: {full_pass_error}")
    else:
        # Default behavior: proceed directly to chunking
        pass

    if not all_recognized:
        print(
            "Switching to Groq fallback chunking "
            f"({fmt_sec(args.groq_chunk_seconds)}s chunks, {fmt_sec(args.groq_overlap_seconds)}s overlap)"
        )
        temp_root = Path(args.temp_dir).resolve() if args.temp_dir else subtitles_dir / ".groq_tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        temp_dir = temp_root / f"groq_subs_{uuid.uuid4().hex[:8]}"
        temp_dir.mkdir(parents=True, exist_ok=False)
        print(f"Temp dir: {temp_dir}")
        try:
            chunks = split_audio_chunks(
                input_audio=vocal_stem,
                duration=song_duration,
                chunk_seconds=args.groq_chunk_seconds,
                overlap_seconds=args.groq_overlap_seconds,
                chunk_format="mp3",
                temp_dir=temp_dir,
            )
            print(f"Groq chunks: {len(chunks)}")
            for chunk in chunks:
                print(
                    f"  Groq transcribe chunk {chunk.index + 1}/{len(chunks)} "
                    f"({fmt_sec(chunk.start)}s -> {fmt_sec(chunk.end)}s)"
                )
                chunk_words = groq_transcribe_full_audio(
                    api_key=api_key,
                    model=args.model,
                    audio_path=chunk.path,
                    lyrics_text=lyrics_text,
                    language=args.language,
                    timeout=args.timeout,
                    audio_duration=chunk.duration,
                )
                for w in chunk_words:
                    # Skip words from the overlap region in non-first chunks.
                    # These words were already captured more accurately in the previous chunk.
                    # Including them again produces duplicates with slightly earlier timestamps
                    # that cause subtitles to appear too early.
                    if chunk.index > 0 and w.start < args.groq_overlap_seconds:
                        continue
                    abs_start = min(song_duration, max(0.0, chunk.start + w.start))
                    abs_end = min(song_duration, max(abs_start + 0.06, chunk.start + w.end))
                    all_recognized.append(
                        WordTiming(
                            word=w.word,
                            norm=w.norm,
                            start=abs_start,
                            end=abs_end,
                            confidence=w.confidence,
                        )
                    )
        finally:
            if not args.keep_temp:
                shutil.rmtree(temp_dir, ignore_errors=True)

    if not all_recognized and full_pass_error:
        raise RuntimeError(
            "Groq could not return recognized words. "
            f"Last full-audio error: {full_pass_error}"
        )
    print(f"Recognized words (raw): {len(all_recognized)}")

    merged = merge_overlapping_words(all_recognized)
    print(f"Recognized words after merge: {len(merged)}")

    # Filter Whisper words that fall before the vocal start time.
    # Whisper frequently hallucinates words during the music intro (before singing
    # begins). If the alignment matches lyric tokens to these phantom early words,
    # the subtitles will appear at the start of the song instead of when sung.
    if not args.no_filter_early:
        vocal_start = detect_vocal_start(vocal_stem, noise_db=args.vocal_start_noise_db)
        print(f"Vocal start detected: {vocal_start:.3f}s")
        if vocal_start > 0.5:
            min_t = max(0.0, vocal_start - 0.3)  # 0.3 s buffer before vocal start
            before = len(merged)
            merged = [w for w in merged if w.start >= min_t]
            removed = before - len(merged)
            if removed:
                print(f"  Filtered {removed} early words (before {min_t:.3f}s)")

    mapping = align_lyrics_to_recognized(lyrics_tokens, merged)
    matched = sum(1 for x in mapping if x is not None)
    print(f"Matched lyric tokens: {matched}/{len(lyrics_tokens)}")

    if matched > 0:
        print("\nTiming Diagnostics (First vs Last matches):")
        matched_indices = [i for i, m in enumerate(mapping) if m is not None]
        for i in matched_indices[:5]:
            m = mapping[i]
            rec = merged[m]
            print(f"  [Lyric] '{lyrics_tokens[i].text}' -> [Audio] '{rec.word}' at {fmt_sec(rec.start)}s")
        if len(matched_indices) > 5:
            print("  ...")
            for i in matched_indices[-5:]:
                m = mapping[i]
                rec = merged[m]
                print(f"  [Lyric] '{lyrics_tokens[i].text}' -> [Audio] '{rec.word}' at {fmt_sec(rec.start)}s")
        print("")

    final_token_timings = generate_token_timings(
        lyrics_tokens=lyrics_tokens,
        recognized_words=merged,
        mapping=mapping,
        audio_duration=song_duration,
    )
    srt_entries = build_srt_entries(lyric_lines, final_token_timings, song_duration)

    srt_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    write_srt(srt_path, srt_entries)
    write_json_word_timings(json_path, final_token_timings)

    if not args.no_ass:
        ass_path.parent.mkdir(parents=True, exist_ok=True)
        ass_style = resolve_subtitle_mood_style(
            mood_name=args.subtitle_mood,
            font_override=args.subtitle_font,
            font_size_override=(args.subtitle_font_size if args.subtitle_font_size > 0 else None),
            margin_v_override=(args.subtitle_margin_v if args.subtitle_margin_v >= 0 else None),
        )
        write_ass(
            path=ass_path,
            entries=srt_entries,
            style=ass_style,
            playres_x=max(320, args.ass_playres_x),
            playres_y=max(180, args.ass_playres_y),
        )

    print(f"Done: {srt_path}")
    print(f"Word timings: {json_path}")
    if not args.no_ass:
        print(f"Styled ASS: {ass_path}")
        print(f"ASS PlayRes: {max(320, args.ass_playres_x)}x{max(180, args.ass_playres_y)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
