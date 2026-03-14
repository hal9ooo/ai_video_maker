import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

try:
    import cv2
except ImportError:
    cv2 = None

BASE_DIR = Path(__file__).parent
DEFAULT_INPUT_DIR = BASE_DIR / "input_delogo"
DEFAULT_REPO_DIR = BASE_DIR / "videorepo"
BOX_CONFIG_PATH = BASE_DIR / "delogo_boxes.json"

FFMPEG_PRESET = "medium"
FFMPEG_CRF = "23"

# Missing constants causing NameError
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
SKIP_PREFIXES = (".", "_")

DEFAULT_CONFIG = {
    "9x16": {"x": 980, "y": 1830, "w": 85, "h": 77},
    "16x9": None,
    "1x1": None,
    "portrait_default": None,
    "landscape_default": None,
}


@dataclass(frozen=True)
class DelogoBox:
    x: int
    y: int
    w: int
    h: int

    def filter(self, show: int = 0) -> str:
        return f"delogo=x={self.x}:y={self.y}:w={self.w}:h={self.h}:show={show}"

    def tag(self) -> str:
        return f"x{self.x}_y{self.y}_w{self.w}_h{self.h}"


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    width: int
    height: int
    form_factor: str


@dataclass(frozen=True)
class EncoderConfig:
    name: str
    pre_input_args: tuple[str, ...]
    vf_suffix: str
    encode_args: tuple[str, ...]


SELECTED_ENCODER: EncoderConfig | None = None


def run_cmd(cmd, cwd=None, capture_output=False):
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def encoder_cpu_libx264() -> EncoderConfig:
    return EncoderConfig(
        name="libx264",
        pre_input_args=(),
        vf_suffix="",
        encode_args=(
            "-c:v",
            "libx264",
            "-preset",
            FFMPEG_PRESET,
            "-crf",
            FFMPEG_CRF,
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
        ),
    )


def test_encoder(cfg: EncoderConfig) -> bool:
    vf = "scale=128:128" + cfg.vf_suffix
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        *cfg.pre_input_args,
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=128x128:rate=1",
        "-frames:v",
        "1",
        "-vf",
        vf,
        *cfg.encode_args,
        "-f",
        "null",
        "-",
    ]
    try:
        run_cmd(cmd, capture_output=True)
        return True
    except subprocess.CalledProcessError:
        return False


def detect_best_encoder() -> EncoderConfig:
    # On Windows, QSV is often the best/most stable if NVIDIA fails.
    # We already know NVIDIA failed in tests, so we prioritize QSV or libx264.
    candidates: list[EncoderConfig] = [
        EncoderConfig(
            name="h264_videotoolbox",
            pre_input_args=(),
            vf_suffix="",
            encode_args=(
                "-c:v",
                "h264_videotoolbox",
                "-q:v",
                "60",
                "-realtime",
                "true",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
            ),
        ),
        EncoderConfig(
            name="h264_amf",
            pre_input_args=(),
            vf_suffix="",
            encode_args=(
                "-c:v",
                "h264_amf",
                "-quality",
                "quality",
                "-rc",
                "vbr_peak",
                "-b:v",
                "8M",
                "-maxrate",
                "8M",
                "-bufsize",
                "16M",
                "-movflags",
                "+faststart",
            ),
        ),
        EncoderConfig(
            name="h264_qsv",
            pre_input_args=(),
            vf_suffix="",
            encode_args=(
                "-c:v",
                "h264_qsv",
                "-global_quality",
                FFMPEG_CRF,
                "-look_ahead",
                "0",
                "-movflags",
                "+faststart",
            ),
        ),
        EncoderConfig(
            name="h264_nvenc",
            pre_input_args=(),
            vf_suffix="",
            encode_args=(
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p5",
                "-cq",
                FFMPEG_CRF,
                "-b:v",
                "0",
                "-movflags",
                "+faststart",
            ),
        ),
    ]

    for cfg in candidates:
        if test_encoder(cfg):
            return cfg
    return encoder_cpu_libx264()


