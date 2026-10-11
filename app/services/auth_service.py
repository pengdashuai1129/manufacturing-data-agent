
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from app.core.context import request_id_ctx_var
from app.core.logging import logger
from app.entities.user import User
from app.repositories.mysql.app.app_mysql_repository import AppMySQLRepository

_HASH_ALGO = "pbkdf2_sha256"
_ITERATIONS = 200_000
_SALT_BYTES = 16
_TOKEN_BYTES = 32

SESSION_TTL_HOURS = 12

SESSION_COOKIE_NAME = "session_id"


def hash_password(password: str) -> str:

    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{_HASH_ALGO}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:

    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
        if algo != _HASH_ALGO:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, AttributeError):
        return False

    return hmac.compare_digest(digest.hex(), digest_hex)


class AuthService:

    def __init__(self, app_mysql_repository: AppMySQLRepository):
        self.app_mysql_repository = app_mysql_repository

    async def login(self, username: str, password: str) -> tuple[str, User] | None:

        user = await self.app_mysql_repository.get_user_by_username(username)

        if user is None or not verify_password(password, user.password_hash):
            logger.warning("登录失败：{}", username)
            return None

        if not user.is_active:
            logger.warning("登录失败（账号已停用）：{}", username)
            return None

        token = secrets.token_urlsafe(_TOKEN_BYTES)
        now = datetime.now()
        self.app_mysql_repository.add_session(
            token=token,
            user_id=user.user_id,
            created_at=now,
            expires_at=now + timedelta(hours=SESSION_TTL_HOURS),
        )
        await self.app_mysql_repository.delete_expired_sessions(now)
        await self.app_mysql_repository.session.commit()

        logger.info("登录成功：{}({})", user.display_name or user.username, user.role)
        return token, user

    async def logout(self, token: str | None):

        if not token:
            return
        await self.app_mysql_repository.delete_session(token)
        await self.app_mysql_repository.session.commit()

    async def get_user_by_token(self, token: str | None) -> User | None:

        if not token:
            return None

        session = await self.app_mysql_repository.get_valid_session(token, datetime.now())
        if session is None:
            return None

        user = await self.app_mysql_repository.get_user_by_id(session.user_id)
        if user is None or not user.is_active:
            return None
        return user


def current_request_id() -> str:

    return request_id_ctx_var.get()
