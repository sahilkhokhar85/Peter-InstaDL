#!/usr/bin/env bash
set -e

echo "==> Validating runtime environment..."

# Check Python 3
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 is not installed or not in PATH."
    exit 1
fi

echo "Python version: $(python3 --version)"

# Check FFmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "ERROR: ffmpeg is not installed or not in PATH."
    exit 1
fi

echo "FFmpeg version: $(ffmpeg -version | head -n 1)"

# Check required environment variables
if [ -z "$BOT_TOKEN" ]; then
    echo "ERROR: BOT_TOKEN environment variable is not set."
    exit 1
fi

echo "==> Environment check passed."
echo "==> Starting yt-dlp Telegram bot..."

exec python3 bot.py
