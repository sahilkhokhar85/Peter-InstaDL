"""
Settings and cookie management utility for admin bot operations.
"""
import os
import shutil
import logging
import config
from utils.cookie_validator import validate_netscape_cookies

logger = logging.getLogger("utils.cookie_settings")

GLOBAL_COOKIE_FILE = os.getenv("COOKIES_FILE", "/tmp/cookies.txt")


class SettingsManager:
    def __init__(self):
        self._awaiting_upload = {}

    def is_owner(self, user_id: int) -> bool:
        if not user_id:
            return False
        if config.OWNER_ID and user_id == config.OWNER_ID:
            return True
        return user_id in config.ADMIN_IDS

    def set_awaiting_upload(self, user_id: int, status: bool):
        if status:
            self._awaiting_upload[user_id] = True
        else:
            self._awaiting_upload.pop(user_id, None)

    def is_awaiting_upload(self, user_id: int) -> bool:
        return self._awaiting_upload.get(user_id, False)

    def get_cookie_status(self) -> dict:
        if not os.path.exists(GLOBAL_COOKIE_FILE):
            return {
                "configured": False,
                "validated": False,
                "filename": None,
                "size_str": "0 B",
                "records": 0,
            }

        file_size = os.path.getsize(GLOBAL_COOKIE_FILE)
        is_valid, record_count = validate_netscape_cookies(GLOBAL_COOKIE_FILE)

        size_str = f"{file_size} B"
        if file_size >= 1024 * 1024:
            size_str = f"{file_size / (1024 * 1024):.2f} MB"
        elif file_size >= 1024:
            size_str = f"{file_size / 1024:.2f} KB"

        return {
            "configured": True,
            "validated": is_valid,
            "filename": os.path.basename(GLOBAL_COOKIE_FILE),
            "size_str": size_str,
            "records": record_count,
        }

    def activate_cookie_file(self, src_path: str) -> tuple[bool, str]:
        if not os.path.exists(src_path):
            return False, "Source cookie file not found."

        is_valid, record_count = validate_netscape_cookies(src_path)
        if not is_valid or record_count == 0:
            return False, f"Invalid Netscape cookie format or empty records ({record_count})."

        target_dir = os.path.dirname(GLOBAL_COOKIE_FILE)
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)

        shutil.copy2(src_path, GLOBAL_COOKIE_FILE)
        os.chmod(GLOBAL_COOKIE_FILE, 0o600)
        logger.info(f"Successfully activated global cookie file at '{GLOBAL_COOKIE_FILE}' ({record_count} valid records).")
        return True, f"Activated successfully with {record_count} valid records."

    def remove_cookies(self) -> bool:
        if os.path.exists(GLOBAL_COOKIE_FILE):
            try:
                os.remove(GLOBAL_COOKIE_FILE)
                logger.info("Removed global cookies file.")
                return True
            except Exception as e:
                logger.error(f"Failed to remove cookies file: {e}")
                return False
        return True


settings_manager = SettingsManager()
