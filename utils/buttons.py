"""
Telegram inline keyboard builders.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from database import users_db
import config


def make_button(text: str, callback_data: str = None, url: str = None, style: str = None) -> InlineKeyboardButton:
    """Helper to safely build InlineKeyboardButton with optional style via api_kwargs."""
    kwargs = {"text": text}
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
    if style:
        kwargs["api_kwargs"] = {"style": style}
    return InlineKeyboardButton(**kwargs)


def build_start_keyboard() -> InlineKeyboardMarkup:
    """Build /start main menu inline keyboard with PRIMARY (BLUE) buttons."""
    contact_url = config.CONTACT_URL or "https://t.me/RudraBlaze"
    buttons = [
        [
            make_button("📢 Updates", url="https://t.me/ChillFlizX", style="primary"),
            make_button("📩 Contact", url=contact_url, style="primary"),
        ],
        [
            make_button("❓ Help", callback_data="start_help", style="primary"),
            make_button("ℹ️ About", callback_data="start_about", style="primary"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def build_start_back_keyboard() -> InlineKeyboardMarkup:
    """Build back button inline keyboard for /start submenus with SUCCESS (GREEN) button."""
    buttons = [
        [make_button("🔙 Back", callback_data="start_main", style="success")]
    ]
    return InlineKeyboardMarkup(buttons)


def build_user_settings_menu(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    """Build user settings menu text and keyboard."""
    s = users_db.get_settings(uid)
    dq = s.get("default_quality", "best")
    dm = s.get("download_mode", "manual")
    ct = s.get("cleanup_timer", 10)

    dq_label = "Best Available" if dq == "best" else dq
    dm_label = "✅ Fixed Quality" if dm == "fixed" else "🎛 Manual Selection"
    ct_label = "♾ Never" if ct == 0 else f"{ct} Minutes"

    text = (
        "⚙️ *User Settings*\n\n"
        f"🎬 *Default Video Quality:* `{dq_label}`\n"
        f"🔁 *Download Mode:* `{dm_label}`\n"
        f"🧹 *Cleanup Timer:* `{ct_label}`\n\n"
        "_Select an option below to customize your preferences:_"
    )

    keyboard = InlineKeyboardMarkup([
        [make_button(f"🎬 Default Video Quality ({dq_label})", callback_data="usrset_quality_menu")],
        [make_button(f"🔁 Download Mode ({dm_label})", callback_data="usrset_mode_menu")],
        [make_button(f"🧹 Cleanup Timer ({ct_label})", callback_data="usrset_cleanup_menu")],
        [make_button("❌ Close", callback_data="usrset_close", style="danger")],
    ])

    return text, keyboard


def get_settings_keyboard() -> InlineKeyboardMarkup:
    """Build settings main menu inline keyboard for bot admin."""
    buttons = [
        [make_button("🍪 Upload Cookies", callback_data="settings_upload_cookies")],
        [make_button("🗑 Remove Cookies", callback_data="settings_remove_cookies", style="danger")],
        [make_button("📊 Cookie Status", callback_data="settings_cookie_status")],
    ]
    return InlineKeyboardMarkup(buttons)


def build_video_action_keyboard(session_id: str) -> InlineKeyboardMarkup:
    """Build the exact 5-row inline action keyboard for video media."""
    buttons = [
        [
            make_button("🎵 Audio", callback_data=f"act_{session_id}_audio"),
            make_button("Quality", callback_data=f"act_{session_id}_qmenu"),
        ],
        [
            make_button("☾ Grayscale", callback_data=f"act_{session_id}_grayscale"),
            make_button("☰ Caption", callback_data=f"act_{session_id}_caption"),
        ],
        [
            make_button("↻ Rotate", callback_data=f"act_{session_id}_rotmenu"),
            make_button("🖼 Thumbnail", callback_data=f"act_{session_id}_thumb"),
        ],
        [
            make_button("🗑 Remove buttons", callback_data=f"act_{session_id}_remove", style="danger"),
        ],
        [
            make_button("↩ Back", callback_data=f"act_{session_id}_back", style="success"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def build_quality_keyboard(session_id: str, available_qualities: list[str]) -> InlineKeyboardMarkup:
    """Build dynamic quality inline keyboard with maximum 2 quality buttons per row and Back on separate final row."""
    qualities = available_qualities or ["720p"]
    rows = []
    current_row = []

    for q in qualities:
        label = q if q.endswith("p") else f"{q}p"
        cb_data = f"act_{session_id}_qsel_{label}"
        current_row.append(make_button(label, callback_data=cb_data))
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []

    if current_row:
        rows.append(current_row)

    rows.append([make_button("↩ Back", callback_data=f"act_{session_id}_back", style="success")])
    return InlineKeyboardMarkup(rows)


def build_rotation_keyboard(session_id: str) -> InlineKeyboardMarkup:
    """Build rotation selection inline keyboard."""
    buttons = [
        [
            make_button("90°", callback_data=f"act_{session_id}_rot90"),
            make_button("180°", callback_data=f"act_{session_id}_rot180"),
        ],
        [
            make_button("270°", callback_data=f"act_{session_id}_rot270"),
            make_button("↩ Back", callback_data=f"act_{session_id}_back", style="success"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def build_cover_keyboard(session_id: str, options: list, current: int = 0) -> InlineKeyboardMarkup:
    """Inline keyboard listing every available cover size (2 per row). Current one is marked."""
    rows, row = [], []
    for i, opt in enumerate(options[:12]):
        w, h = opt.get("width"), opt.get("height")
        label = f"{w}x{h}" if w and h else f"Size {i + 1}"
        if i == 0:
            label = f"⭐ {label}"
        if i == current:
            label = f"✅ {label}"
        row.append(make_button(label, callback_data=f"act_{session_id}_cvq{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)