def probe_dimensions(video_path: Path) -> tuple[int, int]:
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
        str(video_path),
    ]
    result = run_cmd(cmd, capture_output=True)
    token = result.stdout.strip()
    if "x" not in token:
        raise ValueError(f"Cannot parse width/height for {video_path}")
    w, h = token.split("x", 1)
    return int(w), int(h)


def get_form_factor(width: int, height: int) -> str:
    ratio = width / height
    if abs(ratio - (16 / 9)) < 0.1:
        return "16x9"
    if abs(ratio - (9 / 16)) < 0.1:
        return "9x16"
    if abs(ratio - 1.0) < 0.1:
        return "1x1"
    if ratio > 1:
        return f"landscape_{width}x{height}"
    return f"portrait_{width}x{height}"


def clean_stem(stem: str) -> str:
    cleaned = re.sub(r"[_-](?:19|20)\d{6,13}$", "", stem)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_-")
    return cleaned or stem


def unique_output_path(output_dir: Path, stem: str, box: DelogoBox) -> Path:
    base = f"{stem}_nologo_{box.tag()}.mp4"
    out = output_dir / base
    if not out.exists():
        return out
    idx = 2
    while True:
        out = output_dir / f"{stem}_nologo_{box.tag()}_v{idx}.mp4"
        if not out.exists():
            return out
        idx += 1


def ensure_box_config():
    if BOX_CONFIG_PATH.exists():
        return
    BOX_CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
    print(f"Created config file: {BOX_CONFIG_PATH}")


def parse_box(value) -> DelogoBox | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    keys = ("x", "y", "w", "h")
    if not all(k in value for k in keys):
        return None
    try:
        box = DelogoBox(
            x=int(value["x"]),
            y=int(value["y"]),
            w=int(value["w"]),
            h=int(value["h"]),
        )
    except (TypeError, ValueError):
        return None
    if box.x < 0 or box.y < 0 or box.w <= 0 or box.h <= 0:
        return None
    return box


def load_box_config() -> dict[str, object]:
    ensure_box_config()
    with BOX_CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("delogo_boxes.json must contain a JSON object")
    return data


def get_configured_box(form_factor: str, cfg: dict[str, object]) -> DelogoBox | None:
    exact = parse_box(cfg.get(form_factor))
    if exact:
        return exact

    if form_factor.startswith("portrait_"):
        return parse_box(cfg.get("portrait_default"))
    if form_factor.startswith("landscape_"):
        return parse_box(cfg.get("landscape_default"))
    return None


def box_fits_video(box: DelogoBox, width: int, height: int) -> bool:
    return box.x + box.w <= width and box.y + box.h <= height


def apply_delogo(video_path: Path, output_path: Path, box: DelogoBox, show_logo_area: bool = False):
    if SELECTED_ENCODER is None:
        raise RuntimeError("Encoder not initialized")

    vf = box.filter(show=1 if show_logo_area else 0) + SELECTED_ENCODER.vf_suffix
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        *SELECTED_ENCODER.pre_input_args,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        vf,
        *SELECTED_ENCODER.encode_args,
        "-an",
        str(output_path),
    ]
    run_cmd(cmd, capture_output=True)


def save_debug_overlay_png(video_path: Path, box: DelogoBox, output_png: Path):
    if cv2 is None:
        vf = (
            f"select='eq(n,0)',"
            f"drawbox=x={box.x}:y={box.y}:w={box.w}:h={box.h}:color=red@0.95:t=3"
        )
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            vf,
            "-frames:v",
            "1",
            str(output_png),
        ]
        run_cmd(cmd, capture_output=True)
        return

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"[WARN] Could not open video for debug overlay: {video_path.name}")
        return

    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        print(f"[WARN] Could not read first frame for debug overlay: {video_path.name}")
        return

    x1 = max(0, box.x)
    y1 = max(0, box.y)
    x2 = min(frame.shape[1] - 1, box.x + box.w)
    y2 = min(frame.shape[0] - 1, box.y + box.h)

    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 3)
    label = f"Target: x={box.x}, y={box.y}, w={box.w}, h={box.h}"
    label_y = y1 - 10 if y1 > 20 else min(frame.shape[0] - 10, y2 + 25)
    cv2.putText(
        frame,
        label,
        (x1, label_y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 0, 255),
        2,
    )
    cv2.imwrite(str(output_png), frame)


