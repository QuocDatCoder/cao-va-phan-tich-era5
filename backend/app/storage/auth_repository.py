from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from backend.app.domain.live_refresh import (
    DEFAULT_LIVE_INTERVAL_HOURS,
    INACTIVE_AFTER_SECONDS,
    MAX_ACTIVE_LIVE_SCHEDULES,
    adaptive_backoff,
)


class UsernameAlreadyExistsError(Exception):
    pass


class LiveScheduleLimitError(Exception):
    pass


def _live_state(row, now):
    state = adaptive_backoff(
        row["requested_interval_hours"], now - row["activity_at"],
        bool(row["enabled"]), row["disabled_reason"],
    )
    return {
        "enabled": state.effective_enabled,
        "requested_interval_hours": row["requested_interval_hours"],
        "effective_interval_hours": state.effective_interval_hours,
        "backed_off": state.backed_off,
        "disabled_reason": state.disabled_reason,
        "last_refreshed_at": row["last_refreshed_at"],
    }


_LIVE_ROWS = """
    SELECT schedules.*, users.username, users.is_active,
           COALESCE(users.last_active_at, users.last_login_at, users.created_at) AS activity_at
    FROM collection_live_schedules AS schedules JOIN users ON users.id = schedules.user_id
"""


class AuthRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_hash TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    last_login_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    revoked_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS ix_auth_sessions_user_id
                    ON auth_sessions(user_id);
                CREATE TABLE IF NOT EXISTS admin_handoffs (
                    ticket_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL,
                    consumed_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS collection_live_schedules (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    collection_id TEXT NOT NULL,
                    place_name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    requested_interval_hours INTEGER NOT NULL DEFAULT 1
                        CHECK (requested_interval_hours IN (1, 3, 6, 24)),
                    last_refreshed_at TEXT,
                    disabled_reason TEXT,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, collection_id)
                );
                """
            )
            connection.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
            if "is_admin" not in columns:
                connection.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
            if "is_active" not in columns:
                connection.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
            if "last_login_at" not in columns:
                connection.execute("ALTER TABLE users ADD COLUMN last_login_at INTEGER")
            if "last_active_at" not in columns:
                connection.execute("ALTER TABLE users ADD COLUMN last_active_at INTEGER")

    @contextmanager
    def _connect(self):
        with closing(sqlite3.connect(self._db_path, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection

    def create_user(self, username: str, password_hash: str, created_at: int) -> int:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO users(username, password_hash, created_at) VALUES (?, ?, ?)",
                    (username, password_hash, created_at),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise UsernameAlreadyExistsError from exc

    @staticmethod
    def _expire_live(connection, now, user_id=None):
        connection.execute(
            """UPDATE collection_live_schedules
               SET enabled = 0, disabled_reason = 'inactive_7_days', updated_at = ?
               WHERE enabled = 1 AND (? IS NULL OR user_id = ?) AND user_id IN (
                   SELECT id FROM users
                   WHERE COALESCE(last_active_at, last_login_at, created_at) <= ?
               )""",
            (now, user_id, user_id, now - INACTIVE_AFTER_SECONDS),
        )

    def record_activity(self, user_id: int, now: int) -> None:
        with self._connect() as connection:
            self._record_activity(connection, user_id, now)

    def _record_activity(self, connection, user_id, now):
        # Persist expiry before a returning user can reset the inactivity clock.
        self._expire_live(connection, now, user_id)
        connection.execute(
            "UPDATE users SET last_active_at = ? WHERE id = ? AND COALESCE(last_active_at, 0) < ?",
            (now, user_id, now),
        )

    def get_live_schedule(self, user_id, collection_id, now):
        with self._connect() as connection:
            self._expire_live(connection, now, user_id)
            row = connection.execute(
                _LIVE_ROWS + "WHERE schedules.user_id = ? AND schedules.collection_id = ?",
                (user_id, collection_id),
            ).fetchone()
        return _live_state(row or {
            "requested_interval_hours": DEFAULT_LIVE_INTERVAL_HOURS, "activity_at": now,
            "enabled": False, "disabled_reason": None, "last_refreshed_at": None,
        }, now)

    def update_live_schedule(self, user_id, collection_id, place_name, enabled, interval, now):
        with self._connect() as connection:
            self._record_activity(connection, user_id, now)
            if enabled and connection.execute(
                """SELECT COUNT(*) FROM collection_live_schedules
                   WHERE user_id = ? AND enabled = 1 AND collection_id != ?""",
                (user_id, collection_id),
            ).fetchone()[0] >= MAX_ACTIVE_LIVE_SCHEDULES:
                raise LiveScheduleLimitError
            connection.execute(
                """INSERT INTO collection_live_schedules
                       (user_id, collection_id, place_name, enabled, requested_interval_hours, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (user_id, collection_id) DO UPDATE SET
                       place_name = excluded.place_name, enabled = excluded.enabled,
                       requested_interval_hours = excluded.requested_interval_hours,
                       disabled_reason = CASE WHEN excluded.enabled THEN NULL
                                             ELSE collection_live_schedules.disabled_reason END,
                       updated_at = excluded.updated_at""",
                (user_id, collection_id, place_name, enabled, interval, now),
            )
        return self.get_live_schedule(user_id, collection_id, now)

    def list_live_schedules(self, now, *, active_only=False):
        with self._connect() as connection:
            self._expire_live(connection, now)
            where = "WHERE schedules.enabled = 1 AND users.is_active = 1" if active_only else ""
            rows = connection.execute(
                _LIVE_ROWS + where + " ORDER BY users.username, schedules.collection_id",
            ).fetchall()
        fields = ("username", "user_id", "collection_id", "place_name")
        return [{**{field: row[field] for field in fields}, **_live_state(row, now)} for row in rows]

    def disable_live_schedule(self, user_id, collection_id, now):
        with self._connect() as connection:
            return connection.execute(
                """UPDATE collection_live_schedules
                   SET enabled = 0, disabled_reason = 'disabled_by_admin', updated_at = ?
                   WHERE user_id = ? AND collection_id = ?""",
                (now, user_id, collection_id),
            ).rowcount == 1

    def record_live_refresh(self, user_id, collection_id, place_name, now, *, automatic):
        refreshed = datetime.fromtimestamp(now, UTC).isoformat()
        with self._connect() as connection:
            if automatic:
                connection.execute(
                    """UPDATE collection_live_schedules SET last_refreshed_at = ?, updated_at = ?
                       WHERE user_id = ? AND collection_id = ?
                         AND (last_refreshed_at IS NULL OR last_refreshed_at < ?)""",
                    (refreshed, now, user_id, collection_id, refreshed),
                )
            else:
                connection.execute(
                    """INSERT INTO collection_live_schedules
                           (user_id, collection_id, place_name, last_refreshed_at, updated_at)
                       VALUES (?, ?, ?, ?, ?) ON CONFLICT (user_id, collection_id) DO UPDATE SET
                           last_refreshed_at = excluded.last_refreshed_at, updated_at = excluded.updated_at
                       WHERE collection_live_schedules.last_refreshed_at IS NULL
                          OR collection_live_schedules.last_refreshed_at < excluded.last_refreshed_at""",
                    (user_id, collection_id, place_name, refreshed, now),
                )

    def delete_live_schedule(self, user_id, collection_id):
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM collection_live_schedules WHERE user_id = ? AND collection_id = ?",
                (user_id, collection_id),
            )

    def get_user_by_username(self, username: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, username, password_hash, is_admin, is_active FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        return dict(row) if row else None

    def ensure_admin(self, username: str, password_hash: str, created_at: int) -> None:
        with self._connect() as connection:
            connection.execute("INSERT INTO users(username,password_hash,is_admin,is_active,created_at) VALUES(?,?,1,1,?) ON CONFLICT(username) DO UPDATE SET is_admin=1,is_active=1", (username, password_hash, created_at))

    def update_user_password(self, username: str, password_hash: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE users SET password_hash=? WHERE username=?", (password_hash, username))

    def list_users(self) -> list[dict]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT id, username, is_admin, is_active, created_at, last_login_at FROM users ORDER BY id"
            )]

    def set_user_access(self, user_id: int, *, is_admin: bool | None = None, is_active: bool | None = None) -> bool:
        fields, values = [], []
        if is_admin is not None:
            fields.append("is_admin=?"); values.append(int(is_admin))
        if is_active is not None:
            fields.append("is_active=?"); values.append(int(is_active))
        if not fields:
            return False
        with self._connect() as connection:
            cursor = connection.execute(f"UPDATE users SET {','.join(fields)} WHERE id=?", (*values, user_id))
            if cursor.rowcount and is_active is False:
                connection.execute("UPDATE auth_sessions SET revoked_at=COALESCE(revoked_at, strftime('%s','now')) WHERE user_id=?", (user_id,))
            return cursor.rowcount > 0

    def delete_user(self, user_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT id,username FROM users WHERE id=?", (user_id,)).fetchone()
            if row is None:
                return None
            connection.execute("DELETE FROM users WHERE id=?", (user_id,))
            return dict(row)

    def get_admin_overview(self) -> dict:
        with self._connect() as connection:
            row = connection.execute("""SELECT COUNT(*) AS total_users,
                SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END) AS active_users,
                SUM(CASE WHEN is_active=0 THEN 1 ELSE 0 END) AS disabled_users,
                SUM(CASE WHEN is_admin=1 THEN 1 ELSE 0 END) AS admin_users,
                SUM(CASE WHEN created_at >= strftime('%s','now','start of day') THEN 1 ELSE 0 END) AS new_today
                FROM users""").fetchone()
        return {key: int(value or 0) for key, value in dict(row).items()}

    def create_admin_handoff(self, ticket_hash: str, user_id: int, expires_at: int) -> None:
        with self._connect() as connection:
            connection.execute("INSERT INTO admin_handoffs(ticket_hash,user_id,expires_at) VALUES(?,?,?)", (ticket_hash,user_id,expires_at))

    def consume_admin_handoff(self, ticket_hash: str, now: int) -> dict | None:
        with self._connect() as connection:
            cursor = connection.execute("UPDATE admin_handoffs SET consumed_at=? WHERE ticket_hash=? AND consumed_at IS NULL AND expires_at>? AND user_id IN (SELECT id FROM users WHERE is_admin=1 AND is_active=1)", (now,ticket_hash,now))
            if cursor.rowcount != 1:
                return None
            row = connection.execute("SELECT users.id,users.username,users.is_admin FROM admin_handoffs JOIN users ON users.id=admin_handoffs.user_id WHERE ticket_hash=? AND consumed_at=?", (ticket_hash,now)).fetchone()
        return dict(row) if row else None

    def list_user_ids(self) -> list[int]:
        with self._connect() as connection:
            rows = connection.execute("SELECT id FROM users ORDER BY id").fetchall()
        return [int(row["id"]) for row in rows]

    def create_session(
        self, token_hash: str, user_id: int, created_at: int, expires_at: int, *, record_login: bool = False
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO auth_sessions(token_hash, user_id, created_at, expires_at)
                   VALUES (?, ?, ?, ?)""",
                (token_hash, user_id, created_at, expires_at),
            )
            if record_login:
                connection.execute(
                    """UPDATE users SET last_login_at = ?
                       WHERE id = ? AND (last_login_at IS NULL OR last_login_at < ?)""",
                    (created_at, user_id, created_at),
                )
            self._record_activity(connection, user_id, created_at)

    def get_user_for_session(self, token_hash: str, now: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT users.id, users.username, users.is_admin
                   FROM auth_sessions
                   JOIN users ON users.id = auth_sessions.user_id
                   WHERE auth_sessions.token_hash = ?
                     AND auth_sessions.revoked_at IS NULL
                     AND users.is_active = 1
                     AND auth_sessions.expires_at > ?""",
                (token_hash, now),
            ).fetchone()
        return dict(row) if row else None

    def revoke_session(self, token_hash: str, revoked_at: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE auth_sessions SET revoked_at = ?
                   WHERE token_hash = ? AND revoked_at IS NULL""",
                (revoked_at, token_hash),
            )
