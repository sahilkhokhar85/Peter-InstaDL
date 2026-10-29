"""
Media action inline button callback query handlers.
"""
import os
import uuid
import html
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
    build_cover_keyboard,
)
from utils.progress import MovingDotAnimation
from utils.downloader import ytdlp_download, get_cover_options, download_cover_image
from utils import media_processor

logger = logging.getLogger("handlers.callbacks")

# Limit heavy FFmpeg tasks concurrency to 2 simultaneous jobs
FFMPEG_SEMAPHORE = asyncio.Semaphore(2)


CAPTION_SUFFIX = "\n\n💬By : @RudraBlaze"
CAPTION_LIMIT = 1024  # Telegram caption limit (visible characters)


def build_bold_caption(raw_caption: str) -> str:
    """Return the bold HTML caption (original caption + credit line), within Telegram's limit."""
    raw = (raw_caption or "").strip() or "no caption"
    max_raw = CAPTION_LIMIT - len(CAPTION_SUFFIX)
    if len(raw) > max_raw:
        raw = raw[: max_raw - 1].rstrip() + "…"
    return f"<b>{html.escape(raw)}{html.escape(CAPTION_SUFFIX)}</b>"


def message_has_caption(message) -> bool:
    """True when the video message currently shows a caption (i.e. Caption button was used)."""
    return bool(getattr(message, "caption", None))


