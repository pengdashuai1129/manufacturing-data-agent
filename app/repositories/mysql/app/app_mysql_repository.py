
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.user import User
from app.models.data_change_log import DataChangeLogMySQL
from app.models.query_log import QueryLogMySQL
from app.models.sys_session import SysSessionMySQL
from app.models.sys_user import SysUserMySQL
from app.repositories.mysql.app.mappers.user_mapper import UserMapper


class AppMySQLRepository:

    def __init__(self, session: AsyncSession):
        self.session = session


    async def get_user_by_username(self, username: str) -> User | None:

        stmt = select(SysUserMySQL).where(SysUserMySQL.username == username)
        result = await self.session.execute(stmt)
        model = result.scalars().first()
        return UserMapper.to_entity(model) if model else None

    async def get_user_by_id(self, user_id: str) -> User | None:

        model: SysUserMySQL | None = await self.session.get(SysUserMySQL, user_id)
        return UserMapper.to_entity(model) if model else None

    async def list_users(self) -> list[User]:

        result = await self.session.execute(select(SysUserMySQL).order_by(SysUserMySQL.user_id))
        return [UserMapper.to_entity(model) for model in result.scalars().all()]

    def add_user(self, user: User, created_at: datetime):

        self.session.add(UserMapper.to_model(user, created_at))


    def add_session(self, token: str, user_id: str, created_at: datetime, expires_at: datetime):

        self.session.add(
            SysSessionMySQL(
                token=token,
                user_id=user_id,
                created_at=created_at,
                expires_at=expires_at,
            )
        )

    async def get_valid_session(self, token: str, now: datetime) -> SysSessionMySQL | None:

        stmt = select(SysSessionMySQL).where(
            SysSessionMySQL.token == token,
            SysSessionMySQL.expires_at > now,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def delete_session(self, token: str):

        await self.session.execute(
            delete(SysSessionMySQL)
            .where(SysSessionMySQL.token == token)
            .execution_options(synchronize_session=False)
        )

    async def delete_expired_sessions(self, now: datetime):

        await self.session.execute(
            delete(SysSessionMySQL)
            .where(SysSessionMySQL.expires_at <= now)
            .execution_options(synchronize_session=False)
        )


    def add_query_log(
        self,
        user_id: str | None,
        question: str | None,
        final_sql: str | None,
        status: str,
        row_count: int | None,
        elapsed_ms: int | None,
        request_id: str | None,
        created_at: datetime,
    ):

        self.session.add(
            QueryLogMySQL(
                user_id=user_id,
                question=question,
                final_sql=final_sql,
                status=status,
                row_count=row_count,
                elapsed_ms=elapsed_ms,
                request_id=request_id,
                created_at=created_at,
            )
        )

    def add_change_log(
        self,
        table_name: str,
        pk_value: str,
        action: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        user_id: str | None,
        created_at: datetime,
    ):

        self.session.add(
            DataChangeLogMySQL(
                table_name=table_name,
                pk_value=pk_value,
                action=action,
                before_json=before,
                after_json=after,
                user_id=user_id,
                created_at=created_at,
            )
        )
