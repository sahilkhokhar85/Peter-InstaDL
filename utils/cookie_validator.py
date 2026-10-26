"""
Netscape cookies.txt format validator.
"""
import logging

logger = logging.getLogger("utils.cookie_validator")


def validate_netscape_cookies(filepath: str) -> tuple[bool, int]:
    """Validate if file matches Netscape cookies format and return count of valid records."""
    valid_records = 0
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    valid_records += 1

        is_valid = valid_records > 0
        return is_valid, valid_records
    except Exception as e:
        logger.error(f"Error validating cookie file '{filepath}': {e}")
        return False, 0
