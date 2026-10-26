"""
Force subscription check handler.
Checks user membership in FORCE_SUB_CHANNEL before granting access to bot features.
"""
import logging
import config
from telegram import Update, InlineKeyboardMarkup
from utils.buttons import make_button
from telegram.ext import ContextTypes
from telegram.constants import ChatMemberStatus

logger = logging.getLogger("handlers.force_sub")


def is_admin_or_owner(user_id: int) -> bool:
    if not user_id:
        return False
    if config.OWNER_ID and user_id == config.OWNER_ID:
        return True
    return user_id in config.ADMIN_IDS


def parse_channel_setting(channel_str: str) -> tuple[int | str, str]:
    """
    Parse FORCE_SUB_CHANNEL string into (chat_id_or_username, url_hint).
    Supports @username, -100xxxxxxxxxx, or https://t.me/...
    """
    s = channel_str.strip()
    if not s:
        return "", ""

    if s.startswith("https://t.me/") or s.startswith("http://t.me/"):
        parts = s.rstrip("/").split("/")
        last_part = parts[-1]
        if last_part.startswith("+") or "joinchat" in parts:
            return s, s
        else:
            username = f"@{last_part}"
            return username, f"https://t.me/{last_part}"

    if s.startswith("@"):
        username = s[1:]
        return s, f"https://t.me/{username}"

    if s.lstrip("-").isdigit():
        return int(s), ""

    return f"@{s}", f"https://t.me/{s}"


async def resolve_channel_target(context: ContextTypes.DEFAULT_TYPE, channel_setting: str) -> tuple[int | str | None, str]:
    """
    Resolve FORCE_SUB_CHANNEL string into (chat_id_for_api, invite_url_for_button).
    """
    s = channel_setting.strip()
    if not s:
        return None, ""

    chat_id_or_link, url_hint = parse_channel_setting(s)
    if not chat_id_or_link:
        return None, ""

    invite_url = url_hint

    if isinstance(chat_id_or_link, int) or (isinstance(chat_id_or_link, str) and chat_id_or_link.lstrip("-").isdigit()):
        chat_id = int(chat_id_or_link)
        if not invite_url:
            try:
                chat = await context.bot.get_chat(chat_id)
                if getattr(chat, "username", None):
                    invite_url = f"https://t.me/{chat.username}"
                elif getattr(chat, "invite_link", None):
                    invite_url = chat.invite_link
                else:
                    try:
                        invite_url = await context.bot.export_chat_invite_link(chat_id)
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Could not resolve invite link for numeric channel {chat_id}: {e}")

        if not invite_url:
            invite_url = f"https://t.me/c/{abs(chat_id) % 1000000000000}"
        return chat_id, invite_url

    return chat_id_or_link, invite_url or f"https://t.me/{str(chat_id_or_link).lstrip('@')}"


async def check_force_sub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Check if the user is subscribed to FORCE_SUB_CHANNEL.
    Returns True if subscribed, admin/owner, or if feature is disabled.
    """
    channel_setting = config.FORCE_SUB_CHANNEL
    if not channel_setting:
        return True

    user_id = update.effective_user.id if update.effective_user else 0
    if not user_id or is_admin_or_owner(user_id):
        return True

    target_chat, invite_url = await resolve_channel_target(context, channel_setting)
    if not target_chat:
        return True

    subscribed = False
    try:
        member = await context.bot.get_chat_member(chat_id=target_chat, user_id=user_id)
        if member and member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ):
            subscribed = True
        elif member and member.status == ChatMemberStatus.RESTRICTED:
            subscribed = getattr(member, "is_member", False)
    except Exception as e:
        logger.error(f"Error checking force sub for user {user_id} in {target_chat}: {e}")
        subscribed = False

    if subscribed:
        return True

    join_button = make_button("🔔 Join Channel", url=invite_url)
    check_button = make_button("🔄 Re-check", callback_data="forcesub_check")
    kb = InlineKeyboardMarkup([[join_button], [check_button]])

    text = (
        "⚠️ *Subscription Required*\n\n"
        "To use this bot, please join our channel first by clicking the button below:"
    )

    if update.effective_message:
        try:
            await update.effective_message.reply_text(
                text,
                reply_markup=kb,
                parse_mode="Markdown",
            )
        except Exception as err:
            logger.error(f"Failed to send force sub message: {err}")

    return False


async def forcesub_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'forcesub_check' callback button to re-evaluate membership."""
    query = update.callback_query
    if not query:
        return

    channel_setting = config.FORCE_SUB_CHANNEL
    if not channel_setting:
        await query.answer("✅ Force subscription is disabled.", show_alert=True)
        try:
            await query.message.delete()
        except Exception:
            pass
        from handlers.start import send_start_screen
        await send_start_screen(update, context)
        return

    user_id = query.from_user.id
    if is_admin_or_owner(user_id):
        await query.answer("✅ Access granted (Admin/Owner).", show_alert=True)
        try:
            await query.message.delete()
        except Exception:
            pass
        from handlers.start import send_start_screen
        await send_start_screen(update, context)
        return

    target_chat, invite_url = await resolve_channel_target(context, channel_setting)
    if not target_chat:
        await query.answer("✅ Force subscription is disabled.", show_alert=True)
        try:
            await query.message.delete()
        except Exception:
            pass
        from handlers.start import send_start_screen
        await send_start_screen(update, context)
        return

    subscribed = False
    try:
        member = await context.bot.get_chat_member(chat_id=target_chat, user_id=user_id)
        if member and member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ):
            subscribed = True
        elif member and member.status == ChatMemberStatus.RESTRICTED:
            subscribed = getattr(member, "is_member", False)
    except Exception as e:
        logger.error(f"Error re-checking force sub for user {user_id} in {target_chat}: {e}")
        subscribed = False

    if subscribed:
        await query.answer("✅ Thank you for subscribing!", show_alert=True)
        try:
            await query.message.delete()
        except Exception:
            pass
        from handlers.start import send_start_screen
        await send_start_screen(update, context)
    else:
        await query.answer("❌ You have not joined the channel yet. Please join and try again.", show_alert=True)
