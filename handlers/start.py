"""
Start command, auto-registration, and Instagram media link processing handler.
"""
import re
import asyncio
import logging
import os
import uuid
import html
import shutil
from telegram import Update
from telegram.ext import ContextTypes

import config
from database import task_manager, session_manager, users_db
from utils.buttons import build_video_action_keyboard, build_start_keyboard, build_start_back_keyboard
from utils.downloader import ytdlp_download
from .force_sub import check_force_sub

logger = logging.getLogger("handlers.start")

# Instagram & YouTube URL regex
MEDIA_URL_REGEX = re.compile(
    r"https?://(?:www\.)?(?:"
    r"instagram\.com/(?:p|reel|reels|tv|stories)/[-a-zA-Z0-9_.]+/?"
    r"|(?:youtube\.com/(?:watch\?v=|shorts/|embed/|v/)|youtu\.be/)[-a-zA-Z0-9_]+"
    r")"
)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".m4v", ".ts"}
TELEGRAM_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

HELP_TEXT = (
    "<b>❓ How to Use</b>\n\n"
    "<blockquote>\n"
    "1. Send an Instagram Reel, Post, Video, or Story link.\n"
    "2. The bot will fetch the available media information.\n"
    "3. Select the required quality or download option.\n"
    "4. The bot will process and send the media.\n"
    "</blockquote>"
)

ABOUT_TEXT = (
    "<b>ℹ️ About</b>\n\n"
    "<blockquote>\n"
    "A simple and powerful Instagram media downloader designed to make downloading fast and easy.\n"
    "</blockquote>\n\n"
    "⚡ <b>Simple</b> • <b>Fast</b> • <b>Powerful</b>\n\n"
    "<blockquote>🌩️ Provided by <a href=\"https://t.me/RudraBlaze\">@RudraBlaze</a></blockquote>"
)


def get_start_text(first_name: str) -> str:
    safe_name = html.escape(first_name or "User")
    return (
        f"<b>🤖 Hello, {safe_name}!</b>\n\n"
        f"<b>✈️ How to use:</b>\n\n"
        f"<blockquote>Send an Instagram Reel, Post, Video, or Story link and the bot will automatically process the content and provide the available download options.</blockquote>\n\n"
        f"⚡ <b>Simple</b> • <b>Fast</b> • <b>Powerful</b>\n\n"
        f"<blockquote>🌩️ Provided by <a href=\"https://t.me/RudraBlaze\">@RudraBlaze</a></blockquote>"
    )


def extract_media_url(text: str) -> str | None:
    if not text:
        return None
    match = MEDIA_URL_REGEX.search(text.strip())
    return match.group(0) if match else None


def extract_instagram_url(text: str) -> str | None:
    return extract_media_url(text)


def auto_registration(uid: int):
    """Automatically ensure user preferences are registered upon interaction."""
    if uid:
        _ = users_db.get_settings(uid)


async def send_start_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send normal start message with START_IMAGE photo or text and main keyboard."""
    user = update.effective_user
    uid = user.id if user else 0
    auto_registration(uid)

    first_name = user.first_name if user else ""
    start_text = get_start_text(first_name)
    start_image = config.START_IMAGE
    kb = build_start_keyboard()

    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return

    if start_image:
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=start_image,
                caption=start_text,
                reply_markup=kb,
                parse_mode="HTML",
            )
            return
        except Exception as e:
            logger.warning(f"Failed to send START_IMAGE photo ({e}), falling back to text message.")

    await context.bot.send_message(
        chat_id=chat_id,
        text=start_text,
        reply_markup=kb,
        parse_mode="HTML",
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command with START_TEXT and safe START_IMAGE fallback."""
    if not update.effective_message:
        return

    if not await check_force_sub(update, context):
        return

    await send_start_screen(update, context)


async def start_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries from /start main menu buttons (Help, About, Back)."""
    query = update.callback_query
    if not query:
        return

    await query.answer()
    data = query.data or ""
    first_name = query.from_user.first_name if query.from_user else ""

    if data == "start_help":
        kb = build_start_back_keyboard()
        try:
            if query.message.caption:
                await query.edit_message_caption(caption=HELP_TEXT, reply_markup=kb, parse_mode="HTML")
            else:
                await query.edit_message_text(text=HELP_TEXT, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass

    elif data == "start_about":
        kb = build_start_back_keyboard()
        try:
            if query.message.caption:
                await query.edit_message_caption(caption=ABOUT_TEXT, reply_markup=kb, parse_mode="HTML")
            else:
                await query.edit_message_text(text=ABOUT_TEXT, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass

    elif data == "start_main":
        start_text = get_start_text(first_name)
        kb = build_start_keyboard()
        try:
            if query.message.caption:
                await query.edit_message_caption(caption=start_text, reply_markup=kb, parse_mode="HTML")
            else:
                await query.edit_message_text(text=start_text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cancel command."""
    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return

    session_manager.clear_user_states(uid)
    task_id = task_manager.cancel_user_task(uid)
    if task_id:
        await update.effective_message.reply_text("🛑 Cancelling active download task...", parse_mode="Markdown")
    else:
        await update.effective_message.reply_text("ℹ️ Operation cancelled.")


