import os
import shutil
import unittest
import subprocess
from utils.media_processor import apply_grayscale, get_ffmpeg_path


class TestGrayscale(unittest.TestCase):

    def setUp(self):
        self.test_dir = "/tmp/test_grayscale_dir"
        os.makedirs(self.test_dir, exist_ok=True)
        self.color_video = os.path.join(self.test_dir, "color_input.mp4")
        self.gray_video = os.path.join(self.test_dir, "gray_output.mp4")

        # Generate a 1-second color test video with audio using FFmpeg
        ffmpeg_bin = get_ffmpeg_path()
        cmd = [
            ffmpeg_bin,
            "-y",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            self.color_video
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_apply_grayscale_creates_valid_bw_video(self):
        self.assertTrue(os.path.exists(self.color_video))

        out_path = apply_grayscale(self.color_video, self.gray_video)

        self.assertEqual(out_path, self.gray_video)
        self.assertTrue(os.path.exists(self.gray_video))
        self.assertGreater(os.path.getsize(self.gray_video), 0)

        # Inspect that output video has audio stream and valid video stream using ffprobe
        ffprobe_bin = shutil.which("ffprobe") or "ffprobe"
        cmd = [
            ffprobe_bin,
            "-v", "error",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            self.gray_video
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        streams = [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]
        self.assertIn("video", streams)
        self.assertIn("audio", streams)

        # Extract frame in PPM (RGB) format and verify that R, G, B channels are equal (black & white)
        ffmpeg_bin = get_ffmpeg_path()
        cmd_ppm = [
            ffmpeg_bin,
            "-ss", "0.5",
            "-i", self.gray_video,
            "-vframes", "1",
            "-f", "image2pipe",
            "-vcodec", "ppm",
            "-"
        ]
        ppm_result = subprocess.run(cmd_ppm, capture_output=True, check=True)
        raw_bytes = ppm_result.stdout

        # Header: P6\n320 240\n255\n
        header_end = raw_bytes.find(b"\n255\n")
        self.assertNotEqual(header_end, -1)
        pixel_bytes = raw_bytes[header_end + 5:]

        # Sample 100 pixels and verify R, G, B differences are near zero (grayscale)
        max_diff = 0
        for i in range(0, min(len(pixel_bytes) - 3, 300), 3):
            r, g, b = pixel_bytes[i], pixel_bytes[i+1], pixel_bytes[i+2]
            diff = max(abs(r - g), abs(g - b), abs(r - b))
            if diff > max_diff:
                max_diff = diff

        # In a true grayscale converted video, max RGB channel diff is <= 2 (due to 8-bit YUV rounding)
        self.assertLessEqual(max_diff, 2, f"Expected black & white image with near-zero R,G,B diff, got max diff {max_diff}")


if __name__ == "__main__":
    unittest.main()
