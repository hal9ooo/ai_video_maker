#!/usr/bin/env python3
"""
AI Video Maker — GUI
Cross-platform PySide6 interface for the 4-step video pipeline.

Usage:
    python gui.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, QProcessEnvironment
from PySide6.QtGui import QFont, QColor, QPalette, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

BASE_DIR = Path(__file__).parent
SCRIPT_1 = str(BASE_DIR / "1_organize_and_delogo.py")
SCRIPT_2 = str(BASE_DIR / "2_auto_lyrics_subtitles_groq.py")
SCRIPT_3 = str(BASE_DIR / "3_automated_music_video.py")
SCRIPT_4 = str(BASE_DIR / "4_add_subtitles_to_video.py")

# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────

class _PathEdit(QWidget):
    """A line-edit with a Browse button (file or folder).

    Args:
        mode:         'folder', 'open', or 'save'
        filter_:      File dialog filter string
        default_text: Pre-filled value (shown in normal text, user can edit/clear)
        placeholder:  Hint shown when field is empty
    """

    def __init__(self, mode: str = "folder", filter_: str = "",
                 default_text: str = "", placeholder: str = "", parent=None):
        super().__init__(parent)
        self._mode = mode
        self._filter = filter_
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        hint = placeholder or ("Select folder…" if mode == "folder" else "Select file…")
        self.edit.setPlaceholderText(hint)
        if default_text:
            self.edit.setText(default_text)
        self.btn = QPushButton("Browse…")
        self.btn.setFixedWidth(80)
        self.btn.clicked.connect(self._browse)
        layout.addWidget(self.edit)
        layout.addWidget(self.btn)

    def _browse(self):
        start = self.edit.text() or str(BASE_DIR)
        if self._mode == "folder":
            path = QFileDialog.getExistingDirectory(self, "Select folder", start)
        elif self._mode == "save":
            path, _ = QFileDialog.getSaveFileName(self, "Save file", start, self._filter)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Open file", start, self._filter)
        if path:
            self.edit.setText(path)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, v: str):
        self.edit.setText(v)


def _group(title: str, *widgets) -> QGroupBox:
    """Wrap widgets in a named QGroupBox with a form layout."""
    box = QGroupBox(title)
    lay = QFormLayout(box)
    lay.setContentsMargins(8, 12, 8, 8)
    lay.setSpacing(6)
    for label, widget in widgets:
        lay.addRow(label, widget)
    return box


def _spin(lo: int, hi: int, val: int, step: int = 1) -> QSpinBox:
    w = QSpinBox()
    w.setRange(lo, hi)
    w.setValue(val)
    w.setSingleStep(step)
    return w


def _dspin(lo: float, hi: float, val: float, step: float = 0.1, decimals: int = 2) -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setValue(val)
    w.setSingleStep(step)
    w.setDecimals(decimals)
    return w


def _combo(choices: list[str], default: str = "") -> QComboBox:
    w = QComboBox()
    w.addItems(choices)
    if default and default in choices:
        w.setCurrentText(default)
    return w


def _line(placeholder: str = "", default: str = "") -> QLineEdit:
    w = QLineEdit()
    if placeholder:
        w.setPlaceholderText(placeholder)
    if default:
        w.setText(default)
    return w


def _check(label: str, checked: bool = False) -> QCheckBox:
    w = QCheckBox(label)
    w.setChecked(checked)
    return w


def _scrollable(widget: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidget(widget)
    sa.setWidgetResizable(True)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    sa.setFrameShape(QScrollArea.NoFrame)
    return sa


# ─────────────────────────────────────────────────────────────────────────────
# Tab 1 — Organize & Delogo
# ─────────────────────────────────────────────────────────────────────────────

class Tab1(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        # ── parameters ──
        self.input_dir  = _PathEdit("folder", default_text=str(BASE_DIR / "input_delogo"))
        self.repo_dir   = _PathEdit("folder", default_text=str(BASE_DIR / "videorepo"))
        self.box_config = _PathEdit("open", "JSON (*.json)", default_text=str(BASE_DIR / "delogo_boxes.json"))

        PRESETS = ["ultrafast","superfast","veryfast","faster","fast","medium","slow","slower","veryslow"]
        self.preset  = _combo(PRESETS, "medium")
        self.crf     = _spin(0, 51, 23)
        self.show_logo = _check("Show logo area (debug red box)", False)

        content = QWidget()
        vlay = QVBoxLayout(content)
        vlay.setAlignment(Qt.AlignTop)
        vlay.setSpacing(10)

        vlay.addWidget(_group("Directories",
            ("Input dir (videos with logo):", self.input_dir),
            ("Video repo (output):", self.repo_dir),
            ("Box config file:", self.box_config),
        ))
        vlay.addWidget(_group("Encoding",
            ("FFmpeg preset:", self.preset),
            ("CRF (0 = lossless, 51 = worst):", self.crf),
            ("Debug:", self.show_logo),
        ))
        vlay.addStretch()

        main = QVBoxLayout(self)
        main.addWidget(_scrollable(content))

    def build_args(self) -> list[str]:
        """Return extra environment overrides via a small bootstrap wrapper."""
        # Script 1 has no argparse — we inject constants via a wrapper call.
        # We write a tiny inline Python snippet that patches the module globals.
        return []  # args handled by build_cmd

    def build_cmd(self) -> list[str]:
        """Build the command to run, patching module constants via -c."""
        input_dir  = self.input_dir.text()  or str(BASE_DIR / "input_delogo")
        repo_dir   = self.repo_dir.text()   or str(BASE_DIR / "videorepo")
        box_config = self.box_config.text() or str(BASE_DIR / "delogo_boxes.json")
        preset     = self.preset.currentText()
        crf        = str(self.crf.value())
        show_logo  = str(self.show_logo.isChecked())

        snippet = (
            "import sys, types; "
            f"sys.path.insert(0, r'{str(BASE_DIR)}'); "
            "import importlib.util, pathlib, json; "
            f"spec = importlib.util.spec_from_file_location('m', r'{SCRIPT_1}'); "
            "m = importlib.util.module_from_spec(spec); "
            f"m.INPUT_DIR = pathlib.Path(r'{input_dir}'); "
            f"m.REPO_DIR = pathlib.Path(r'{repo_dir}'); "
            f"m.BOX_CONFIG_PATH = pathlib.Path(r'{box_config}'); "
            f"m.FFMPEG_PRESET = '{preset}'; "
            f"m.FFMPEG_CRF = '{crf}'; "
            "spec.loader.exec_module(m); "
            f"m.main()"
        )
        return [sys.executable, "-c", snippet]


# ─────────────────────────────────────────────────────────────────────────────
# Tab 2 — Auto Lyrics Subtitles (Groq)
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_WORKDIR = str(BASE_DIR / "input_automated")

class Tab2(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        MOODS = ["driving_rain_sunset", "neon_night", "warm_cinematic", "clean_white"]
        MODELS = ["whisper-large-v3", "whisper-large-v3-turbo", "distil-whisper-large-v3-en"]

        _workdir     = str(BASE_DIR / "input_automated")
        _stem_dir    = str(BASE_DIR / "input_automated" / "vocal_stems")
        _lyrics_dir  = str(BASE_DIR / "input_automated" / "lyrics")
        _subs_dir    = str(BASE_DIR / "input_automated" / "subtitles")

        # ── Dirs ──
        self.workdir       = _PathEdit("folder", default_text=_workdir)
        self.stem_dir      = _PathEdit("folder", default_text=_stem_dir, placeholder="<workdir>/vocal_stems")
        self.lyrics_dir    = _PathEdit("folder", default_text=_lyrics_dir, placeholder="<workdir>/lyrics")
        self.subtitles_dir = _PathEdit("folder", default_text=_subs_dir, placeholder="<workdir>/subtitles")

        # ── Files ──
        self.vocal_stem  = _PathEdit("open", "Audio (*.mp3 *.wav)", placeholder="Auto-detected in vocal_stems/")
        self.lyrics_file = _PathEdit("open", "Text (*.txt *.lrc)",  placeholder="Auto-detected in lyrics/")
        self.output_srt  = _PathEdit("save", "SRT (*.srt)",          placeholder="Auto: <subtitles>/<stem>_lyrics.srt")
        self.output_json = _PathEdit("save", "JSON (*.json)",         placeholder="Auto: <subtitles>/<stem>_word_timing.json")
        self.output_ass  = _PathEdit("save", "ASS (*.ass)",           placeholder="Auto: <subtitles>/<stem>_lyrics.ass")

        # ── Groq ──
        self.config       = _PathEdit("open", "Config (*.conf *.txt)", default_text=str(BASE_DIR / "groq.conf"))
        self.model        = _combo(MODELS, "whisper-large-v3")
        self.language     = _line("e.g. it, en (leave blank for auto)")
        self.timeout      = _spin(10, 600, 300)

        # ── Chunking ──
        self.chunk_secs   = _dspin(1.0, 300.0, 24.0, 1.0, 1)
        self.overlap_secs = _dspin(0.0, 30.0, 0.5, 0.1, 1)
        self.no_chunking  = _check("Disable chunking (full-audio pass, may fail)")
        self.keep_temp    = _check("Keep temp chunk files")
        self.temp_dir     = _PathEdit("folder", placeholder="auto: <subtitles-dir>/.groq_tmp")

        # ── ASS style ──
        self.subtitle_mood       = _combo(MOODS, "driving_rain_sunset")
        self.subtitle_font       = _line("Leave blank for preset default")
        self.subtitle_font_size  = _spin(0, 200, 0)
        self.subtitle_margin_v   = _spin(-1, 2000, -1)
        self.ass_playres_x       = _spin(100, 7680, 1080)
        self.ass_playres_y       = _spin(100, 4320, 1920)

        # ── Toggles ──
        self.no_ass          = _check("Disable ASS export")
        self.no_filter_early = _check("Disable early-word filter")
        self.noise_db        = _dspin(-60.0, 0.0, -35.0, 1.0, 1)
        self.init_layout     = _check("Init layout (create dirs and exit)")

        content = QWidget()
        vlay = QVBoxLayout(content)
        vlay.setAlignment(Qt.AlignTop)
        vlay.setSpacing(10)
        vlay.addWidget(_group("Directories",
            ("Workdir:", self.workdir),
            ("Stem dir:", self.stem_dir),
            ("Lyrics dir:", self.lyrics_dir),
            ("Subtitles dir:", self.subtitles_dir),
        ))
        vlay.addWidget(_group("Input Files",
            ("Vocal stem (.mp3/.wav):", self.vocal_stem),
            ("Lyrics file (.txt/.lrc):", self.lyrics_file),
        ))
        vlay.addWidget(_group("Output Files",
            ("Output SRT:", self.output_srt),
            ("Output JSON (word timings):", self.output_json),
            ("Output ASS:", self.output_ass),
        ))
        vlay.addWidget(_group("Groq API",
            ("Config file (GROQ_API_KEY):", self.config),
            ("Model:", self.model),
            ("Language hint:", self.language),
            ("Timeout (s):", self.timeout),
        ))
        vlay.addWidget(_group("Chunking",
            ("Chunk length (s):", self.chunk_secs),
            ("Chunk overlap (s):", self.overlap_secs),
            ("Temp dir:", self.temp_dir),
            ("Options:", self.no_chunking),
            ("", self.keep_temp),
        ))
        vlay.addWidget(_group("ASS Subtitle Style",
            ("Mood preset:", self.subtitle_mood),
            ("Font override:", self.subtitle_font),
            ("Font size (0 = preset):", self.subtitle_font_size),
            ("Margin V (-1 = preset):", self.subtitle_margin_v),
            ("PlayResX:", self.ass_playres_x),
            ("PlayResY:", self.ass_playres_y),
        ))
        vlay.addWidget(_group("Options",
            ("", self.no_ass),
            ("", self.no_filter_early),
            ("Vocal start noise (dB):", self.noise_db),
            ("", self.init_layout),
        ))
        vlay.addStretch()

        main = QVBoxLayout(self)
        main.addWidget(_scrollable(content))

    def build_cmd(self) -> list[str]:
        args = [sys.executable, SCRIPT_2]

        def _add(flag, widget, empty_val=""):
            v = widget.text() if hasattr(widget, 'text') and not isinstance(widget, QCheckBox) else ""
            if isinstance(widget, QLineEdit):
                v = widget.text().strip()
            elif isinstance(widget, _PathEdit):
                v = widget.text()
            if v and v != str(empty_val):
                args.extend([flag, v])

        def _add_check(flag, widget):
            if widget.isChecked():
                args.append(flag)

        def _add_val(flag, widget, default):
            if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                v = widget.value()
                if v != default:
                    args.extend([flag, str(v)])
            elif isinstance(widget, QComboBox):
                args.extend([flag, widget.currentText()])

        _add("--workdir",       self.workdir)
        _add("--stem-dir",      self.stem_dir)
        _add("--lyrics-dir",    self.lyrics_dir)
        _add("--subtitles-dir", self.subtitles_dir)
        _add("--vocal-stem",    self.vocal_stem)
        _add("--lyrics",        self.lyrics_file)
        _add("--output-srt",    self.output_srt)
        _add("--output-json",   self.output_json)
        _add("--output-ass",    self.output_ass)
        _add("--config",        self.config)
        args.extend(["--model", self.model.currentText()])
        lang = self.language.text().strip()
        if lang:
            args.extend(["--language", lang])
        if self.timeout.value() != 300:
            args.extend(["--timeout", str(self.timeout.value())])
        if self.chunk_secs.value() != 24.0:
            args.extend(["--groq-chunk-seconds", str(self.chunk_secs.value())])
        if self.overlap_secs.value() != 0.5:
            args.extend(["--groq-overlap-seconds", str(self.overlap_secs.value())])
        _add("--temp-dir", self.temp_dir)
        _add_check("--no-chunking",    self.no_chunking)
        _add_check("--keep-temp",      self.keep_temp)
        args.extend(["--subtitle-mood", self.subtitle_mood.currentText()])
        font = self.subtitle_font.text().strip()
        if font:
            args.extend(["--subtitle-font", font])
        if self.subtitle_font_size.value() > 0:
            args.extend(["--subtitle-font-size", str(self.subtitle_font_size.value())])
        if self.subtitle_margin_v.value() >= 0:
            args.extend(["--subtitle-margin-v", str(self.subtitle_margin_v.value())])
        if self.ass_playres_x.value() != 1080:
            args.extend(["--ass-playres-x", str(self.ass_playres_x.value())])
        if self.ass_playres_y.value() != 1920:
            args.extend(["--ass-playres-y", str(self.ass_playres_y.value())])
        _add_check("--no-ass",          self.no_ass)
        _add_check("--no-filter-early", self.no_filter_early)
        if self.noise_db.value() != -35.0:
            args.extend(["--vocal-start-noise-db", str(self.noise_db.value())])
        _add_check("--init-layout",     self.init_layout)
        return args


# ─────────────────────────────────────────────────────────────────────────────
# Tab 3 — Automated Music Video
# ─────────────────────────────────────────────────────────────────────────────

class Tab3(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        PRESETS = ["ultrafast","superfast","veryfast","faster","fast","medium","slow","slower","veryslow"]
        TRANSITIONS = ["fade","distance","smoothleft","smoothright","smoothup","smoothdown"]

        _workdir    = str(BASE_DIR / "input_automated")
        _audio_dir  = str(BASE_DIR / "input_automated" / "audio")
        _video_dir  = str(BASE_DIR / "input_automated" / "video_clips")
        _output_dir = str(BASE_DIR / "output_automated")

        # ── Directories ──
        self.workdir    = _PathEdit("folder", default_text=_workdir)
        self.audio_dir  = _PathEdit("folder", default_text=_audio_dir,  placeholder="<workdir>/audio")
        self.video_dir  = _PathEdit("folder", default_text=_video_dir,  placeholder="<workdir>/video_clips")
        self.output_dir = _PathEdit("folder", default_text=_output_dir, placeholder="<script dir>/output_automated")
        self.output_filename = _line("output.mp4", "output.mp4")

        # ── Timeline ──
        self.seed            = _line("Leave blank for random", "")
        self.trans_dur       = _dspin(0.1, 10.0, 1.0, 0.1, 2)
        self.min_trans_dur   = _dspin(0.05, 5.0, 0.25, 0.05, 2)
        self.preview_seconds = _dspin(0.0, 3600.0, 0.0, 1.0, 1)

        # ── Resolution ──
        self.width  = _spin(16, 7680, 1080)
        self.height = _spin(16, 4320, 1920)
        self.fps    = _spin(1, 120, 30)

        # ── Encoding ──
        self.crf     = _spin(0, 51, 18)
        self.preset  = _combo(PRESETS, "slow")
        self.maxrate = _line("", "8M")
        self.bufsize = _line("", "16M")

        # ── Audio ──
        self.audio_bitrate = _line("", "320k")
        self.audio_vbr     = _spin(1, 5, 5)

        # ── Heat ──
        self.heat_penalty = _dspin(0.0, 100.0, 10.0, 1.0, 1)
        self.heat_decay   = _dspin(0.0, 1.0, 0.0, 0.01, 2)

        # ── Watermark ──
        self.watermark       = _PathEdit("open", "Video (*.mp4 *.mov)")
        self.no_watermark    = _check("Disable watermark")
        self.wm_width        = _spin(0, 7680, 0)
        self.wm_margin       = _spin(0, 500, 20)
        self.wm_chroma_color = _line("", "0x00FF00")
        self.wm_chroma_sim   = _dspin(0.0, 1.0, 0.3, 0.01, 2)
        self.wm_chroma_blend = _dspin(0.0, 1.0, 0.05, 0.01, 2)

        # ── Actions ──
        self.init_layout = _check("Init layout (create dirs and exit)")
        self.dry_run     = _check("Dry run (print command, don't encode)")

        content = QWidget()
        vlay = QVBoxLayout(content)
        vlay.setAlignment(Qt.AlignTop)
        vlay.setSpacing(10)

        vlay.addWidget(_group("Directories & Output",
            ("Workdir:", self.workdir),
            ("Audio dir:", self.audio_dir),
            ("Video clips dir:", self.video_dir),
            ("Output dir:", self.output_dir),
            ("Output filename:", self.output_filename),
        ))
        vlay.addWidget(_group("Timeline",
            ("Random seed (blank = random):", self.seed),
            ("Transition duration (s):", self.trans_dur),
            ("Min transition duration (s):", self.min_trans_dur),
            ("Preview seconds (0 = full):", self.preview_seconds),
        ))
        vlay.addWidget(_group("Output Resolution",
            ("Width:", self.width),
            ("Height:", self.height),
            ("FPS:", self.fps),
        ))
        vlay.addWidget(_group("Video Encoding",
            ("CRF:", self.crf),
            ("Preset (libx264 fallback):", self.preset),
            ("Max bitrate:", self.maxrate),
            ("Buffer size:", self.bufsize),
        ))
        vlay.addWidget(_group("Audio Encoding",
            ("Audio bitrate (AAC fallback):", self.audio_bitrate),
            ("VBR quality (libfdk_aac, 1–5):", self.audio_vbr),
        ))
        vlay.addWidget(_group("Clip Selection Heat",
            ("Heat penalty:", self.heat_penalty),
            ("Heat decay (0 = auto):", self.heat_decay),
        ))
        vlay.addWidget(_group("Watermark",
            ("Watermark video (green screen):", self.watermark),
            ("", self.no_watermark),
            ("Width (0 = 50% of output):", self.wm_width),
            ("Top margin (px):", self.wm_margin),
            ("Chroma key color:", self.wm_chroma_color),
            ("Chroma similarity (0–1):", self.wm_chroma_sim),
            ("Chroma blend (0–1):", self.wm_chroma_blend),
        ))
        vlay.addWidget(_group("Actions",
            ("", self.init_layout),
            ("", self.dry_run),
        ))
        vlay.addStretch()

        main = QVBoxLayout(self)
        main.addWidget(_scrollable(content))

    def build_cmd(self) -> list[str]:
        args = [sys.executable, SCRIPT_3]

        def _add(flag, widget):
            v = widget.text() if isinstance(widget, (_PathEdit, QLineEdit)) else ""
            if isinstance(widget, QLineEdit):
                v = widget.text().strip()
            elif isinstance(widget, _PathEdit):
                v = widget.text()
            if v:
                args.extend([flag, v])

        def _check_add(flag, widget):
            if widget.isChecked():
                args.append(flag)

        _add("--workdir",    self.workdir)
        _add("--audio-dir",  self.audio_dir)
        _add("--video-dir",  self.video_dir)
        _add("--output-dir", self.output_dir)
        _add("--output",     self.output_filename)

        seed = self.seed.text().strip()
        if seed:
            args.extend(["--seed", seed])
        if self.trans_dur.value() != 1.0:
            args.extend(["--transition-duration", str(self.trans_dur.value())])
        if self.min_trans_dur.value() != 0.25:
            args.extend(["--min-transition-duration", str(self.min_trans_dur.value())])
        if self.preview_seconds.value() > 0.0:
            args.extend(["--preview-seconds", str(self.preview_seconds.value())])

        if self.width.value() != 1080:
            args.extend(["--width", str(self.width.value())])
        if self.height.value() != 1920:
            args.extend(["--height", str(self.height.value())])
        if self.fps.value() != 30:
            args.extend(["--fps", str(self.fps.value())])

        if self.crf.value() != 18:
            args.extend(["--crf", str(self.crf.value())])
        if self.preset.currentText() != "slow":
            args.extend(["--preset", self.preset.currentText()])
        _add("--maxrate", self.maxrate)
        _add("--bufsize", self.bufsize)
        _add("--audio-bitrate", self.audio_bitrate)
        if self.audio_vbr.value() != 5:
            args.extend(["--audio-vbr", str(self.audio_vbr.value())])

        if self.heat_penalty.value() != 10.0:
            args.extend(["--heat-penalty", str(self.heat_penalty.value())])
        if self.heat_decay.value() != 0.0:
            args.extend(["--heat-decay", str(self.heat_decay.value())])

        _add("--watermark", self.watermark)
        _check_add("--no-watermark", self.no_watermark)
        if self.wm_width.value() != 0:
            args.extend(["--watermark-width", str(self.wm_width.value())])
        if self.wm_margin.value() != 20:
            args.extend(["--watermark-margin", str(self.wm_margin.value())])
        _add("--watermark-chroma-color", self.wm_chroma_color)
        if self.wm_chroma_sim.value() != 0.3:
            args.extend(["--watermark-chroma-similarity", str(self.wm_chroma_sim.value())])
        if self.wm_chroma_blend.value() != 0.05:
            args.extend(["--watermark-chroma-blend", str(self.wm_chroma_blend.value())])

        _check_add("--init-layout", self.init_layout)
        _check_add("--dry-run",     self.dry_run)
        return args


# ─────────────────────────────────────────────────────────────────────────────
# Tab 4 — Add Subtitles to Video
# ─────────────────────────────────────────────────────────────────────────────

class Tab4(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        STYLES    = ["neon_night", "warm_cinematic", "bold_impact", "clean_white"]
        PLATFORMS = ["instagram_reels", "tiktok", "youtube_shorts", "generic"]
        PRESETS   = ["ultrafast","superfast","veryfast","faster","fast","medium","slow","slower","veryslow"]

        _video_dir  = str(BASE_DIR / "output_automated")
        _subs_dir   = str(BASE_DIR / "input_automated" / "subtitles")
        _output_dir = str(BASE_DIR / "output_automated")

        # ── Files ──
        self.video  = _PathEdit("open", "Video (*.mp4 *.mov *.mkv)", placeholder=f"Auto-detected in {_video_dir}/")
        self.srt    = _PathEdit("open", "SRT (*.srt)",                placeholder=f"Auto-detected in {_subs_dir}/")
        self.ass    = _PathEdit("open", "ASS (*.ass)",                placeholder=f"Auto-detected in {_subs_dir}/")
        self.json   = _PathEdit("open", "JSON (*.json)",              placeholder=f"Auto-detected in {_subs_dir}/")
        self.output = _PathEdit("save", "Video (*.mp4)",              placeholder=f"Auto: {_output_dir}/<name>_subtitled.mp4")
        self.audio  = _PathEdit("open", "Audio (*.wav *.mp3)",        placeholder="Optional: for --auto-offset")

        # ── Style ──
        self.style    = _combo(STYLES, "neon_night")
        self.use_srt  = _check("Force re-generate ASS from SRT")
        self.karaoke  = _check("Karaoke word-highlight mode")

        # ── Style overrides ──
        self.font          = _line("Leave blank for preset default")
        self.font_size     = _spin(0, 200, 0)
        self.primary_color = _line("#RRGGBB override", "")
        self.outline_color = _line("#RRGGBB override", "")
        self.bold_check    = _check("Bold")
        self.italic_check  = _check("Italic")
        self.outline_width = _dspin(0.0, 10.0, 0.0, 0.1, 1)
        self.shadow_depth  = _dspin(0.0, 10.0, 0.0, 0.1, 1)
        self.blur          = _dspin(0.0, 10.0, 0.0, 0.1, 1)

        # ── Positioning ──
        self.platform      = _combo(PLATFORMS, "instagram_reels")
        self.margin_bottom = _spin(-1, 5000, -1)
        self.margin_lr     = _spin(0, 500, 80)
        self.extra_pad     = _spin(0, 500, 0)

        # ── Timing ──
        self.subtitle_offset = _dspin(-300.0, 300.0, 0.0, 0.5, 2)
        self.auto_offset     = _check("Auto-compute offset (requires --audio)")

        # ── Encoding ──
        self.crf            = _spin(0, 51, 18)
        self.preset         = _combo(PRESETS, "slow")
        self.no_audio_copy  = _check("Re-encode audio to AAC (instead of stream copy)")

        # hwaccel: None/True/False → Auto / Force HW / Force libx264
        self.hwaccel = _combo(["Auto-detect", "Force hardware", "Force libx264"], "Auto-detect")

        # ── Debug ──
        self.keep_ass   = _check("Keep generated .ass file")
        self.dry_run    = _check("Dry run (generate ASS, don't encode)")
        self.list_styles = _check("List styles and exit")

        content = QWidget()
        vlay = QVBoxLayout(content)
        vlay.setAlignment(Qt.AlignTop)
        vlay.setSpacing(10)
        vlay.addWidget(_group("Input / Output",
            ("Input video:", self.video),
            ("SRT file:", self.srt),
            ("Pre-built ASS file:", self.ass),
            ("Word-timing JSON:", self.json),
            ("Output video:", self.output),
        ))
        vlay.addWidget(_group("Style",
            ("Style preset:", self.style),
            ("", self.use_srt),
            ("", self.karaoke),
        ))
        vlay.addWidget(_group("Style Overrides (leave blank = use preset)",
            ("Font:", self.font),
            ("Font size (0 = preset):", self.font_size),
            ("Primary color (#RRGGBB):", self.primary_color),
            ("Outline color (#RRGGBB):", self.outline_color),
            ("", self.bold_check),
            ("", self.italic_check),
            ("Outline width (0 = preset):", self.outline_width),
            ("Shadow depth (0 = preset):", self.shadow_depth),
            ("Blur (0 = preset default):", self.blur),
        ))
        vlay.addWidget(_group("Positioning",
            ("Platform:", self.platform),
            ("Bottom margin px (-1 = auto):", self.margin_bottom),
            ("Left/right margin px:", self.margin_lr),
            ("Extra padding above safe zone:", self.extra_pad),
        ))
        vlay.addWidget(_group("Timing",
            ("Subtitle offset (s):", self.subtitle_offset),
            ("Full audio file (for auto-offset):", self.audio),
            ("", self.auto_offset),
        ))
        vlay.addWidget(_group("Encoding",
            ("CRF:", self.crf),
            ("Preset (libx264 fallback):", self.preset),
            ("Hardware accel:", self.hwaccel),
            ("", self.no_audio_copy),
        ))
        vlay.addWidget(_group("Debug",
            ("", self.keep_ass),
            ("", self.dry_run),
            ("", self.list_styles),
        ))
        vlay.addStretch()

        main = QVBoxLayout(self)
        main.addWidget(_scrollable(content))

    def build_cmd(self) -> list[str]:
        args = [sys.executable, SCRIPT_4]

        def _add(flag, widget):
            v = ""
            if isinstance(widget, _PathEdit):
                v = widget.text()
            elif isinstance(widget, QLineEdit):
                v = widget.text().strip()
            if v:
                args.extend([flag, v])

        def _check_add(flag, widget):
            if widget.isChecked():
                args.append(flag)

        _add("--video",  self.video)
        _add("--srt",    self.srt)
        _add("--ass",    self.ass)
        _add("--json",   self.json)
        _add("--output", self.output)
        _add("--audio",  self.audio)

        args.extend(["--style", self.style.currentText()])
        _check_add("--use-srt",  self.use_srt)
        _check_add("--karaoke",  self.karaoke)

        font = self.font.text().strip()
        if font:
            args.extend(["--font", font])
        if self.font_size.value() > 0:
            args.extend(["--font-size", str(self.font_size.value())])
        pc = self.primary_color.text().strip()
        if pc and pc.startswith("#"):
            args.extend(["--primary-color", pc])
        oc = self.outline_color.text().strip()
        if oc and oc.startswith("#"):
            args.extend(["--outline-color", oc])
        if self.bold_check.isChecked():
            args.append("--bold")
        if self.italic_check.isChecked():
            args.append("--italic")
        if self.outline_width.value() > 0:
            args.extend(["--outline-width", str(self.outline_width.value())])
        if self.shadow_depth.value() > 0:
            args.extend(["--shadow-depth", str(self.shadow_depth.value())])
        if self.blur.value() > 0:
            args.extend(["--blur", str(self.blur.value())])

        args.extend(["--platform", self.platform.currentText()])
        if self.margin_bottom.value() >= 0:
            args.extend(["--margin-bottom", str(self.margin_bottom.value())])
        if self.margin_lr.value() != 80:
            args.extend(["--margin-lr", str(self.margin_lr.value())])
        if self.extra_pad.value() != 0:
            args.extend(["--extra-pad", str(self.extra_pad.value())])

        if self.subtitle_offset.value() != 0.0:
            args.extend(["--subtitle-offset", str(self.subtitle_offset.value())])
        _check_add("--auto-offset", self.auto_offset)

        if self.crf.value() != 18:
            args.extend(["--crf", str(self.crf.value())])
        if self.preset.currentText() != "slow":
            args.extend(["--preset", self.preset.currentText()])
        _check_add("--no-audio-copy", self.no_audio_copy)

        hw = self.hwaccel.currentText()
        if hw == "Force hardware":
            args.append("--hwaccel")
        elif hw == "Force libx264":
            args.append("--no-hwaccel")

        _check_add("--keep-ass",    self.keep_ass)
        _check_add("--dry-run",     self.dry_run)
        _check_add("--list-styles", self.list_styles)

        return args


# ─────────────────────────────────────────────────────────────────────────────
# Main Window
# ─────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Video Maker")
        self.setMinimumSize(860, 700)

        self._process: QProcess | None = None

        # ── Tabs ──
        self.tab1 = Tab1()
        self.tab2 = Tab2()
        self.tab3 = Tab3()
        self.tab4 = Tab4()

        self.tabs = QTabWidget()
        self.tabs.addTab(self.tab1, "1 · Organize & Delogo")
        self.tabs.addTab(self.tab2, "2 · Lyrics Subtitles")
        self.tabs.addTab(self.tab3, "3 · Music Video")
        self.tabs.addTab(self.tab4, "4 · Burn Subtitles")

        # ── Run / Stop buttons ──
        self.run_btn = QPushButton("▶  Run Step")
        self.run_btn.setFixedHeight(36)
        self.run_btn.setStyleSheet(
            "QPushButton { background-color: #2ea44f; color: white; border-radius: 6px; font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { background-color: #3cbf5e; }"
            "QPushButton:disabled { background-color: #555; color: #aaa; }"
        )
        self.run_btn.clicked.connect(self._run)

        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.setFixedHeight(36)
        self.stop_btn.setFixedWidth(100)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet(
            "QPushButton { background-color: #b31d28; color: white; border-radius: 6px; font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { background-color: #cc2233; }"
            "QPushButton:disabled { background-color: #555; color: #aaa; }"
        )
        self.stop_btn.clicked.connect(self._stop)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.run_btn)
        btn_row.addWidget(self.stop_btn)

        # ── Console output ──
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(QFont("Courier New" if sys.platform.startswith("win") else "Menlo", 10))
        self.console.setStyleSheet(
            "QTextEdit { background-color: #1a1a2e; color: #e0e0e0; border-radius: 4px; padding: 6px; }"
        )
        self.console.setMinimumHeight(180)
        self.console.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        clr_btn = QPushButton("Clear")
        clr_btn.setFixedWidth(60)
        clr_btn.clicked.connect(self.console.clear)

        console_hdr = QHBoxLayout()
        console_hdr.addWidget(QLabel("Console output:"))
        console_hdr.addStretch()
        console_hdr.addWidget(clr_btn)

        # ── Splitter: top = tabs+buttons, bottom = console ──
        top_widget = QWidget()
        top_lay = QVBoxLayout(top_widget)
        top_lay.setContentsMargins(8, 8, 8, 4)
        top_lay.addWidget(self.tabs)
        top_lay.addLayout(btn_row)

        bottom_widget = QWidget()
        bottom_lay = QVBoxLayout(bottom_widget)
        bottom_lay.setContentsMargins(8, 0, 8, 8)
        bottom_lay.addLayout(console_hdr)
        bottom_lay.addWidget(self.console)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(top_widget)
        splitter.addWidget(bottom_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([480, 200])

        self.setCentralWidget(splitter)
        self._update_run_label()
        self.tabs.currentChanged.connect(self._update_run_label)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _update_run_label(self):
        idx = self.tabs.currentIndex() + 1
        self.run_btn.setText(f"▶  Run Step {idx}")

    def _current_tab(self):
        return [self.tab1, self.tab2, self.tab3, self.tab4][self.tabs.currentIndex()]

    def _append_console(self, text: str, color: str = "#e0e0e0"):
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.console.append(f'<span style="color:{color};">{escaped}</span>')

    # ── process management ───────────────────────────────────────────────────

    def _run(self):
        if self._process is not None:
            return

        tab = self._current_tab()
        if self.tabs.currentIndex() == 0:
            cmd = self.tab1.build_cmd()
        else:
            cmd = tab.build_cmd()

        self._append_console(f"\n$ {' '.join(cmd)}\n", "#7ec8e3")
        self.console.ensureCursorVisible()

        self._process = QProcess(self)
        self._process.setProgram(cmd[0])
        self._process.setArguments(cmd[1:])

        # Inherit current environment
        env = QProcessEnvironment.systemEnvironment()
        self._process.setProcessEnvironment(env)
        self._process.setWorkingDirectory(str(BASE_DIR))

        self._process.readyReadStandardOutput.connect(self._on_stdout)
        self._process.readyReadStandardError.connect(self._on_stderr)
        self._process.finished.connect(self._on_finished)

        self._process.start()
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def _stop(self):
        if self._process:
            self._process.kill()
            self._append_console("[Process killed by user]", "#e06c75")

    def _on_stdout(self):
        if self._process:
            raw = bytes(self._process.readAllStandardOutput())
            text = raw.decode("utf-8", errors="replace")
            self._append_console(text.rstrip())

    def _on_stderr(self):
        if self._process:
            raw = bytes(self._process.readAllStandardError())
            text = raw.decode("utf-8", errors="replace")
            self._append_console(text.rstrip(), "#e5c07b")

    def _on_finished(self, exit_code: int, exit_status):
        if self._process:
            color = "#98c379" if exit_code == 0 else "#e06c75"
            self._append_console(f"\n[Process finished with exit code {exit_code}]", color)
            self._process = None
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("AI Video Maker")

    # ── Palette: dark theme ──
    palette = QPalette()
    dark   = QColor("#1e1e2e")
    panel  = QColor("#2a2a3e")
    border = QColor("#3a3a5e")
    text   = QColor("#cdd6f4")
    accent = QColor("#89b4fa")
    palette.setColor(QPalette.Window,          dark)
    palette.setColor(QPalette.WindowText,      text)
    palette.setColor(QPalette.Base,            panel)
    palette.setColor(QPalette.AlternateBase,   dark)
    palette.setColor(QPalette.ToolTipBase,     dark)
    palette.setColor(QPalette.ToolTipText,     text)
    palette.setColor(QPalette.Text,            text)
    palette.setColor(QPalette.Button,          panel)
    palette.setColor(QPalette.ButtonText,      text)
    palette.setColor(QPalette.BrightText,      QColor("#f38ba8"))
    palette.setColor(QPalette.Highlight,       accent)
    palette.setColor(QPalette.HighlightedText, dark)
    palette.setColor(QPalette.Disabled, QPalette.Text,       border)
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, border)
    app.setPalette(palette)

    app.setStyleSheet("""
        QGroupBox {
            border: 1px solid #3a3a5e;
            border-radius: 6px;
            margin-top: 10px;
            font-weight: bold;
            color: #89b4fa;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
        }
        QTabBar::tab {
            background: #2a2a3e;
            color: #cdd6f4;
            padding: 8px 18px;
            border-top-left-radius: 5px;
            border-top-right-radius: 5px;
            min-width: 140px;
        }
        QTabBar::tab:selected {
            background: #313244;
            border-bottom: 2px solid #89b4fa;
            color: #cdd6f4;
        }
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
            background-color: #313244;
            border: 1px solid #45475a;
            border-radius: 4px;
            padding: 3px 6px;
            color: #cdd6f4;
        }
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
            border: 1px solid #89b4fa;
        }
        QScrollBar:vertical {
            background: #2a2a3e;
            width: 8px;
        }
        QScrollBar::handle:vertical {
            background: #45475a;
            border-radius: 4px;
        }
        QPushButton {
            background-color: #313244;
            color: #cdd6f4;
            border: 1px solid #45475a;
            border-radius: 4px;
            padding: 4px 10px;
        }
        QPushButton:hover {
            background-color: #45475a;
        }
        QCheckBox {
            color: #cdd6f4;
        }
        QLabel {
            color: #bac2de;
        }
        QSplitter::handle {
            background: #3a3a5e;
            height: 2px;
        }
    """)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
