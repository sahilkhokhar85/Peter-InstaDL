"""
Admin & owner configuration menu and document handlers.
"""
import os
import uuid
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import config
from utils.cookie_settings import settings_manager
from utils.buttons import get_settings_keyboard, make_button

logger = logging.getLogger("handlers.admin")


def is_admin_or_owner(user_id: int) -> bool:
    """Check if user_id is the owner or listed in ADMIN_IDS."""
    if not user_id:
        return False
    if config.OWNER_ID and user_id == config.OWNER_ID:
        return True
    return user_id in config.ADMIN_IDS or settings_manager.is_owner(user_id)


async def bsettings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /bSettings command (Owner / Admin only)."""
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin_or_owner(user_id):
        if update.effective_message:
            await update.effective_message.reply_text("❌ You don't have permission to use this command.")
        return

    menu_text = (
        "⚙️ *Admin Bot Settings*\n\n"
        "Manage global Instagram cookies and admin configuration below:"
    )
    if update.effective_message:
        await update.effective_message.reply_text(
            menu_text,
            reply_markup=get_settings_keyboard(),
            parse_mode="Markdown",
        )


async def settings_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries from /bSettings menu."""
    query = update.callback_query
    if not query:
        return

    user_id = query.from_user.id
    if not is_admin_or_owner(user_id):
        await query.answer("❌ You don't have permission to use this button.", show_alert=True)
        return

    await query.answer()
    data = query.data

    if data == "settings_upload_cookies":
        settings_manager.set_awaiting_upload(user_id, True)
        prompt_msg = (
            "🍪 *Upload Cookies*\n\n"
            "Please send your `cookies.txt` file as a Telegram document attachment.\n\n"
            "_Note: File must be in Netscape cookies.txt format._"
        )
        back_kb = InlineKeyboardMarkup([
            [make_button("❌ Cancel", callback_data="settings_main_menu", style="danger")]
        ])
        await query.edit_message_text(prompt_msg, reply_markup=back_kb, parse_mode="Markdown")

    elif data == "settings_cookie_status":
        status = settings_manager.get_cookie_status()
        if status["configured"] and status["validated"]:
            status_text = (
                "📊 *Cookie Status*\n\n"
                f"Status: ✅ Configured\n"
                f"Validated: ✅\n"
                f"Active: ✅\n"
                f"File: `{status['filename']}`\n"
                f"Size: `{status['size_str']}`\n"
                f"Valid Records: `{status['records']}`"
            )
        else:
            status_text = (
                "📊 *Cookie Status*\n\n"
                "Status: ❌ Not Configured\n"
                "Validated: ❌\n"
                "Active: ❌\n"
                "File: None\n"
                "Size: 0 B"
            )
        back_kb = InlineKeyboardMarkup([
            [make_button("🔙 Back to Settings", callback_data="settings_main_menu", style="success")]
        ])
        await query.edit_message_text(status_text, reply_markup=back_kb, parse_mode="Markdown")

    elif data == "settings_remove_cookies":
        confirm_text = (
            "⚠️ *Remove Cookies*\n\n"
            "Are you sure you want to remove the currently configured global cookies?"
        )
        confirm_kb = InlineKeyboardMarkup([
            [
                make_button("✅ Confirm", callback_data="settings_confirm_remove"),
                make_button("❌ Cancel", callback_data="settings_main_menu", style="danger"),
            ]
        ])
        await query.edit_message_text(confirm_text, reply_markup=confirm_kb, parse_mode="Markdown")

    elif data == "settings_confirm_remove":
        success = settings_manager.remove_cookies()
        if success:
            res_text = "✅ *Cookies removed successfully.*"
        else:
            res_text = "❌ *Failed to remove cookies file.*"
        back_kb = InlineKeyboardMarkup([
            [make_button("🔙 Back to Settings", callback_data="settings_main_menu", style="success")]
        ])
        await query.edit_message_text(res_text, reply_markup=back_kb, parse_mode="Markdown")

    elif data == "settings_main_menu":
        settings_manager.set_awaiting_upload(user_id, False)
        menu_text = (
            "⚙️ *Admin Bot Settings*\n\n"
            "Manage global Instagram cookies and admin configuration below:"
        )
        await query.edit_message_text(menu_text, reply_markup=get_settings_keyboard(), parse_mode="Markdown")


async def handle_document_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document uploads for cookies.txt from owner or admin."""
    if not update.effective_message or not update.effective_message.document:
        return

    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin_or_owner(user_id):
        return

    doc = update.effective_message.document
    fn = (doc.file_name or "").lower()
    if not settings_manager.is_awaiting_upload(user_id) and not ("cookie" in fn or fn.endswith(".txt")):
        return

    settings_manager.set_awaiting_upload(user_id, False)

    status_msg = await update.effective_message.reply_text("⏳ *Validating uploaded cookie file...*", parse_mode="Markdown")

    temp_id = uuid.uuid4().hex[:8]
    temp_dir = os.path.join("/tmp", "cookie_uploads")
    os.makedirs(temp_dir, exist_ok=True)
    temp_file = os.path.join(temp_dir, f"temp_cookie_{temp_id}.txt")

    try:
        telegram_file = await context.bot.get_file(doc.file_id)
        await telegram_file.download_to_drive(custom_path=temp_file)

        activated, msg = settings_manager.activate_cookie_file(temp_file)
        if activated:
            await status_msg.edit_text(
                "✅ *Cookies validated and uploaded successfully.*\n\n"
                "🍪 Cookies are now configured.",
                parse_mode="Markdown",
            )
        else:
            await status_msg.edit_text(
                f"❌ *Invalid cookies file.*\n\n"
                "Please send a valid Netscape-format `cookies.txt` file.",
                parse_mode="Markdown",
            )
    except Exception as e:
        logger.error(f"Error processing uploaded document: {e}")
        await status_msg.edit_text("❌ *Error processing uploaded file.*", parse_mode="Markdown")
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass
