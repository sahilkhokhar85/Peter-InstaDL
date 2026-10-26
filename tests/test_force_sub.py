import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import ChatMember, User, Message, Chat
from telegram.constants import ChatMemberStatus

import config
from handlers.force_sub import (
    parse_channel_setting,
    resolve_channel_target,
    check_force_sub,
    forcesub_callback_handler,
    is_admin_or_owner,
)


class TestForceSub(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.original_force_sub = config.FORCE_SUB_CHANNEL
        self.original_owner_id = config.OWNER_ID
        self.original_admin_ids = config.ADMIN_IDS.copy()

    def tearDown(self):
        config.FORCE_SUB_CHANNEL = self.original_force_sub
        config.OWNER_ID = self.original_owner_id
        config.ADMIN_IDS = self.original_admin_ids

    def test_parse_channel_setting(self):
        # Username
        chat_id, url = parse_channel_setting("@mychannel")
        self.assertEqual(chat_id, "@mychannel")
        self.assertEqual(url, "https://t.me/mychannel")

        # Plain name without @
        chat_id, url = parse_channel_setting("mychannel")
        self.assertEqual(chat_id, "@mychannel")
        self.assertEqual(url, "https://t.me/mychannel")

        # Numeric ID
        chat_id, url = parse_channel_setting("-1001234567890")
        self.assertEqual(chat_id, -1001234567890)
        self.assertEqual(url, "")

        # Full URL
        chat_id, url = parse_channel_setting("https://t.me/mychannel")
        self.assertEqual(chat_id, "@mychannel")
        self.assertEqual(url, "https://t.me/mychannel")

        # Private invite URL
        chat_id, url = parse_channel_setting("https://t.me/+AbCdEf123")
        self.assertEqual(chat_id, "https://t.me/+AbCdEf123")
        self.assertEqual(url, "https://t.me/+AbCdEf123")

    async def test_resolve_channel_target_numeric(self):
        context = MagicMock()
        mock_chat = MagicMock()
        mock_chat.username = "numeric_channel"
        context.bot.get_chat = AsyncMock(return_value=mock_chat)

        chat_id, invite_url = await resolve_channel_target(context, "-1001234567890")
        self.assertEqual(chat_id, -1001234567890)
        self.assertEqual(invite_url, "https://t.me/numeric_channel")

    async def test_check_force_sub_disabled(self):
        config.FORCE_SUB_CHANNEL = ""
        update = MagicMock()
        context = MagicMock()

        res = await check_force_sub(update, context)
        self.assertTrue(res)

    async def test_check_force_sub_admin_bypass(self):
        config.FORCE_SUB_CHANNEL = "@mychannel"
        config.OWNER_ID = 12345
        update = MagicMock()
        update.effective_user.id = 12345
        context = MagicMock()

        res = await check_force_sub(update, context)
        self.assertTrue(res)

    async def test_check_force_sub_subscribed(self):
        config.FORCE_SUB_CHANNEL = "@mychannel"
        config.OWNER_ID = 99999
        config.ADMIN_IDS = set()

        update = MagicMock()
        update.effective_user.id = 11111

        member = MagicMock()
        member.status = ChatMemberStatus.MEMBER

        context = MagicMock()
        context.bot.get_chat_member = AsyncMock(return_value=member)

        res = await check_force_sub(update, context)
        self.assertTrue(res)

    async def test_check_force_sub_unsubscribed(self):
        config.FORCE_SUB_CHANNEL = "@mychannel"
        config.OWNER_ID = 99999
        config.ADMIN_IDS = set()

        update = MagicMock()
        update.effective_user.id = 11111
        update.effective_message.reply_text = AsyncMock()

        member = MagicMock()
        member.status = ChatMemberStatus.LEFT

        context = MagicMock()
        context.bot.get_chat_member = AsyncMock(return_value=member)

        res = await check_force_sub(update, context)
        self.assertFalse(res)
        update.effective_message.reply_text.assert_called_once()
        args, kwargs = update.effective_message.reply_text.call_args
        self.assertIn("Subscription Required", args[0])

    async def test_check_force_sub_api_error_fails_safe(self):
        config.FORCE_SUB_CHANNEL = "@mychannel"
        config.OWNER_ID = 99999
        config.ADMIN_IDS = set()

        update = MagicMock()
        update.effective_user.id = 11111
        update.effective_message.reply_text = AsyncMock()

        context = MagicMock()
        context.bot.get_chat_member = AsyncMock(side_effect=Exception("Telegram Chat Not Found"))

        res = await check_force_sub(update, context)
        self.assertFalse(res)

    async def test_callback_recheck_subscribed(self):
        config.FORCE_SUB_CHANNEL = "@mychannel"
        config.OWNER_ID = 99999
        config.ADMIN_IDS = set()

        update = MagicMock()
        query = MagicMock()
        query.from_user.id = 11111
        query.answer = AsyncMock()
        query.message.delete = AsyncMock()
        update.callback_query = query

        member = MagicMock()
        member.status = ChatMemberStatus.MEMBER

        context = MagicMock()
        context.bot.get_chat_member = AsyncMock(return_value=member)

        with patch("handlers.start.send_start_screen", new_callable=AsyncMock) as mock_send_start:
            await forcesub_callback_handler(update, context)
            query.answer.assert_called_once_with("✅ Thank you for subscribing!", show_alert=True)
            query.message.delete.assert_called_once()
            mock_send_start.assert_called_once_with(update, context)

    async def test_callback_recheck_unsubscribed(self):
        config.FORCE_SUB_CHANNEL = "@mychannel"
        config.OWNER_ID = 99999
        config.ADMIN_IDS = set()

        update = MagicMock()
        query = MagicMock()
        query.from_user.id = 11111
        query.answer = AsyncMock()
        update.callback_query = query

        member = MagicMock()
        member.status = ChatMemberStatus.LEFT

        context = MagicMock()
        context.bot.get_chat_member = AsyncMock(return_value=member)

        await forcesub_callback_handler(update, context)
        query.answer.assert_called_once_with("❌ You have not joined the channel yet. Please join and try again.", show_alert=True)


if __name__ == "__main__":
    unittest.main()
