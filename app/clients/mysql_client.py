import asyncio
from sqlalchemy import text

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
)

from app.conf.app_config import DBConfig, app_config


class MySQLClientManager:

    def __init__(self, config: DBConfig):
        self.config = config
        self.engine = None
        self.session_factory = None

    def _get_url(self):
        return (
            f"mysql+asyncmy://{self.config.user}:{self.config.password}"
            f"@{self.config.host}:{self.config.port}/{self.config.database}"
            f"?charset=utf8mb4"
        )

    def init(self):
        self.engine = create_async_engine(
            self._get_url(),
            pool_size=10,
            pool_pre_ping=True,
        )

        self.session_factory = async_sessionmaker(
            self.engine,
            autoflush=True,
            expire_on_commit=False,
        )

    async def close(self):
        await self.engine.dispose()


meta_mysql_client_manager = MySQLClientManager(app_config.db_meta)

dw_mysql_client_manager = MySQLClientManager(app_config.db_dw)

app_mysql_client_manager = MySQLClientManager(app_config.db_app)


if __name__ == "__main__":
    dw_mysql_client_manager.init()

    async def _test():
        async with dw_mysql_client_manager.session_factory() as session:

            result = await session.execute(
                text(
                    "SELECT o.order_id, o.order_status, o.is_on_time, d.year, d.month "
                    "FROM fact_order o "
                    "JOIN dim_date d ON o.order_date_id = d.date_id "
                    "ORDER BY o.order_date_id DESC LIMIT 10"
                )
            )

            rows = result.mappings().fetchall()

            for row in rows:
                print(
                    f"  {row['order_id']} | {row['order_status']} | "
                    f"{row['is_on_time']} | {row['year']}-{row['month']:02d}"
                )

        await dw_mysql_client_manager.close()
        print(f"\nMySQL 客户端测试通过 ✓ ({len(rows)} 条)")

    asyncio.run(_test())
