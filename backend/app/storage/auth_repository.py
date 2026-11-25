from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3


class UsernameAlreadyExistsError(Exception):
    pass


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
                    created_at INTEGER NOT NULL
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
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
            if "is_admin" not in columns:
                connection.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
            if "is_active" not in columns:
                connection.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")

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
            return [dict(row) for row in connection.execute("SELECT id,username,is_admin,is_active,created_at FROM users ORDER BY id")]

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
        self, token_hash: str, user_id: int, created_at: int, expires_at: int
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO auth_sessions(token_hash, user_id, created_at, expires_at)
                   VALUES (?, ?, ?, ?)""",
                (token_hash, user_id, created_at, expires_at),
            )

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
