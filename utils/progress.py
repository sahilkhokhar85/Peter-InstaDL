"""
Progress tracking and status animation utilities.
"""
import asyncio
import logging

logger = logging.getLogger("progress")


class MovingDotAnimation:
    """WhatsApp-style moving dot processing status indicator context manager."""

    DOT_FRAMES = ["•", "••", "•••", "••", "•"]

    def __init__(self, message, interval: float = 1.5):
        self.message = message
        self.interval = interval
        self._task = None
        self._running = False

    async def _animate(self):
        idx = 0
        while self._running:
            frame_text = self.DOT_FRAMES[idx % len(self.DOT_FRAMES)]
            idx += 1
            try:
                await self.message.edit_text(frame_text)
            except Exception:
                pass
            await asyncio.sleep(self.interval)

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._animate())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


def format_bytes(bytes_val: float) -> str:
    """Format raw byte counts to human-readable strings (e.g. 12.5 MB)."""
    if not bytes_val or bytes_val <= 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_val < 1024.0:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} PB"


def build_progress_card(
    status: str,
    fname: str,
    pct: float,
    done: int = 0,
    total: int = 0,
    speed: float = 0.0,
    eta: float = 0.0,
    elapsed: float = 0.0,
    tid: str = "",
    user_mention: str = "",
) -> str:
    """Format progress card text for Telegram status updates."""
    bar_len = 10
    filled = int(round(bar_len * (pct / 100.0))) if pct > 0 else 0
    bar = "█" * filled + "░" * (bar_len - filled)

    speed_str = f"{format_bytes(speed)}/s" if speed > 0 else "0 B/s"
    done_str = format_bytes(done)
    total_str = format_bytes(total) if total > 0 else "Unknown"

    eta_min, eta_sec = divmod(int(eta), 60)
    eta_str = f"{eta_min}m {eta_sec}s" if eta_min > 0 else f"{eta_sec}s"

    return (
        f"⬇️ *Downloading Media...*\n\n"
        f"📂 *File:* `{fname[:30]}`\n"
        f"📊 *Progress:* `[{bar}] {pct:.1f}%`\n"
        f"💾 *Size:* `{done_str} / {total_str}`\n"
        f"⚡ *Speed:* `{speed_str}`\n"
        f"⏳ *ETA:* `{eta_str}`"
    )


async def safe_edit(msg, text: str, parse_mode: str = "Markdown"):
    """Safely edit Telegram status message text with error suppression."""
    try:
        await msg.edit_text(text, parse_mode=parse_mode)
    except Exception:
        pass
