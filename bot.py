"""
Main entrypoint for Telegram Bot.
"""
import os
import sys
import time
import shutil
import logging
import threading
from telegram import BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import config
from app import run_flask
from database import session_manager
from utils.cookie_settings import settings_manager, GLOBAL_COOKIE_FILE
from utils import media_processor
from handlers import (
    start_command,
    cancel_command,
    handle_url_message,
    settings_command,
    user_settings_callback_handler,
    media_action_callback_handler,
    bsettings_command,
    settings_callback_handler,
    handle_document_upload,
    forcesub_callback_handler,
    start_callback_handler,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("instagram_bot")

TEMPORARY_FILES_CLEANUP_QUEUE = []


def check_ffmpeg() -> bool:
    """Check if FFmpeg binary is available on system PATH or local bin."""
    try:
        path = media_processor.get_ffmpeg_path()
        logger.info(f"FFmpeg detected at {path}")
        return True
    except RuntimeError:
        logger.warning("FFmpeg not found on PATH.")
        return False


def setup_global_cookies():
    """Reconstruct global cookie file from environment variables if provided."""
    cookie_content = os.getenv("INSTAGRAM_COOKIES") or os.getenv("COOKIES_TEXT")
    if cookie_content and cookie_content.strip():
        try:
            target_dir = os.path.dirname(GLOBAL_COOKIE_FILE)
            if target_dir:
                os.makedirs(target_dir, exist_ok=True)

            with open(GLOBAL_COOKIE_FILE, "w", encoding="utf-8") as f:
                f.write(cookie_content.strip() + "\n")

            os.chmod(GLOBAL_COOKIE_FILE, 0o600)
            file_size = os.path.getsize(GLOBAL_COOKIE_FILE)
            logger.info(f"Global cookie file successfully reconstructed at '{GLOBAL_COOKIE_FILE}' ({file_size} bytes, permissions: 0600).")
        except Exception as e:
            logger.error(f"Failed to create global cookie file at '{GLOBAL_COOKIE_FILE}': {e}")
    elif os.path.exists(GLOBAL_COOKIE_FILE):
        file_size = os.path.getsize(GLOBAL_COOKIE_FILE)
        logger.info(f"Existing global cookie file detected at '{GLOBAL_COOKIE_FILE}' ({file_size} bytes).")
    else:
        logger.info("No global cookies provided in environment (INSTAGRAM_COOKIES / COOKIES_TEXT).")


def validate_startup() -> tuple[str, int]:
    """Validate environment variables and system dependencies at startup."""
    token = config.BOT_TOKEN
    if not token:
        logger.error("BOT_TOKEN environment variable is not set!")
        print("ERROR: BOT_TOKEN environment variable is missing.")
        sys.exit(1)

    if not check_ffmpeg():
        logger.error("FFmpeg is missing!")
        print("ERROR: FFmpeg binary not found.")
        sys.exit(1)

    port_env = os.getenv("PORT", "8080")
    try:
        port = int(port_env)
        if not (1 <= port <= 65535):
            raise ValueError(f"Port number {port} out of range (1-65535)")
    except ValueError as e:
        logger.error(f"Invalid PORT environment variable '{port_env}': {e}")
        print(f"ERROR: Invalid PORT environment variable '{port_env}': {e}")
        sys.exit(1)

    setup_global_cookies()

    return token, port


def background_cleanup_worker():
    """Background task to remove expired orphan temporary files NOT attached to active MediaSessions."""
    while True:
        now = time.time()
        expired = []
        for item in list(TEMPORARY_FILES_CLEANUP_QUEUE):
            expire_time = item[0]
            if now >= expire_time:
                expired.append(item)

        for item in expired:
            expire_time, file_path, temp_dir, sid = item
            try:
                if sid and session_manager.get_session(sid):
                    logger.debug(f"Skipping cleanup for active MediaSession {sid}")
                    if item in TEMPORARY_FILES_CLEANUP_QUEUE:
                        TEMPORARY_FILES_CLEANUP_QUEUE.remove(item)
                    continue

                if os.path.exists(file_path):
                    os.remove(file_path)
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                logger.info(f"Cleaned up expired orphan temporary file: {file_path}")
            except Exception as e:
                logger.error(f"Failed to cleanup expired file '{file_path}': {e}")

            if item in TEMPORARY_FILES_CLEANUP_QUEUE:
                TEMPORARY_FILES_CLEANUP_QUEUE.remove(item)

        time.sleep(30)


async def post_init(application: Application):
    """Auto-register commands with Telegram Bot API on bot startup."""
    try:
        commands = [BotCommand(cmd, desc) for cmd, desc in config.BOT_COMMANDS]
        await application.bot.set_my_commands(commands)
        logger.info(f"Successfully auto-registered {len(commands)} commands with Telegram API.")
    except Exception as e:
        logger.error(f"Failed to auto-register bot commands: {e}")


def main():
    token, port = validate_startup()

    logger.info("Instagram Bot initialization started")
    logger.info(f"Starting Flask health server on port {port}...")

    flask_thread = threading.Thread(target=run_flask, args=(port,), daemon=True)
    flask_thread.start()

    cleanup_thread = threading.Thread(target=background_cleanup_worker, daemon=True)
    cleanup_thread.start()

    app = Application.builder().token(token).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("bSettings", bsettings_command))
    app.add_handler(CommandHandler("bsettings", bsettings_command))

    app.add_handler(CallbackQueryHandler(forcesub_callback_handler, pattern="^forcesub_check$"))
    app.add_handler(CallbackQueryHandler(start_callback_handler, pattern="^start_"))
    app.add_handler(CallbackQueryHandler(user_settings_callback_handler, pattern="^usrset_"))
    app.add_handler(CallbackQueryHandler(settings_callback_handler, pattern="^settings_"))
    app.add_handler(CallbackQueryHandler(media_action_callback_handler, pattern="^act_"))

    app.add_handler(MessageHandler(filters.Document.ALL, handle_document_upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url_message))

    logger.info("Instagram bot polling starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
