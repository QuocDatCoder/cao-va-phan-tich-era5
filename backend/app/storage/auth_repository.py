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
                """
            )

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
                "SELECT id, username, password_hash FROM users WHERE username = ?",
                (username,),
            ).fetchone()
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
                """SELECT users.id, users.username
                   FROM auth_sessions
                   JOIN users ON users.id = auth_sessions.user_id
                   WHERE auth_sessions.token_hash = ?
                     AND auth_sessions.revoked_at IS NULL
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
