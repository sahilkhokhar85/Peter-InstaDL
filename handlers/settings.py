"""
User settings menu and callback handlers.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import users_db
from utils.buttons import build_user_settings_menu, make_button
from handlers.force_sub import check_force_sub

logger = logging.getLogger("handlers.settings")


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /settings command."""
    if not await check_force_sub(update, context):
        return

    uid = update.effective_user.id if update.effective_user else 0
    if not uid:
        return

    text, keyboard = build_user_settings_menu(uid)
    if update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def user_settings_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user settings menu callbacks."""
    query = update.callback_query
    if not query:
        return

    await query.answer()
    uid = query.from_user.id
    data = query.data or ""

    if data == "usrset_main_menu":
        text, keyboard = build_user_settings_menu(uid)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")

    elif data == "usrset_close":
        await query.delete_message()

    elif data == "usrset_quality_menu":
        text = "🎬 *Select Default Video Quality:*\n\nThis quality will be used automatically in Fixed Quality mode or as your default preference."
        kb = InlineKeyboardMarkup([
            [make_button("360p", callback_data="usrset_set_quality_360p"), make_button("480p", callback_data="usrset_set_quality_480p")],
            [make_button("720p", callback_data="usrset_set_quality_720p"), make_button("1080p", callback_data="usrset_set_quality_1080p")],
            [make_button("Best Available", callback_data="usrset_set_quality_best")],
            [make_button("🔙 Back", callback_data="usrset_main_menu", style="success")],
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")

    elif data.startswith("usrset_set_quality_"):
        q_val = data.replace("usrset_set_quality_", "")
        users_db.update_settings(uid, {"default_quality": q_val})
        text, keyboard = build_user_settings_menu(uid)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")

    elif data == "usrset_mode_menu":
        text = (
            "🔁 *Select Download Mode:*\n\n"
            "• *✅ Fixed Quality:* Skips quality selection and directly downloads using your saved default resolution.\n"
            "• *🎛 Manual Selection:* Shows dynamic quality buttons extracted from yt-dlp formats."
        )
        kb = InlineKeyboardMarkup([
            [make_button("✅ Fixed Quality", callback_data="usrset_set_mode_fixed")],
            [make_button("🎛 Manual Selection", callback_data="usrset_set_mode_manual")],
            [make_button("🔙 Back", callback_data="usrset_main_menu", style="success")],
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")

    elif data.startswith("usrset_set_mode_"):
        m_val = data.replace("usrset_set_mode_", "")
        users_db.update_settings(uid, {"download_mode": m_val})
        text, keyboard = build_user_settings_menu(uid)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")

    elif data == "usrset_format_menu":
        current = users_db.get_settings(uid).get("video_format", "mp4")
        text = (
            "🎞 *Select Video Format:*\n\n"
            "Videos will be sent as a video in this format.\n"
            "_MP4 plays everywhere. MKV is kept as-is without quality loss, "
            "but Telegram may show it as a file instead of a playable video._"
        )
        kb = InlineKeyboardMarkup([
            [
                make_button(("✅ " if current == "mp4" else "") + "MP4", callback_data="usrset_set_format_mp4"),
                make_button(("✅ " if current == "mkv" else "") + "MKV", callback_data="usrset_set_format_mkv"),
            ],
            [make_button("🔙 Back", callback_data="usrset_main_menu", style="success")],
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")

    elif data.startswith("usrset_set_format_"):
        f_val = data.replace("usrset_set_format_", "")
        if f_val in ("mp4", "mkv"):
            users_db.update_settings(uid, {"video_format": f_val})
        text, keyboard = build_user_settings_menu(uid)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")

    elif data == "usrset_cleanup_menu":
        text = "🧹 *Select Auto-Cleanup Timer:*\n\nControls how long orphan unattached temporary download files are retained on the server after processing."
        kb = InlineKeyboardMarkup([
            [make_button("5 Minutes", callback_data="usrset_set_timer_5"), make_button("10 Minutes", callback_data="usrset_set_timer_10")],
            [make_button("15 Minutes", callback_data="usrset_set_timer_15"), make_button("30 Minutes", callback_data="usrset_set_timer_30")],
            [make_button("♾ Never", callback_data="usrset_set_timer_0")],
            [make_button("🔙 Back", callback_data="usrset_main_menu", style="success")],
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")

    elif data.startswith("usrset_set_timer_"):
        t_val = int(data.replace("usrset_set_timer_", ""))
        users_db.update_settings(uid, {"cleanup_timer": t_val})
        text, keyboard = build_user_settings_menu(uid)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