async def handle_url_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages: process Instagram and YouTube URLs."""
    if not update.effective_message or not update.effective_message.text:
        return

    if not await check_force_sub(update, context):
        return

    text = update.effective_message.text.strip()
    uid = update.effective_user.id if update.effective_user else 0
    auto_registration(uid)

    url = extract_media_url(text)

    if not url:
        await update.effective_message.reply_text(
            "⚠️ Please send a valid Instagram or YouTube link."
        )
        return

    if uid and uid in task_manager.user_active_tasks:
        await update.effective_message.reply_text(
            "⚠️ You already have an active download task. Use `/cancel` to stop it first.",
            parse_mode="Markdown",
        )
        return

    status_msg = await update.effective_message.reply_text("🔍 Fetching media info…")

    user_mention = update.effective_user.mention_markdown() if update.effective_user else "User"
    asyncio.create_task(process_download(url, update, status_msg, uid, user_mention))


async def process_download(
    url: str,
    update: Update,
    status_msg,
    uid: int,
    user_mention: str,
):
    """Run Instagram media download workflow and send result."""
    task_id = uuid.uuid4().hex[:12]
    temp_dir = os.path.join("/tmp", "ytdlp_downloads", task_id)
    os.makedirs(temp_dir, exist_ok=True)

    task_manager.register_task(task_id, uid, user_mention)

    user_settings = users_db.get_settings(uid) if uid else {}
    mode = user_settings.get("download_mode", "manual")
    def_q = user_settings.get("default_quality", "best")

    try:
        fmt_override = def_q if (mode == "fixed" and def_q != "best") else None

        try:
            q_label = f" {def_q}" if fmt_override else ""
            await status_msg.edit_text(f"⬇️ Downloading media{q_label}…")
        except Exception:
            pass

        file_path, caption_text, available_qualities = await ytdlp_download(
            url=url,
            dest_dir=temp_dir,
            task_id=task_id,
            msg=status_msg,
            uid=uid,
            format_override=fmt_override,
        )

        if task_manager.is_cancelled(task_id):
            raise asyncio.CancelledError()

        if not file_path or not os.path.exists(file_path):
            raise FileNotFoundError(f"Downloaded file not found for task {task_id}")

        file_size = os.path.getsize(file_path)
        ext = os.path.splitext(file_path)[1].lower()

        if file_size > TELEGRAM_MAX_FILE_SIZE:
            size_mb = file_size / (1024 * 1024)
            await status_msg.edit_text(
                f"⚠️ *File too large for Telegram upload*\n\n"
                f"💾 *Size:* `{size_mb:.2f} MB` (Limit: 50 MB)",
                parse_mode="Markdown",
            )
            return

        try:
            await status_msg.edit_text("📤 Preparing media…")
        except Exception:
            pass

        session_id = uuid.uuid4().hex[:8]
        media_type = "video" if ext in VIDEO_EXTENSIONS else "photo"
        session = session_manager.create_session(
            session_id, uid, file_path, media_type, caption=caption_text, available_qualities=available_qualities, url=url
        )

        with open(file_path, "rb") as media_file:
            if media_type == "video":
                kb = build_video_action_keyboard(session_id)
                sent_msg = None
                try:
                    sent_msg = await update.effective_message.reply_video(
                        video=media_file,
                        reply_markup=kb,
                    )
                except Exception as ve:
                    logger.warning(f"reply_video failed, falling back to reply_document: {ve}")
                    media_file.seek(0)
                    sent_msg = await update.effective_message.reply_document(
                        document=media_file,
                        reply_markup=kb,
                    )
                if sent_msg:
                    session.telegram_chat_id = sent_msg.chat_id
                    session.telegram_message_id = sent_msg.message_id
            else:
                await update.effective_message.reply_photo(
                    photo=media_file,
                )
        session_manager.save_session_state()

        try:
            await status_msg.delete()
        except Exception:
            pass

        logger.info(f"Created persistent MediaSession {session_id} for user {uid} at {file_path}")

    except asyncio.CancelledError:
        logger.info(f"Task {task_id} was cancelled by user {uid}")
        try:
            await status_msg.edit_text("🛑 *Download cancelled.*", parse_mode="Markdown")
        except Exception:
            pass
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

    except Exception as e:
        logger.error(f"Download failed for task {task_id} (URL: {url}): {e}", exc_info=True)
        try:
            await status_msg.edit_text(
                "❌ Unable to download this media.\n\n"
                "Please check that:\n"
                "• The link is valid\n"
                "• The post/reel/video is accessible\n"
                "• The media is not unavailable or private"
            )
        except Exception:
            pass
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

    finally:
        task_manager.remove_task(task_id)
