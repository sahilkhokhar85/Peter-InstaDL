import os
import re

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_ID = int(os.getenv("API_ID", "0")) if os.getenv("API_ID", "").isdigit() else 0
API_HASH = os.getenv("API_HASH", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

OWNER_ID = int(os.getenv("OWNER_ID", "0")) if os.getenv("OWNER_ID", "").isdigit() else 0

raw_admins = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = set()
if OWNER_ID:
    ADMIN_IDS.add(OWNER_ID)
if raw_admins:
    for item in re.split(r"[,;:\s]+", raw_admins.strip()):
        item_str = item.strip()
        if item_str.lstrip("-").isdigit():
            ADMIN_IDS.add(int(item_str))

START_TEXT = os.getenv(
    "START_TEXT",
    "📸 *Instagram Downloader*\n\nSend an Instagram Reel, Post, IGTV, or Story link and the bot will download the media."
)
START_IMAGE = os.getenv("START_IMAGE", "")
FORCE_SUB_CHANNEL = os.getenv("FORCE_SUB_CHANNEL", "https://cdn.phototourl.com/free/2026-09-17-80d35211-554a-4548-8bc4-a0e18dfa54f5.png").strip()
YOUTUBE_CHANNEL_URL = os.getenv("YOUTUBE_CHANNEL_URL", "https://youtube.com/@tdbotdev?si=ue8vF4BMSaH1m_zH").strip()

# Telegram Bot commands list for auto-registration on startup
BOT_COMMANDS = [
    ("start", "Start the bot and view instructions"),
    ("settings", "Configure video quality and download options"),
    ("cancel", "Cancel current active download task"),
    ("bsettings", "Admin settings for global cookie management"),
]
