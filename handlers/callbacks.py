"""
Media action inline button callback query handlers.
"""
import os
import uuid
import shutil
import asyncio
import logging
from telegram import Update, LinkPreviewOptions
from telegram.ext import ContextTypes

from database import task_manager, session_manager, MediaSession
from utils.buttons import (
    build_video_action_keyboard,
    build_quality_keyboard,
    build_rotation_keyboard,
)
from utils.progress import MovingDotAnimation
from utils.downloader import ytdlp_download, fetch_instagram_cover
from utils import media_processor

logger = logging.getLogger("handlers.callbacks")

# Limit heavy FFmpeg tasks concurrency to 2 simultaneous jobs
FFMPEG_SEMAPHORE = asyncio.Semaphore(2)


async def media_action_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button actions on downloaded Instagram video."""
    query = update.callback_query
    if not query:
        return

    data = query.data or ""
    if not data.startswith("act_"):
        return

    parts = data.split("_", 2)
    if len(parts) < 3:
        return

    session_id = parts[1]
    action = parts[2]

    session = session_manager.get_session(session_id)
    if not session:
        await query.answer("⚠️ Session unavailable. Please send the link again.", show_alert=True)
        return

    if session.uid != query.from_user.id:
        await query.answer("⚠️ You are not authorized to control this media session.", show_alert=True)
        return

    # Handle Back & Remove & Submenus & Caption immediately without lock
    if action == "back":
        await query.answer()
        session.showing_rotation_menu = False
        session.showing_quality_menu = False
        kb = build_video_action_keyboard(session_id)
        try:
            await query.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            pass
        return

    elif action == "remove":
        await query.answer("✅ Controls removed")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        # Clean up session and associated source file on explicit remove
        try:
            session_manager.remove_session(session_id)
            if session.file_path and os.path.exists(session.file_path):
                os.remove(session.file_path)
                temp_dir = os.path.dirname(session.file_path)
                if os.path.exists(temp_dir) and not os.listdir(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception as remove_err:
            logger.warning(f"Error cleaning up session {session_id} on remove button click: {remove_err}")
        return

    elif action == "qmenu":
        await query.answer()
        session.showing_quality_menu = True
        session.showing_rotation_menu = False
        kb = build_quality_keyboard(session_id, session.available_qualities)
        try:
            await query.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            pass
        return

    elif action == "rotmenu":
        await query.answer()
        session.showing_rotation_menu = True
        session.showing_quality_menu = False
        kb = build_rotation_keyboard(session_id)
        try:
            await query.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            pass
        return

    elif action == "caption":
        raw_caption = (session.caption or "").strip()
        if raw_caption:
            formatted_caption = f"{raw_caption}\n\n💬By : @TDBotDevZ"
        else:
            formatted_caption = "no caption\n\n💬By : @TDBotDevZ"

        try:
            if not session.telegram_chat_id or not session.telegram_message_id:
                raise ValueError("No recorded Telegram message ID for this session.")

            kb = build_video_action_keyboard(session_id)
            await context.bot.edit_message_caption(
                chat_id=session.telegram_chat_id,
                message_id=session.telegram_message_id,
                caption=formatted_caption,
                reply_markup=kb,
            )
            await query.answer("✅ Caption updated")
        except Exception as e:
            logger.error(f"Failed to edit caption on Telegram message for session {session_id}: {e}")
            await query.answer("❌ Unable to update caption.\nPlease download the media again.", show_alert=True)
        return

    # Check lock for processing actions (Audio, Grayscale, Rotation choices, Thumbnail, Quality selections)
    with session.lock:
        if session.is_processing:
            await query.answer("⏳ Processing in progress...", show_alert=True)
            return
        session.is_processing = True

    await query.answer()

    # Process background action
    asyncio.create_task(run_media_action(update, query, session, action))


async def run_media_action(update: Update, query, session: MediaSession, action: str):
    """Execute heavy FFmpeg media action in background task with moving dot animation and lock release."""
    temp_dir = os.path.dirname(session.file_path) if session.file_path else "/tmp"
    base_name = os.path.splitext(os.path.basename(session.file_path))[0] if session.file_path else "media"
    out_file = None
    cover_info = None
    status_msg = None
    animator = None

    try:
        if action.startswith("qsel_") and session.url:
            target_q = action.replace("qsel_", "")
            status_msg = await query.message.reply_text(f"⬇️ Downloading {target_q}…")
            q_task_id = uuid.uuid4().hex[:8]
            q_temp_dir = os.path.join("/tmp", "ytdlp_downloads", q_task_id)

            task_manager.register_task(q_task_id, session.uid)
            try:
                dl_file, _, _ = await ytdlp_download(
                    url=session.url,
                    dest_dir=q_temp_dir,
                    task_id=q_task_id,
                    msg=status_msg,
                    uid=session.uid,
                    format_override=target_q,
                )
                out_file = dl_file
            finally:
                task_manager.remove_task(q_task_id)

            if not out_file or not os.path.exists(out_file):
                raise RuntimeError(f"Direct download for quality {target_q} failed.")

        else:
            # Ensure session.file_path exists; if cleaned up or after reboot, auto-redownload seamlessly
            if not session.file_path or not os.path.exists(session.file_path):
                if not session.url:
                    raise RuntimeError("Session source file missing and media URL unknown.")
                status_msg = await query.message.reply_text("⬇️ Restoring media file…")
                redl_task_id = uuid.uuid4().hex[:8]
                redl_dir = os.path.join("/tmp", "ytdlp_downloads", redl_task_id)
                task_manager.register_task(redl_task_id, session.uid)
                try:
                    restored_path, _, _ = await ytdlp_download(
                        url=session.url,
                        dest_dir=redl_dir,
                        task_id=redl_task_id,
                        msg=status_msg,
                        uid=session.uid,
                    )
                    session.file_path = restored_path
                    session_manager.save_session_state()
                    temp_dir = os.path.dirname(session.file_path)
                    base_name = os.path.splitext(os.path.basename(session.file_path))[0]
                finally:
                    task_manager.remove_task(redl_task_id)
                try:
                    await status_msg.delete()
                except Exception:
                    pass

            status_msg = await query.message.reply_text("•")
            animator = MovingDotAnimation(status_msg, interval=1.5)
            await animator.start()

            loop = asyncio.get_running_loop()

            async with FFMPEG_SEMAPHORE:
                if action == "audio":
                    out_file = os.path.join(temp_dir, f"audio_{uuid.uuid4().hex[:6]}.mp3")
                    await loop.run_in_executor(None, media_processor.extract_audio, session.file_path, out_file)

                    if not os.path.exists(out_file) or os.path.getsize(out_file) == 0:
                        raise RuntimeError("Audio extraction output file is missing or empty.")

                elif action == "grayscale":
                    out_file = os.path.join(temp_dir, f"gray_{uuid.uuid4().hex[:6]}.mp4")
                    await loop.run_in_executor(None, media_processor.apply_grayscale, session.file_path, out_file)

                    if not os.path.exists(out_file) or os.path.getsize(out_file) == 0:
                        raise RuntimeError("Grayscale video output file is missing or empty.")

                elif action.startswith("qsel_"):
                    target_q = action.replace("qsel_", "")
                    try:
                        target_height = int(target_q.replace("p", ""))
                    except ValueError:
                        target_height = 720

                    out_file = os.path.join(temp_dir, f"quality_{target_q}_{uuid.uuid4().hex[:6]}.mp4")
                    await loop.run_in_executor(None, media_processor.scale_video, session.file_path, out_file, target_height)

                    if not os.path.exists(out_file) or os.path.getsize(out_file) == 0:
                        raise RuntimeError(f"Quality scaling to {target_q} output file is missing or empty.")

                elif action in ("rot90", "rot180", "rot270"):
                    deg = 90 if action == "rot90" else (180 if action == "rot180" else 270)
                    out_file = os.path.join(temp_dir, f"rot{deg}_{uuid.uuid4().hex[:6]}.mp4")
                    await loop.run_in_executor(None, media_processor.rotate_video, session.file_path, out_file, deg)

                    if not os.path.exists(out_file) or os.path.getsize(out_file) == 0:
                        raise RuntimeError("Rotated video output file is missing or empty.")

                elif action == "thumb":
                    out_file = os.path.join(temp_dir, f"thumb_{uuid.uuid4().hex[:6]}.jpg")
                    got_cover = False
                    if session.url:
                        try:
                            cover_info = await loop.run_in_executor(None, fetch_instagram_cover, session.url, out_file, session.uid)
                            got_cover = os.path.exists(out_file) and os.path.getsize(out_file) > 0
                        except Exception as cover_err:
                            logger.warning(f"Real cover fetch failed ({cover_err}); falling back to video frame.")
                    if not got_cover:
                        await loop.run_in_executor(None, media_processor.extract_thumbnail, session.file_path, out_file)

                    if not os.path.exists(out_file) or os.path.getsize(out_file) == 0:
                        raise RuntimeError("Thumbnail output file is missing or empty.")

        # Processing completed successfully; transition status to 📤 Uploading… before sending to Telegram
        if animator:
            await animator.stop()
            animator = None

        if status_msg:
            try:
                await status_msg.edit_text("📤 Uploading…")
            except Exception:
                pass

        # Upload processed file to Telegram
        try:
            with open(out_file, "rb") as f:
                if action == "audio":
                    await query.message.reply_audio(
                        audio=f,
                        title=base_name[:40],
                        filename=f"{base_name[:30]}.mp3",
                    )
                elif action == "thumb":
                    await query.message.reply_photo(
                        photo=f,
                    )
                    if cover_info:
                        w, h = cover_info.get("width"), cover_info.get("height")
                        size_txt = f"{w}x{h}" if w and h else "unknown"
                        kb = cover_info.get("bytes", 0) / 1024
                        lines = [
                            "🖼 Cover source (Instagram)",
                            f"Resolution: {size_txt}",
                            f"Original size: {kb:.0f} KB",
                            "",
                            "Selected URL (highest quality):",
                            cover_info["url"],
                        ]
                        others = cover_info.get("others") or []
                        if others:
                            lines += ["", "Other available sizes:"]
                            for ow, oh, ourl in others[:3]:
                                lines.append(f"{ow}x{oh}: {ourl}")
                        info_text = "\n".join(lines)[:4000]
                        await query.message.reply_text(
                            info_text,
                            link_preview_options=LinkPreviewOptions(is_disabled=True),
                        )
                else:
                    await query.message.reply_video(
                        video=f,
                    )
        except Exception as upload_err:
            logger.error(f"Telegram upload failed for action '{action}' session {session.session_id}: {upload_err}")
            if status_msg:
                try:
                    await status_msg.edit_text("❌ Uploading media to Telegram failed. Please try again.")
                except Exception:
                    pass

        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass
            status_msg = None

    except Exception as e:
        logger.error(f"Error performing media action '{action}' on session {session.session_id}: {e}", exc_info=True)
        err_txt = "❌ Processing failed."
        if status_msg:
            try:
                await status_msg.edit_text(err_txt)
            except Exception:
                pass

    finally:
        if animator:
            try:
                await animator.stop()
            except Exception:
                pass
        session.is_processing = False
        if out_file and os.path.exists(out_file):
            try:
                os.remove(out_file)
            except Exception:
                pass
        if action.startswith("qsel_") and out_file:
            dl_dir = os.path.dirname(out_file)
            if dl_dir != temp_dir and os.path.exists(dl_dir):
                shutil.rmtree(dl_dir, ignore_errors=True)
