from hashlib import sha256
import secrets
import time

from pwdlib import PasswordHash

from backend.app.storage.auth_repository import AuthRepository


SESSION_LIFETIME_SECONDS = 12 * 60 * 60
_password_hasher = PasswordHash.recommended()


class InvalidCredentialsError(Exception):
    pass


class InvalidSessionError(Exception):
    pass


class AuthService:
    def __init__(self, repository: AuthRepository) -> None:
        self._repository = repository

    def provision_admin(self, username: str, password: str) -> None:
        normalized = username.casefold()
        user = self._repository.get_user_by_username(normalized)
        if user is None:
            self._repository.ensure_admin(normalized, _password_hasher.hash(password), int(time.time()))
        else:
            self._repository.ensure_admin(normalized, user["password_hash"], int(time.time()))

    def set_admin_password(self, username: str, password: str) -> None:
        normalized = username.casefold()
        user = self._repository.get_user_by_username(normalized)
        password_hash = _password_hasher.hash(password)
        if user is None:
            self._repository.ensure_admin(normalized, password_hash, int(time.time()))
        else:
            self._repository.update_user_password(normalized, password_hash)
            self._repository.ensure_admin(normalized, password_hash, int(time.time()))

    def create_admin_handoff(self, user_id: int) -> str:
        ticket = secrets.token_urlsafe(32)
        self._repository.create_admin_handoff(self._token_hash(ticket), user_id, int(time.time()) + 300)
        return ticket

    def redeem_admin_handoff(self, ticket: str) -> dict:
        user = self._repository.consume_admin_handoff(self._token_hash(ticket), int(time.time()))
        if user is None:
            raise InvalidSessionError
        return self._create_session(int(user["id"]), user["username"], int(time.time()))

    def register(self, username: str, password: str) -> dict:
        now = int(time.time())
        normalized_username = username.casefold()
        password_hash = _password_hasher.hash(password)
        user_id = self._repository.create_user(
            normalized_username, password_hash, now
        )
        return self._create_session(user_id, normalized_username, now)

    def admin_create_user(self, username: str, password: str, *, is_admin: bool = False) -> dict:
        now = int(time.time())
        username = username.casefold()
        user_id = self._repository.create_user(username, _password_hasher.hash(password), now, is_admin=is_admin)
        return dict(id=user_id, username=username, is_admin=is_admin, is_active=True,
                    created_at=now, last_login_at=None, last_active_at=None)

    def admin_reset_password(self, user_id: int, password: str, *, revoke_sessions: bool = True) -> int | None:
        return self._repository.reset_user_password(
            user_id, _password_hasher.hash(password), int(time.time()), revoke_sessions=revoke_sessions,
        )

    def login(self, username: str, password: str) -> dict:
        user = self._repository.get_user_by_username(username.casefold())
        if user is None or not user["is_active"] or not _password_hasher.verify(password, user["password_hash"]):
            raise InvalidCredentialsError
        try:
            return self._create_session(
                int(user["id"]), user["username"], int(time.time()),
                record_login=True, verified_password_hash=user["password_hash"],
            )
        except InvalidSessionError as exc:
            raise InvalidCredentialsError from exc

    def authenticate(self, access_token: str) -> dict:
        now = int(time.time())
        user = self._repository.get_user_for_session(
            self._token_hash(access_token), now
        )
        if user is None:
            raise InvalidSessionError
        return user

    def logout(self, access_token: str) -> None:
        self._repository.revoke_session(
            self._token_hash(access_token), int(time.time())
        )

    def _create_session(self, user_id: int, username: str, now: int, *, record_login: bool = False,
                        verified_password_hash: str | None = None) -> dict:
        access_token = secrets.token_urlsafe(32)
        expires_at = now + SESSION_LIFETIME_SECONDS
        created = self._repository.create_session(
            self._token_hash(access_token), user_id, now, expires_at,
            record_login=record_login, verified_password_hash=verified_password_hash,
        )
        if not created:
            raise InvalidSessionError
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "username": username,
            "expires_at": expires_at,
            "is_admin": bool((self._repository.get_user_by_username(username) or {}).get("is_admin", False)),
        }

    @staticmethod
    def _token_hash(access_token: str) -> str:
        return sha256(access_token.encode("utf-8")).hexdigest()
