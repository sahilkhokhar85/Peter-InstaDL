"""
FFmpeg media processing utilities for Instagram Downloader.
Provides functions for:
- Video dimensions inspection via ffprobe
- Audio extraction (MP3)
- Grayscale video filter
- Video rotation (90, 180, 270 degrees)
- Caption overlay on video (via ASS subtitles)
- Thumbnail frame extraction
"""
import os
import shutil
import subprocess
import logging
from typing import Tuple

logger = logging.getLogger("media_processor")

JULES_BIN = "/home/jules/bin"
if os.path.exists(JULES_BIN) and JULES_BIN not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{JULES_BIN}:{os.environ.get('PATH', '')}"


def get_ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if path:
        return path
    if os.path.exists(os.path.join(JULES_BIN, "ffmpeg")):
        return os.path.join(JULES_BIN, "ffmpeg")
    raise RuntimeError("FFmpeg executable not found on PATH.")


def get_ffprobe_path() -> str:
    path = shutil.which("ffprobe")
    if path:
        return path
    if os.path.exists(os.path.join(JULES_BIN, "ffprobe")):
        return os.path.join(JULES_BIN, "ffprobe")
    raise RuntimeError("FFprobe executable not found on PATH.")


def get_video_dimensions(input_path: str) -> Tuple[int, int]:
    """Inspect video width and height using ffprobe."""
    try:
        ffprobe_bin = get_ffprobe_path()
        cmd = [
            ffprobe_bin,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            input_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10.0)
        output = result.stdout.strip()
        if "x" in output:
            w_str, h_str = output.split("x", 1)
            w, h = int(w_str), int(h_str)
            if w > 0 and h > 0:
                return w, h
    except Exception as e:
        logger.warning(f"Failed to inspect video dimensions for {input_path}: {e}")
    return 640, 360


def run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess:
    """Run FFmpeg process with error handling and returncode verification."""
    ffmpeg_bin = get_ffmpeg_path()
    cmd = [ffmpeg_bin] + args
    logger.info(f"Running FFmpeg command: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        logger.error(f"FFmpeg command failed ({result.returncode}): {result.stderr}")
        raise RuntimeError(f"FFmpeg processing failed: {result.stderr[-300:] if result.stderr else 'Unknown error'}")
    return result


def extract_audio(input_path: str, output_path: str) -> str:
    """Extract audio from video file and export to MP3."""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input video file not found: {input_path}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    args = ["-y", "-i", input_path, "-vn", "-acodec", "libmp3lame", "-q:a", "2", output_path]
    run_ffmpeg(args)
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("Extracted MP3 file is empty or missing.")
    return output_path


def apply_grayscale(input_path: str, output_path: str) -> str:
    """Apply grayscale filter to video and encode to yuv420p for universal playback compatibility."""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input video file not found: {input_path}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    args = [
        "-y", "-i", input_path,
        "-vf", "format=gray,format=yuv420p",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
        "-c:a", "copy",
        output_path
    ]
    try:
        run_ffmpeg(args)
    except Exception as e:
        logger.warning(f"apply_grayscale with -c:a copy failed ({e}), retrying with AAC audio re-encoding...")
        args_aac = [
            "-y", "-i", input_path,
            "-vf", "format=gray,format=yuv420p",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
            "-c:a", "aac",
            output_path
        ]
        run_ffmpeg(args_aac)

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("Grayscale video file is empty or missing.")
    return output_path


def scale_video(input_path: str, output_path: str, target_height: int) -> str:
    """Rescale video resolution using FFmpeg filter matching aspect ratio and yuv420p encoding."""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input video file not found: {input_path}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    w, h = get_video_dimensions(input_path)
    if h > w:
        vf = f"scale={target_height}:-2"
    else:
        vf = f"scale=-2:{target_height}"

    args = [
        "-y", "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
        "-c:a", "copy",
        output_path
    ]
    try:
        run_ffmpeg(args)
    except Exception as e:
        logger.warning(f"scale_video with -c:a copy failed ({e}), retrying with AAC audio re-encoding...")
        args_aac = [
            "-y", "-i", input_path,
            "-vf", vf,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
            "-c:a", "aac",
            output_path
        ]
        run_ffmpeg(args_aac)

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(f"Scaled {target_height}p video file is empty or missing.")
    return output_path


def rotate_video(input_path: str, output_path: str, degrees: int) -> str:
    """Rotate video by 90, 180, or 270 degrees clockwise and encode to yuv420p."""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input video file not found: {input_path}")

    if degrees == 90:
        vf = "transpose=1"
    elif degrees == 180:
        vf = "transpose=1,transpose=1"
    elif degrees == 270:
        vf = "transpose=2"
    else:
        raise ValueError(f"Unsupported rotation angle: {degrees}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    args = [
        "-y", "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
        "-c:a", "copy",
        output_path
    ]
    try:
        run_ffmpeg(args)
    except Exception as e:
        logger.warning(f"rotate_video with -c:a copy failed ({e}), retrying with AAC audio re-encoding...")
        args_aac = [
            "-y", "-i", input_path,
            "-vf", vf,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
            "-c:a", "aac",
            output_path
        ]
        run_ffmpeg(args_aac)

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("Rotated video file is empty or missing.")
    return output_path


def add_caption_to_video(input_path: str, output_path: str, caption_text: str) -> str:
    """Overlay caption text on video using ASS subtitle generator."""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input video file not found: {input_path}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    ass_path = os.path.join(os.path.dirname(output_path), f"caption_{os.getpid()}.ass")

    clean_text = caption_text.replace("\n", "\\N").replace("{", "(").replace("}", ")")

    ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 640
PlayResY: 360

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, BackColour, Bold, Italic, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,18,&H00FFFFFF,&H80000000,-1,0,2,10,10,25,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:05:00.00,Default,,0000,0000,0000,,{clean_text}
"""
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    args = ["-y", "-i", input_path, "-vf", f"ass={ass_path}", "-preset", "fast", "-c:a", "copy", output_path]
    try:
        run_ffmpeg(args)
    finally:
        if os.path.exists(ass_path):
            try:
                os.remove(ass_path)
            except Exception:
                pass

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("Captioned video file is empty or missing.")
    return output_path


def extract_thumbnail(input_path: str, output_path: str, timestamp_sec: float = 1.0) -> str:
    """Extract a representative frame from video as JPEG thumbnail."""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input video file not found: {input_path}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    args = ["-y", "-ss", f"{timestamp_sec:.2f}", "-i", input_path, "-vframes", "1", output_path]
    try:
        run_ffmpeg(args)
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("Frame extraction at timestamp produced empty file.")
    except Exception as e:
        logger.warning(f"Thumbnail extraction at {timestamp_sec}s failed/empty ({e}), falling back to 00:00:00")
        args_fallback = ["-y", "-ss", "00:00:00", "-i", input_path, "-vframes", "1", output_path]
        run_ffmpeg(args_fallback)

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("Extracted thumbnail photo is empty or missing.")
    return output_path
