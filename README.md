# AI Automated Music Video Pipeline

A powerful, automated pipeline for creating professional music videos with synchronized karaoke subtitles. This project handles everything from cleaning up raw footage to AI-driven lyric synchronization and high-performance video rendering.

## 🚀 Features

- **Automated Pre-processing**: Detects and removes logos/watermarks from raw clips using intelligent coordinate-based filtering.
- **AI Lyric Synchronization**: Powered by **Groq Whisper API**, providing high-fidelity per-word timestamps even in complex musical environments.
- **Dynamic Montage Generation**: Automatically assembles random clips into a cinematic video, perfectly timed to your music track.
- **Professional Karaoke Subtitles**: Renders high-quality ASS subtitles with word-by-word highlighting effects and platform-aware safe zones (Instagram Reels, TikTok, YouTube Shorts).
- **Hardare Acceleration**: Optimized for **Apple Silicon (M1/M2/M3)** via `h264_videotoolbox` for lightning-fast encoding.

## 📁 Project Structure

```text
.
├── docs/                 # Detailed documentation and technical notes
├── input_automated/      # Project-specific input assets (audio, lyrics, clips)
├── output_automated/     # Generated music videos
├── videorepo/            # Processed, delogoed video clips repository
├── 1_organize_and_delogo.py
├── 2_auto_lyrics_subtitles_groq.py
├── 3_automated_music_video.py
└── 4_add_subtitles_to_video.py
```

## 🛠 Setup

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
2. **FFmpeg**: Ensure you have a full build of FFmpeg installed (on macOS: `brew install ffmpeg-full`).
3. **Configure API**:
   - Copy `groq.conf.example` to `groq.conf`.
   - Add your [Groq API Key](https://console.groq.com/keys).

## 📖 Usage

### The Workflow
1. **Prepare Clips**: Put your raw videos in `input_delogo/<project_name>` and run:
   ```bash
   python3 1_organize_and_delogo.py --project <project_name>
   ```
2. **Generate Subtitles**: Provide a vocal stem and lyrics in `input_automated/<project_name>`:
   ```bash
   python3 2_auto_lyrics_subtitles_groq.py --project <project_name>
   ```
3. **Build Video**: Assemble the montage:
   ```bash
   python3 3_automated_music_video.py --project <project_name>
   ```
4. **Burn Subtitles**: Add the final karaoke layer:
   ```bash
   python3 4_add_subtitles_to_video.py --project <project_name> --karaoke
   ```

## 📚 Documentation
For more details, check the files in the `docs/` folder:
- [Pipeline Guide](docs/PIPELINE_GUIDE.md): Step-by-step workflow guide.
- [Technical Reference](docs/TECHNICAL_REFERENCE.md): Full CLI argument list and script descriptions.
- [Workflow Analysis](docs/WORKFLOW_ANALYSIS.md): Deep-dive into technical challenges (Groq, Delogo, Performance).

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
