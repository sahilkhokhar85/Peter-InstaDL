"""
Centralized database and session storage module.
Provides user preferences management and persistent MediaSession tracking.
Supports PostgreSQL (via psycopg2), SQLite, and local JSON file fallback.
"""
import os
import json
import time
import threading
import logging
from typing import Dict, Any, Optional

import config

logger = logging.getLogger("ytdlp_bot.database")

USERS_DB_FILE = os.getenv("USERS_DB_FILE", "/tmp/users_settings.json")
SESSIONS_DB_FILE = os.getenv("SESSIONS_DB_FILE", "/tmp/sessions_db.json")

DEFAULT_USER_SETTINGS = {
    "default_quality": "best",  # '360p', '480p', '720p', '1080p', 'best'
    "download_mode": "manual",  # 'fixed', 'manual'
    "cleanup_timer": 10,       # 5, 10, 15, 30, 0 (0 = Never)
    "cookies_path": None,
}


def _get_db_connection():
    db_url = (config.DATABASE_URL or os.getenv("DATABASE_URL", "")).strip()
    if not db_url:
        return None, "none"

    if db_url.startswith("postgres://") or db_url.startswith("postgresql://"):
        try:
            import psycopg2
            pg_url = db_url
            if pg_url.startswith("postgres://"):
                pg_url = "postgresql://" + pg_url[len("postgres://"):]
            conn = psycopg2.connect(pg_url)
            conn.autocommit = True
            return conn, "postgres"
        except Exception as e:
            logger.error(f"Failed to connect to PostgreSQL database: {e}")
            return None, "postgres_error"

    if db_url.startswith("sqlite://") or db_url.endswith(".db") or db_url.endswith(".sqlite"):
        try:
            import sqlite3
            db_path = db_url.replace("sqlite:///", "").replace("sqlite://", "")
            if not db_path:
                db_path = "/tmp/app_database.db"
            target_dir = os.path.dirname(db_path)
            if target_dir:
                os.makedirs(target_dir, exist_ok=True)
            conn = sqlite3.connect(db_path, check_same_thread=False)
            return conn, "sqlite"
        except Exception as e:
            logger.error(f"Failed to connect to SQLite database ({db_url}): {e}")
            return None, "sqlite_error"

    return None, "unsupported"


