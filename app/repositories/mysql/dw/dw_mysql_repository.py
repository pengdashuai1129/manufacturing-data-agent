
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def json_safe_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


class DWMySQLRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_column_types(self, table_name: str) -> dict[str, str]:
        sql = f"show columns from {table_name}"
        result = await self.session.execute(text(sql))
        return {row.Field: row.Type for row in result.all()}

    async def get_column_values(
        self, table_name: str, column_name: str, limit: int = 10
    ) -> list:
        sql = f"select distinct {column_name} from {table_name} limit {limit}"
        result = await self.session.execute(text(sql))
        return [json_safe_value(v) for v in result.scalars().all()]

    async def get_db_info(self) -> dict[str, str]:
        sql = "select version()"
        result = await self.session.execute(text(sql))
        version = result.scalar()
        dialect = self.session.bind.dialect.name
        return {"dialect": dialect, "version": version}

    async def validate(self, sql: str) -> None:
        explain_sql = f"explain {sql}"
        await self.session.execute(text(explain_sql))

    async def run(self, sql: str) -> list[dict]:
        result = await self.session.execute(text(sql))
        return [dict(row) for row in result.mappings().fetchall()]