def make_telegram_thumb(src: str, dst: str) -> str:
    """Create a Telegram-compliant video thumbnail: JPEG, max 320x320, under 200 KB."""
    for quality in (4, 8, 14, 20, 28):
        media_processor.run_ffmpeg([
            "-y", "-i", src,
            "-vf", "scale=320:320:force_original_aspect_ratio=decrease",
            "-frames:v", "1", "-q:v", str(quality), dst,
        ])
        if os.path.exists(dst) and 0 < os.path.getsize(dst) < 200 * 1024:
            return dst
    raise RuntimeError("Could not create a thumbnail under 200 KB.")


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
        if message_has_caption(query.message):
            await query.answer("✅ Caption already added")
            return

        formatted_caption = build_bold_caption(session.caption)

        try:
            if not session.telegram_chat_id or not session.telegram_message_id:
                raise ValueError("No recorded Telegram message ID for this session.")

            kb = build_video_action_keyboard(session_id)
            await context.bot.edit_message_caption(
                chat_id=session.telegram_chat_id,
                message_id=session.telegram_message_id,
                caption=formatted_caption,
                parse_mode="HTML",
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

    # Send the video again with a custom thumbnail / cover
    if action in ("sendthumb", "sendcover"):
        with_caption = message_has_caption(query.message)
        asyncio.create_task(run_send_with_action(query, session, action, with_caption))
        return

    # Cover actions (thumbnail + quality buttons) use their own flow
    if action == "thumb" or (action.startswith("cvq") and action[3:].isdigit()):
        index = int(action[3:]) if action != "thumb" else 0
        asyncio.create_task(run_cover_action(query, session, index))
        return

    # Process background action
    asyncio.create_task(run_media_action(update, query, session, action))


COVER_CACHE: dict = {}  # session_id -> list of cover size options (in memory)


def _cache_cover_options(session_id: str, options: list):
    if len(COVER_CACHE) >= 200:
        COVER_CACHE.pop(next(iter(COVER_CACHE)))
    COVER_CACHE[session_id] = options


async def run_cover_action(query, session: MediaSession, index: int = 0):
    """Send the Instagram cover: photo + original file + download link + quality buttons.

    index 0 = highest quality. Other indexes come from the quality buttons.
    """
    status_msg = None
    tmp_dir = None
    loop = asyncio.get_running_loop()

    try:
        status_msg = await query.message.reply_text("🖼 Fetching cover…")

        options = COVER_CACHE.get(session.session_id)
        if not options:
            if not session.url:
                raise RuntimeError("Media URL unknown; cannot fetch cover.")
            options = await loop.run_in_executor(None, get_cover_options, session.url, session.uid)
            _cache_cover_options(session.session_id, options)

        index = max(0, min(index, len(options) - 1))
        chosen = options[index]

        tmp_dir = os.path.join("/tmp", "ytdlp_downloads", f"cover_{uuid.uuid4().hex[:8]}")
        out_file = os.path.join(tmp_dir, "cover.jpg")
        meta = await loop.run_in_executor(None, download_cover_image, chosen["url"], out_file)

        w = meta.get("width") or chosen.get("width")
        h = meta.get("height") or chosen.get("height")
        size_txt = f"{w}x{h}" if w and h else "unknown size"
        kb_size = meta["bytes"] / 1024
        tag = " (best quality)" if index == 0 else ""

        if status_msg:
            try:
                await status_msg.edit_text("📤 Uploading…")
            except Exception:
                pass

        with open(out_file, "rb") as f:
            await query.message.reply_photo(photo=f, caption=f"🖼 Cover {size_txt}{tag}")
        with open(out_file, "rb") as f:
            await query.message.reply_document(
                document=f,
                filename=f"cover_{size_txt}.jpg",
                caption=f"📁 Original file • {kb_size:.0f} KB",
            )

        link_text = (
            f"🔗 Download link ({size_txt}, {kb_size:.0f} KB){tag}:\n{chosen['url']}\n\n"
            "Want another quality? Tap a size below."
        )
        await query.message.reply_text(
            link_text[:4000],
            reply_markup=build_cover_keyboard(session.session_id, options, index),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    except Exception as e:
        logger.error(f"Cover action failed for session {session.session_id}: {e}", exc_info=True)
        # Fallback: frame from the downloaded video (no URL/quality options available)
        fallback_ok = False
        try:
            if session.file_path and os.path.exists(session.file_path):
                tmp_dir = tmp_dir or os.path.join("/tmp", "ytdlp_downloads", f"cover_{uuid.uuid4().hex[:8]}")
                os.makedirs(tmp_dir, exist_ok=True)
                frame_file = os.path.join(tmp_dir, "frame.jpg")
                await loop.run_in_executor(None, media_processor.extract_thumbnail, session.file_path, frame_file)
                if os.path.exists(frame_file) and os.path.getsize(frame_file) > 0:
                    with open(frame_file, "rb") as f:
                        await query.message.reply_photo(
                            photo=f,
                            caption="🖼 Couldn't fetch the Instagram cover, so this is a frame from the video.",
                        )
                    fallback_ok = True
        except Exception as fb_err:
            logger.error(f"Cover fallback failed for session {session.session_id}: {fb_err}")
        if not fallback_ok:
            try:
                await query.message.reply_text("❌ Unable to fetch the cover. Please try again.")
            except Exception:
                pass

    finally:
        session.is_processing = False
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


async def run_send_with_action(query, session: MediaSession, action: str, with_caption: bool):
    """Send the video again with the Instagram cover as Telegram thumbnail or cover.

    The bold caption is attached only if the Caption button was used on the video message.
    """
    status_msg = None
    tmp_dir = os.path.join("/tmp", "ytdlp_downloads", f"send_{uuid.uuid4().hex[:8]}")
    loop = asyncio.get_running_loop()

    try:
        status_msg = await query.message.reply_text("⏳ Preparing…")

        # Make sure the video file exists (auto re-download after cleanup/reboot)
        if not session.file_path or not os.path.exists(session.file_path):
            if not session.url:
                raise RuntimeError("Session source file missing and media URL unknown.")
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
            finally:
                task_manager.remove_task(redl_task_id)

        os.makedirs(tmp_dir, exist_ok=True)
        cover_file = os.path.join(tmp_dir, "cover.jpg")

        # Best Instagram cover; fall back to a frame from the video itself
        got_cover = False
        try:
            if session.url:
                options = COVER_CACHE.get(session.session_id)
                if not options:
                    options = await loop.run_in_executor(None, get_cover_options, session.url, session.uid)
                    _cache_cover_options(session.session_id, options)
                await loop.run_in_executor(None, download_cover_image, options[0]["url"], cover_file)
                got_cover = os.path.exists(cover_file) and os.path.getsize(cover_file) > 0
        except Exception as cover_err:
            logger.warning(f"Instagram cover unavailable for send-with ({cover_err}); using a video frame.")
        if not got_cover:
            await loop.run_in_executor(None, media_processor.extract_thumbnail, session.file_path, cover_file)
        if not os.path.exists(cover_file) or os.path.getsize(cover_file) == 0:
            raise RuntimeError("Could not obtain a cover image.")

        caption_kwargs = {}
        if with_caption:
            caption_kwargs = {"caption": build_bold_caption(session.caption), "parse_mode": "HTML"}

        try:
            await status_msg.edit_text("📤 Uploading…")
        except Exception:
            pass

        if action == "sendthumb":
            thumb_file = os.path.join(tmp_dir, "thumb.jpg")
            await loop.run_in_executor(None, make_telegram_thumb, cover_file, thumb_file)
            with open(session.file_path, "rb") as vf, open(thumb_file, "rb") as tf:
                await query.message.reply_video(
                    video=vf, thumbnail=tf, supports_streaming=True, **caption_kwargs
                )
        else:
            with open(session.file_path, "rb") as vf, open(cover_file, "rb") as cf:
                await query.message.reply_video(
                    video=vf, cover=cf, supports_streaming=True, **caption_kwargs
                )

    except Exception as e:
        logger.error(f"Send-with action '{action}' failed for session {session.session_id}: {e}", exc_info=True)
        try:
            await query.message.reply_text("❌ Unable to send the video. Please try again.")
        except Exception:
            pass

    finally:
        session.is_processing = False
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def run_media_action(update: Update, query, session: MediaSession, action: str):
    """Execute heavy FFmpeg media action in background task with moving dot animation and lock release."""
    temp_dir = os.path.dirname(session.file_path) if session.file_path else "/tmp"
    base_name = os.path.splitext(os.path.basename(session.file_path))[0] if session.file_path else "media"
    out_file = None
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