def _init_db_tables(conn, db_type: str):
    if not conn or db_type in ("none", "unsupported", "postgres_error", "sqlite_error"):
        return

    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                uid VARCHAR(64) PRIMARY KEY,
                data TEXT
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS media_sessions (
                session_id VARCHAR(64) PRIMARY KEY,
                uid BIGINT,
                file_path TEXT,
                media_type VARCHAR(32),
                title TEXT,
                uploader TEXT,
                caption TEXT,
                available_qualities TEXT,
                url TEXT,
                telegram_chat_id BIGINT,
                telegram_message_id BIGINT,
                created_at DOUBLE PRECISION
            );
        """)
        if db_type == "sqlite":
            conn.commit()
        cur.close()
        logger.info(f"Database tables initialized successfully using {db_type}.")
    except Exception as e:
        logger.error(f"Error initializing database tables ({db_type}): {e}")


class UsersDatabase:
    def __init__(self, db_file: str = USERS_DB_FILE):
        self.db_file = db_file
        self._data: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.conn, self.db_type = _get_db_connection()
        if self.conn:
            _init_db_tables(self.conn, self.db_type)
        self._load()

    def _load(self):
        with self._lock:
            # 1. Load from SQL database if available
            if self.conn:
                try:
                    cur = self.conn.cursor()
                    cur.execute("SELECT uid, data FROM user_settings;")
                    rows = cur.fetchall()
                    for uid_str, data_str in rows:
                        try:
                            self._data[str(uid_str)] = json.loads(data_str)
                        except Exception:
                            pass
                    cur.close()
                    logger.info(f"Loaded {len(self._data)} user settings records from {self.db_type} DB.")
                    return
                except Exception as e:
                    logger.error(f"Failed to load user settings from DB ({self.db_type}): {e}")

            # 2. Local JSON fallback
            if os.path.exists(self.db_file):
                try:
                    with open(self.db_file, "r", encoding="utf-8") as f:
                        self._data = json.load(f)
                except Exception as e:
                    logger.error(f"Failed to load users DB file '{self.db_file}': {e}")
                    self._data = {}

    def _save(self):
        # 1. Save to SQL database if available
        if self.conn:
            try:
                cur = self.conn.cursor()
                ph = "%s" if self.db_type == "postgres" else "?"
                for uid_str, udata in self._data.items():
                    data_str = json.dumps(udata)
                    if self.db_type == "postgres":
                        query = f"INSERT INTO user_settings (uid, data) VALUES ({ph}, {ph}) ON CONFLICT (uid) DO UPDATE SET data = EXCLUDED.data;"
                    else:
                        query = f"INSERT OR REPLACE INTO user_settings (uid, data) VALUES ({ph}, {ph});"
                    cur.execute(query, (uid_str, data_str))
                if self.db_type == "sqlite":
                    self.conn.commit()
                cur.close()
            except Exception as e:
                logger.error(f"Failed to save user settings to DB ({self.db_type}): {e}")

        # 2. Save to local JSON file
        try:
            target_dir = os.path.dirname(self.db_file)
            if target_dir:
                os.makedirs(target_dir, exist_ok=True)
            with open(self.db_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save users DB file '{self.db_file}': {e}")

    def get_settings(self, uid: int) -> Dict[str, Any]:
        """Get per-user settings with fallback to defaults."""
        uid_str = str(uid)
        with self._lock:
            user_data = self._data.get(uid_str, {})
        settings = DEFAULT_USER_SETTINGS.copy()
        settings.update(user_data)
        return settings

    def update_settings(self, uid: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update per-user settings using partial merge and save."""
        uid_str = str(uid)
        current = self.get_settings(uid)
        current.update(updates)
        with self._lock:
            self._data[uid_str] = current
            self._save()
        return current


class TaskManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.user_active_tasks: Dict[int, str] = {}

    def register_task(self, task_id: str, uid: int, user_mention: str = ""):
        with self._lock:
            self.tasks[task_id] = {
                "uid": uid,
                "user_mention": user_mention,
                "status": "pending",
                "cancelled": False,
                "progress": {
                    "name": "",
                    "done": 0,
                    "total": 0,
                    "speed": 0.0,
                    "eta": 0.0,
                    "status": "pending"
                }
            }
            if uid:
                self.user_active_tasks[uid] = task_id

    def set_status(self, task_id: str, status: str):
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id]["status"] = status
                self.tasks[task_id]["progress"]["status"] = status

    def update_progress(self, task_id: str, name: str, done: int, total: int, speed: float, eta: float, status: str):
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id]["progress"] = {
                    "name": name,
                    "done": done,
                    "total": total,
                    "speed": speed,
                    "eta": eta,
                    "status": status,
                }

    def cancel_task(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id]["cancelled"] = True
                self.tasks[task_id]["status"] = "cancelled"
                return True
            return False

    def cancel_user_task(self, uid: int) -> Optional[str]:
        with self._lock:
            task_id = self.user_active_tasks.get(uid)
            if task_id and task_id in self.tasks:
                self.tasks[task_id]["cancelled"] = True
                self.tasks[task_id]["status"] = "cancelled"
                return task_id
            return None

    def is_cancelled(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self.tasks:
                return self.tasks[task_id]["cancelled"]
            return False

    def get_user_mention(self, task_id: str) -> str:
        with self._lock:
            if task_id in self.tasks:
                return self.tasks[task_id].get("user_mention", "")
            return ""

    def remove_task(self, task_id: str):
        with self._lock:
            if task_id in self.tasks:
                uid = self.tasks[task_id].get("uid")
                if uid and self.user_active_tasks.get(uid) == task_id:
                    del self.user_active_tasks[uid]
                del self.tasks[task_id]


class MediaSession:
    def __init__(self, session_id: str, uid: int, file_path: str, media_type: str, title: str = "", uploader: str = "", caption: str = "", available_qualities: Optional[list] = None, url: str = ""):
        self.session_id = session_id
        self.uid = uid
        self.file_path = file_path
        self.media_type = media_type  # 'video' or 'photo'
        self.title = title
        self.uploader = uploader
        self.caption = caption
        self.available_qualities = available_qualities or []
        self.url = url
        self.telegram_chat_id: Optional[int] = None
        self.telegram_message_id: Optional[int] = None
        self.is_processing = False
        self.awaiting_caption = False
        self.showing_rotation_menu = False
        self.showing_quality_menu = False
        self.created_at = time.time()
        self.lock = threading.Lock()


class SessionManager:
    def __init__(self, db_file: str = SESSIONS_DB_FILE):
        self._lock = threading.Lock()
        self.db_file = db_file
        self.sessions: Dict[str, MediaSession] = {}
        self.user_active_caption_sessions: Dict[int, str] = {}
        self.conn, self.db_type = _get_db_connection()
        if self.conn:
            _init_db_tables(self.conn, self.db_type)
        self._load()

    def _load(self):
        with self._lock:
            # 1. Load from DB if available
            if self.conn:
                try:
                    cur = self.conn.cursor()
                    cur.execute("""
                        SELECT session_id, uid, file_path, media_type, title, uploader, caption,
                               available_qualities, url, telegram_chat_id, telegram_message_id, created_at
                        FROM media_sessions;
                    """)
                    rows = cur.fetchall()
                    for r in rows:
                        sid, uid, fp, mt, title, uploader, caption, qual_str, url, chat_id, msg_id, created_at = r
                        try:
                            qualities = json.loads(qual_str) if qual_str else []
                        except Exception:
                            qualities = []
                        session = MediaSession(
                            session_id=sid,
                            uid=uid or 0,
                            file_path=fp or "",
                            media_type=mt or "video",
                            title=title or "",
                            uploader=uploader or "",
                            caption=caption or "",
                            available_qualities=qualities,
                            url=url or "",
                        )
                        session.telegram_chat_id = chat_id
                        session.telegram_message_id = msg_id
                        session.created_at = created_at or time.time()
                        self.sessions[sid] = session
                    cur.close()
                    logger.info(f"Loaded {len(self.sessions)} media sessions from {self.db_type} DB.")
                    return
                except Exception as e:
                    logger.error(f"Failed to load sessions from DB ({self.db_type}): {e}")

            # 2. Local JSON file fallback
            if os.path.exists(self.db_file):
                try:
                    with open(self.db_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        for sid, sdata in data.items():
                            session = MediaSession(
                                session_id=sdata.get("session_id", sid),
                                uid=sdata.get("uid", 0),
                                file_path=sdata.get("file_path", ""),
                                media_type=sdata.get("media_type", "video"),
                                title=sdata.get("title", ""),
                                uploader=sdata.get("uploader", ""),
                                caption=sdata.get("caption", ""),
                                available_qualities=sdata.get("available_qualities", []),
                                url=sdata.get("url", ""),
                            )
                            session.telegram_chat_id = sdata.get("telegram_chat_id")
                            session.telegram_message_id = sdata.get("telegram_message_id")
                            session.created_at = sdata.get("created_at", time.time())
                            self.sessions[sid] = session
                except Exception as e:
                    logger.error(f"Failed to load sessions from '{self.db_file}': {e}")

    def _save(self):
        # 1. Save to DB if available
        if self.conn:
            try:
                cur = self.conn.cursor()
                ph = "%s" if self.db_type == "postgres" else "?"
                for sid, s in list(self.sessions.items()):
                    qual_str = json.dumps(s.available_qualities)
                    if self.db_type == "postgres":
                        query = f"""
                            INSERT INTO media_sessions (
                                session_id, uid, file_path, media_type, title, uploader, caption,
                                available_qualities, url, telegram_chat_id, telegram_message_id, created_at
                            ) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
                            ON CONFLICT (session_id) DO UPDATE SET
                                file_path = EXCLUDED.file_path,
                                media_type = EXCLUDED.media_type,
                                title = EXCLUDED.title,
                                uploader = EXCLUDED.uploader,
                                caption = EXCLUDED.caption,
                                available_qualities = EXCLUDED.available_qualities,
                                url = EXCLUDED.url,
                                telegram_chat_id = EXCLUDED.telegram_chat_id,
                                telegram_message_id = EXCLUDED.telegram_message_id,
                                created_at = EXCLUDED.created_at;
                        """
                    else:
                        query = f"""
                            INSERT OR REPLACE INTO media_sessions (
                                session_id, uid, file_path, media_type, title, uploader, caption,
                                available_qualities, url, telegram_chat_id, telegram_message_id, created_at
                            ) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph});
                        """
                    params = (
                        s.session_id, s.uid, s.file_path, s.media_type, s.title, s.uploader, s.caption,
                        qual_str, s.url, s.telegram_chat_id, s.telegram_message_id, s.created_at
                    )
                    cur.execute(query, params)
                if self.db_type == "sqlite":
                    self.conn.commit()
                cur.close()
            except Exception as e:
                logger.error(f"Failed to save sessions to DB ({self.db_type}): {e}")

        # 2. Local JSON file fallback
        try:
            target_dir = os.path.dirname(self.db_file)
            if target_dir:
                os.makedirs(target_dir, exist_ok=True)
            data = {}
            for sid, s in self.sessions.items():
                data[sid] = {
                    "session_id": s.session_id,
                    "uid": s.uid,
                    "file_path": s.file_path,
                    "media_type": s.media_type,
                    "title": s.title,
                    "uploader": s.uploader,
                    "caption": s.caption,
                    "available_qualities": s.available_qualities,
                    "url": s.url,
                    "telegram_chat_id": s.telegram_chat_id,
                    "telegram_message_id": s.telegram_message_id,
                    "created_at": s.created_at,
                }
            with open(self.db_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save sessions to '{self.db_file}': {e}")

    def create_session(self, session_id: str, uid: int, file_path: str, media_type: str, title: str = "", uploader: str = "", caption: str = "", available_qualities: Optional[list] = None, url: str = "") -> MediaSession:
        with self._lock:
            session = MediaSession(session_id, uid, file_path, media_type, title, uploader, caption, available_qualities, url)
            self.sessions[session_id] = session
            self._save()
            return session

    def get_session(self, session_id: str) -> Optional[MediaSession]:
        with self._lock:
            return self.sessions.get(session_id)

    def save_session_state(self):
        with self._lock:
            self._save()

    def set_awaiting_caption(self, uid: int, session_id: str, awaiting: bool):
        with self._lock:
            if awaiting:
                self.user_active_caption_sessions[uid] = session_id
                session = self.sessions.get(session_id)
                if session:
                    session.awaiting_caption = True
            else:
                if self.user_active_caption_sessions.get(uid) == session_id:
                    del self.user_active_caption_sessions[uid]
                session = self.sessions.get(session_id)
                if session:
                    session.awaiting_caption = False

    def get_caption_session_id(self, uid: int) -> Optional[str]:
        with self._lock:
            return self.user_active_caption_sessions.get(uid)

    def clear_user_states(self, uid: int):
        with self._lock:
            sid = self.user_active_caption_sessions.pop(uid, None)
            if sid and sid in self.sessions:
                self.sessions[sid].awaiting_caption = False
            for session in self.sessions.values():
                if session.uid == uid:
                    session.awaiting_caption = False
                    session.showing_rotation_menu = False
                    session.showing_quality_menu = False
                    session.is_processing = False

    def remove_session(self, session_id: str):
        with self._lock:
            session = self.sessions.pop(session_id, None)
            if session:
                uid = session.uid
                if self.user_active_caption_sessions.get(uid) == session_id:
                    del self.user_active_caption_sessions[uid]
                if self.conn:
                    try:
                        cur = self.conn.cursor()
                        ph = "%s" if self.db_type == "postgres" else "?"
                        cur.execute(f"DELETE FROM media_sessions WHERE session_id = {ph};", (session_id,))
                        if self.db_type == "sqlite":
                            self.conn.commit()
                        cur.close()
                    except Exception as e:
                        logger.error(f"Failed to delete session {session_id} from DB: {e}")
                self._save()


users_db = UsersDatabase()
task_manager = TaskManager()
session_manager = SessionManager()