def collect_videos(input_dir: Path) -> list[VideoInfo]:
    infos = []
    for file in sorted(input_dir.rglob("*")):
        if file.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        if file.name.startswith(SKIP_PREFIXES):
            continue
        # Exclude only the render output directory to avoid re-processing the final montage
        if "output_automated" in file.parts:
            continue
        try:
            w, h = probe_dimensions(file)
            ff = get_form_factor(w, h)
            infos.append(VideoInfo(path=file, width=w, height=h, form_factor=ff))
        except Exception as exc:
            print(f"[WARN] Skipping {file.name}: {exc}")
    return infos


def process_group(form_factor: str, videos: list[VideoInfo], cfg: dict[str, object], repo_dir: Path, show_logo_area: bool = False):
    output_dir = repo_dir / form_factor
    output_dir.mkdir(parents=True, exist_ok=True)

    box = get_configured_box(form_factor, cfg)
    if box is None:
        print(f"[WARN] No configured box for form factor {form_factor}. Skipping group.")
        return

    print(f"\n=== Processing group {form_factor} ({len(videos)} files) with box {box.tag()} ===")
    for info in videos:
        if not box_fits_video(box, info.width, info.height):
            print(
                f"[WARN] Box {box.tag()} out of bounds for {info.path.name} "
                f"({info.width}x{info.height}). Skipping file."
            )
            continue

        cleaned_stem = clean_stem(info.path.stem)
        output_path = unique_output_path(output_dir, cleaned_stem, box)
        debug_png_path = output_path.with_suffix(".png")
        print(f"  ffmpeg {info.path.name} -> {output_path.name}")
        try:
            save_debug_overlay_png(info.path, box, debug_png_path)
            apply_delogo(info.path, output_path, box, show_logo_area=show_logo_area)
        except subprocess.CalledProcessError as exc:
            print(f"[ERROR] Processing failed for {info.path.name}: {exc.stderr}")


def main():
    parser = argparse.ArgumentParser(description="Organize and delogo videos")
    parser.add_argument("--project", help="Project name (subfolder in input_delogo and videorepo)")
    parser.add_argument("--input-dir", help="Explicit input directory")
    parser.add_argument("--repo-dir", help="Explicit repo directory")
    parser.add_argument("--show-logo", action="store_true", help="Show logo area instead of removing it")
    args = parser.parse_args()

    input_dir = Path(args.input_dir).resolve() if args.input_dir else DEFAULT_INPUT_DIR
    repo_dir = Path(args.repo_dir).resolve() if args.repo_dir else DEFAULT_REPO_DIR

    if args.project:
        input_dir = input_dir / args.project
        repo_dir = repo_dir / args.project

    if not input_dir.exists():
        print(f"Input directory {input_dir} not found")
        return

    cfg = load_box_config()

    global SELECTED_ENCODER
    SELECTED_ENCODER = detect_best_encoder()
    print(f"Selected video encoder: {SELECTED_ENCODER.name}")
    print(f"Using config: {BOX_CONFIG_PATH}")
    print(f"Input dir: {input_dir}")
    print(f"Repo dir: {repo_dir}")

    videos = collect_videos(input_dir)
    if not videos:
        print(f"No input videos found in {input_dir}")
        return

    grouped: dict[str, list[VideoInfo]] = {}
    for info in videos:
        grouped.setdefault(info.form_factor, []).append(info)

    for form_factor in sorted(grouped.keys()):
        process_group(form_factor, grouped[form_factor], cfg, repo_dir, show_logo_area=args.show_logo)


if __name__ == "__main__":
    main()
