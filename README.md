# Instagram Telegram Downloader Bot

A complete, standalone Telegram bot that downloads Instagram Reels, Posts, IGTV, and Stories using `yt-dlp`. Features a built-in Flask health check server for cloud deployment platforms like Render.

## Features
- Support for Instagram Reels, Posts, IGTV, and Stories
- Live download progress updates (speed, ETA, size, percentage)
- Dynamic quality resolution buttons (`1080p`, `720p`, `480p`, `Audio`) based strictly on available media formats
- Multi-user task isolation and concurrent download safety
- Per-user download task cancellation via `/cancel`
- Per-user preferences via `/settings` (default video quality, download mode, cleanup timer)
- Document-only upload policy (`reply_document`) to prevent Telegram media compression
- Automatic temporary file cleanup worker
- 50 MB Telegram Bot API upload limit protection
- Built-in Flask health check server (`GET /` and `GET /health`)
- Global Netscape Instagram cookies authentication support for cloud & VPS deployments

## Environment Variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `BOT_TOKEN` | **Yes** | - | Telegram Bot API token obtained from @BotFather |
| `OWNER_ID` | No | `0` | Telegram User ID of the bot owner |
| `PORT` | No | `8080` | Port for the Flask health server |
| `INSTAGRAM_COOKIES` | No | - | Netscape format cookies text string for Instagram authentication |
| `COOKIES_FILE` | No | `/tmp/cookies.txt` | Path to global cookies file |

Copy `.env.example` to `.env` to set your environment variables:
```bash
cp .env.example .env
```

---

## Instagram Cookies Configuration (Render & Cloud VPS)

Instagram enforces login-gating on certain posts/reels and stories. To resolve this:

1. **Export Instagram Cookies:**
   - Use a browser extension (e.g., *Get cookies.txt LOCALLY*) while logged into Instagram to export your cookies in **Netscape format**.
2. **Configure Environment Variable:**
   - On **Render Dashboard**: Go to Environment -> Add Environment Variable `INSTAGRAM_COOKIES` and paste the entire content of the exported `cookies.txt` file as a multi-line string value.
   - On **VPS**: Set `INSTAGRAM_COOKIES` in your `.env` file or export it in your shell environment.
3. **Runtime Reconstruction:**
   - At startup, the bot detects `INSTAGRAM_COOKIES`, automatically reconstructs `/tmp/cookies.txt` with strict `0600` permissions, and passes it to `yt-dlp`.

---

## Deployment Options

### 1. Render Docker Deployment

Render deploys directly using the included `Dockerfile` and `render.yaml`.

1. Push your repository to GitHub / GitLab.
2. In Render Dashboard, click **New +** -> **Blueprints**.
3. Connect your repository. Render will automatically detect `render.yaml`.
4. Fill in the required environment variable: `BOT_TOKEN` (and optionally `OWNER_ID` and `INSTAGRAM_COOKIES`).
5. Render will build the Docker container (which includes FFmpeg) and run the service.
6. The Flask health check server runs automatically on `$PORT` (default `8080`), responding to `GET /health` with HTTP 200 `{"status": "ok"}`.

### 2. VPS Docker Deployment (Ubuntu/Debian)

Run the bot inside a Docker container on any VPS with Docker installed:

1. Build the Docker image:
   ```bash
   docker build -t instagram-telegram-bot .
   ```

2. Run the Docker container passing environment variables:
   ```bash
   docker run -d \
     --name instagram-bot \
     --restart unless-stopped \
     -p 8080:8080 \
     -e BOT_TOKEN="your_telegram_bot_token" \
     -e OWNER_ID="123456789" \
     -e PORT=8080 \
     -e INSTAGRAM_COOKIES="$(cat cookies.txt)" \
     instagram-telegram-bot
   ```

3. Verify health check:
   ```bash
   curl http://localhost:8080/health
   # Response: {"status":"ok"}
   ```

### 3. VPS Non-Docker Deployment (systemd)

Run directly on Ubuntu/Debian host machine without Docker:

1. Install system prerequisites (Python 3.10+ and FFmpeg):
   ```bash
   sudo apt-get update && sudo apt-get install -y python3 python3-pip ffmpeg
   ```

2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Setup environment file:
   ```bash
   cp .env.example .env
   # Edit .env and set BOT_TOKEN="your_telegram_bot_token" and INSTAGRAM_COOKIES="..."
   ```

4. Test run using `run.sh`:
   ```bash
   export BOT_TOKEN="your_telegram_bot_token"
   ./run.sh
   ```

5. Install systemd service for automatic restarts:
   ```bash
   sudo cp ytdlp-telegram-bot.service /etc/systemd/system/instagram-telegram-bot.service
   ```
   Edit `/etc/systemd/system/instagram-telegram-bot.service` to set your system username, working directory, and path to `.env`.

   Enable and start the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable instagram-telegram-bot
   sudo systemctl start instagram-telegram-bot
   ```

---

## System Requirements & Technical Notes

- **FFmpeg Requirement:** FFmpeg is required for video merging, audio conversion, and stream processing. The included Dockerfile automatically installs FFmpeg. For non-Docker VPS setups, system `ffmpeg` must be installed on `PATH`.
- **50 MB Upload Protection:** Standard Telegram Bot API limits file uploads to 50 MB. Files exceeding 50 MB will be safely rejected before upload with a clear notification to the user.
- **Temporary Storage Cleanup:** Downloads are stored in temporary task directories `/tmp/ytdlp_downloads/<task_id>` and automatically cleaned up after upload or failure.
