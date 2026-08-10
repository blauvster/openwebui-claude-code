"""
Persistent chat_id -> Claude Code session_id mapping.

Why this exists
----------------
`claude_agent_pipe.py` used to keep this mapping in a plain in-process dict
(`_chat_sessions`). That breaks in three common situations:

  1. The backend process restarts (redeploy, crash, admin reloads the
     function) -> the dict is empty again.
  2. The backend runs with more than one worker process / replica -> each
     worker has its own dict, so whichever worker handles the next turn may
     simply not know about the session the previous turn created.
  3. Long-lived deployments accumulate sessions for chats that are no longer
     active with no way to expire them.

This module replaces the in-memory dict with a small SQLite database that
lives next to the per-chat workdir (`WORKDIR_ROOT/.session_store.sqlite3`).
It is intentionally NOT integrated with OpenWebUI's own database: OpenWebUI's
internal SQLAlchemy engine/schema is not a stable, documented plugin API, and
writing into OpenWebUI's own `chat`/`message` tables risks racing with
OpenWebUI's own read-modify-write cycle on the same rows. A separate SQLite
file has no such overlap and needs no coordination with OpenWebUI at all.

SQLite is a reasonable choice here (rather than e.g. requiring Redis) because
the write volume is tiny (one row write per chat turn) and SQLite's built-in
locking is sufficient for the "one active turn per chat_id at a time" access
pattern this pipe has. If you run many worker processes hammering the *same*
chat_id concurrently, SQLite's default locking will simply serialize those
writes rather than corrupt anything.

Usage
-----
    store = SessionStore(root_dir)
    resume_id = store.get(chat_id)
    ...
    store.set(chat_id, session_id)
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS claude_code_sessions (
    chat_id     TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    updated_at  REAL NOT NULL
);
"""


class SessionStore:
    """Thread-safe, file-backed chat_id -> session_id store.

    One SQLite connection per instance, guarded by a lock. Pipes are invoked
    concurrently for different chats within the same process, so the lock is
    held only for the duration of a single get/set (a few milliseconds),
    not for the whole agent turn.
    """

    def __init__(self, root_dir: str | Path, filename: str = ".session_store.sqlite3") -> None:
        self._path = Path(root_dir) / filename
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # check_same_thread=False: pipe() runs inside asyncio, potentially
        # scheduled on different threads across calls (e.g. via run_in_executor
        # elsewhere in the codebase). Access is still serialized by self._lock.
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False, timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        with self._lock:
            self._conn.execute(_SCHEMA)
            self._conn.commit()

    def get(self, chat_id: str) -> Optional[str]:
        try:
            with self._lock:
                cur = self._conn.execute(
                    "SELECT session_id FROM claude_code_sessions WHERE chat_id = ?",
                    (chat_id,),
                )
                row = cur.fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            log.exception("SessionStore.get failed for chat_id=%s", chat_id)
            return None

    def set(self, chat_id: str, session_id: str) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT INTO claude_code_sessions (chat_id, session_id, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET
                        session_id = excluded.session_id,
                        updated_at = excluded.updated_at
                    """,
                    (chat_id, session_id, time.time()),
                )
                self._conn.commit()
        except sqlite3.Error:
            log.exception("SessionStore.set failed for chat_id=%s", chat_id)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_stores: dict[str, SessionStore] = {}
_stores_lock = threading.Lock()


def get_store(root_dir: str | Path) -> SessionStore:
    """Return a process-wide singleton SessionStore per WORKDIR_ROOT.

    Avoids opening a new SQLite connection on every single pipe() call while
    still keying correctly if the valve ever points at a different root.
    """
    key = str(root_dir)
    with _stores_lock:
        store = _stores.get(key)
        if store is None:
            store = SessionStore(root_dir)
            _stores[key] = store
        return store
