# Technical Reference: Automated Video Pipeline

This document provides a detailed technical reference for the four main scripts in the automated video generation pipeline.

---

## 1. [1_organize_and_delogo.py](file:///Volumes/Ext_drive/dev/ai_video_maker/1_organize_and_delogo.py)

Handles the preprocessing of raw clips by detecting faces/logos and removing them.

### CLI Arguments
| Argument | Default | Description |
| :--- | :--- | :--- |
| `--project` | `None` | Subfolder name within `input_delogo/` and `videorepo/`. |
| `--input-dir` | `input_delogo/` | Overrides the base input directory. |
| `--repo-dir` | `videorepo/` | Overrides the base output directory for processed clips. |
| `--show-logo` | `False` | Instead of removing, draws a green box around the logo area (for configuration). |

### Technical Details
- **Encoder Detection**: Automatically detects the best hardware encoder (`h264_videotoolbox`, `h264_nvenc`, `h264_amf`, etc.).
- **Logo Removal**: Uses the [delogo](file:///Volumes/Ext_drive/dev/ai_video_maker/1_organize_and_delogo.py#314-335) FFmpeg filter based on coordinates defined in [delogo_boxes.json](file:///Volumes/Ext_drive/dev/ai_video_maker/delogo_boxes.json).
- **Organization**: Clips are grouped and moved into subfolders based on their aspect ratio/form factor (e.g., `vertical`, `horizontal`, `square`).

---

## 2. [2_auto_lyrics_subtitles_groq.py](file:///Volumes/Ext_drive/dev/ai_video_maker/2_auto_lyrics_subtitles_groq.py)

Generates synchronized subtitles using Groq's Whisper API.

### CLI Arguments
| Argument | Default | Description |
| :--- | :--- | :--- |
| `--project` | `None` | Project folder within `input_automated/`. |
| `--workdir` | `input_automated/` | Pipeline root directory. |
| `--vocal-stem` | `None` | Path to the vocal-only audio file (discovered if not provided). |
| `--lyrics` | `None` | Path to the lyrics text file. |
| `--subtitle-mood` | `driving_rain_sunset` | Visual preset for ASS styling (see `SUBTITLE_MOOD_PRESETS`). |
| `--no-chunking` | `False` | Send the whole file to Groq (Warning: file size limits apply). |
| `--groq-chunk-seconds`| `24.0` | Chunk length when splitting audio for the API. |
| `--vocal-start-noise-db`| `-35.0` | DB threshold to detect when singing actually starts (to filter intro hallucinations). |

### Technical Details
- **Chunking Logic**: Splits audio into small chunks with overlap to ensure no words are lost at boundaries.
- **Alignment**: Uses a custom alignment algorithm to match Whisper's timestamped output with the "ground truth" lyrics text provided.
- **Output Formats**: Generates `.srt` (standard), `.ass` (styled), and [.json](file:///Volumes/Ext_drive/dev/ai_video_maker/delogo_boxes.json) (per-word timings for karaoke).

---

## 3. [3_automated_music_video.py](file:///Volumes/Ext_drive/dev/ai_video_maker/3_automated_music_video.py)

Assembles individual clips into a random montage synchronized to a music track.

### CLI Arguments
| Argument | Default | Description |
| :--- | :--- | :--- |
| `--project` | `None` | Project name for directory resolution. |
| `--audio` | `None` | Path to the main audio track. |
| `--width` / `--height`| `1080 / 1920` | Output video resolution. |
| `--transition-duration`| `1.0` | Crossfade duration between clips. |
| `--min-clip-duration`| `2.0` | Minimum length of a clip cut. |
| `--max-clip-duration`| `5.0` | Maximum length of a clip cut. |
| `--watermark` | `None` | Path to a video file to overlay as a watermark (transparent MOV/MP4). |

### Technical Details
- **Clip Selection**: Randomly selects clips from `videorepo/`. It avoids repeating the same clip too frequently.
- **Transitions**: Implements `xfade` (crossfade) between clips.
- **Watermark**: Uses the [overlay](file:///Volumes/Ext_drive/dev/ai_video_maker/1_organize_and_delogo.py#337-389) filter with `shortest=1` to repeat the watermark if it's shorter than the main video.

---

## 4. [4_add_subtitles_to_video.py](file:///Volumes/Ext_drive/dev/ai_video_maker/4_add_subtitles_to_video.py)

Burns subtitles into the final video with platform-aware positioning.

### CLI Arguments
| Argument | Default | Description |
| :--- | :--- | :--- |
| `--project` | `None` | Project name for auto-discovery of assets. |
| `--karaoke` | `False` | Enables word-by-word highlight effect. |
| `--style` | `neon_night` | Visual style preset (see `STYLE_PRESETS`). |
| `--platform` | `instagram_reels`| Calculates safe zones for `tiktok`, `youtube_shorts`, etc. |
| `--subtitle-offset` | `0.0` | Manual time shift for all subtitles (seconds). |
| `--auto-offset` | `False` | Compares video audio with vocal stem to align timing automatically. |
| `--hwaccel` | `None` | Forces specific hardware acceleration or disables it. |

### Styling Overrides
You can override any preset property directly:
- `--font`, `--font-size`, `--primary-color`, `--outline-color`, `--bold`, `--italic`, `--outline-width`, `--shadow-depth`, `--blur`.

### Technical Details
- **Safe Zones**: Automatically calculates vertical margins to ensure text isn't covered by platform-specific UI elements (like Like/Comment buttons).
- **Karaoke Rendering**: Converts word-level JSON timings into ASS `\k` tags for smooth per-character highlighting.
- **Encoder Performance**: Uses `h264_videotoolbox` on macOS for extremely fast encoding bypassing the CPU.
