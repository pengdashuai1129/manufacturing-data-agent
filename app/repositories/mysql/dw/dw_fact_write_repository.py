
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ORDER_COLS = ["order_id", "customer_id", "employee_id", "order_date_id", "ship_date_id",
              "actual_ship_date_id", "currency", "contract_type", "order_status",
              "delay_days", "is_on_time", "delay_reason"]
ITEM_COLS = ["item_id", "order_id", "product_id", "quantity", "total_weight", "amount"]
PROD_COLS = ["production_id", "order_id", "product_id", "plan_quantity", "output_quantity",
             "plan_done_date_id", "actual_done_date_id", "delay_days", "is_on_time",
             "delay_reason"]
PROC_COLS = ["process_id", "production_id", "workshop_id", "process_seq",
             "process_plan_quantity", "process_output", "defect_quantity", "defect_type",
             "process_done_date_id"]
QUOT_COLS = ["quotation_id", "customer_id", "product_id", "employee_id", "quote_date_id",
             "currency", "quote_quantity", "quote_amount", "quote_status", "lost_reason",
             "order_id"]

DIM_TABLES = ["dim_customer", "dim_product", "dim_employee", "dim_workshop"]


class DWFactsWriteRepository:

    def __init__(self, session: AsyncSession):
        self.session = session


    async def _rows(self, sql: str, **params) -> list[dict]:
        result = await self.session.execute(text(sql), params)
        return [dict(r) for r in result.mappings().all()]

    async def get_order(self, order_id: str) -> dict[str, Any] | None:
        rows = await self._rows("SELECT * FROM fact_order WHERE order_id = :oid", oid=order_id)
        return rows[0] if rows else None

    async def get_items(self, order_id: str) -> list[dict]:
        return await self._rows(
            "SELECT * FROM fact_order_item WHERE order_id = :oid ORDER BY item_id", oid=order_id)

    async def get_production(self, order_id: str) -> list[dict]:
        return await self._rows(
            "SELECT * FROM fact_production WHERE order_id = :oid ORDER BY production_id",
            oid=order_id)

    async def get_processes(self, production_ids: list[str]) -> list[dict]:
        if not production_ids:
            return []
        return await self._rows(
            "SELECT * FROM fact_production_process WHERE production_id IN :ids "
            "ORDER BY production_id, process_seq",
            ids=tuple(production_ids))

    async def get_quotations(self, order_id: str) -> list[dict]:
        return await self._rows(
            "SELECT * FROM fact_quotation WHERE order_id = :oid", oid=order_id)

    async def valid_date_ids(self) -> set[int]:
        rows = await self._rows("SELECT date_id FROM dim_date")
        return {r["date_id"] for r in rows}

    async def load_dimensions(self) -> dict[str, list[dict]]:
        return {t: await self._rows(f"SELECT * FROM {t}") for t in DIM_TABLES}

    async def recent_orders(self, limit: int = 50) -> list[dict]:
        return await self._rows(
            "SELECT order_id, customer_id, employee_id, order_date_id, ship_date_id, "
            "       currency, contract_type, order_status, delay_days "
            "FROM fact_order ORDER BY order_id DESC LIMIT :n", n=int(limit))

    async def next_order_id(self, employee_id: str) -> str | None:
        rows = await self._rows(
            "SELECT SUBSTRING_INDEX(order_id, '-', 1) AS prefix, "
            "       MAX(CAST(SUBSTRING_INDEX(order_id, '-', -1) AS UNSIGNED)) AS max_seq "
            "FROM fact_order WHERE employee_id = :eid "
            "GROUP BY prefix ORDER BY max_seq DESC LIMIT 1", eid=employee_id)
        if not rows or not rows[0]["prefix"]:
            return None
        return f"{rows[0]['prefix']}-{int(rows[0]['max_seq'] or 0) + 1}"

    async def next_quotation_id(self, order_prefix: str) -> str:
        rows = await self._rows(
            "SELECT MAX(CAST(SUBSTRING_INDEX(quotation_id, '-', -1) AS UNSIGNED)) AS m "
            "FROM fact_quotation")
        return f"BJ-{order_prefix}-{int(rows[0]['m'] or 0) + 1:04d}"


    @staticmethod
    def _upsert_sql(table: str, cols: list[str]) -> str:
        names = ", ".join(cols)
        binds = ", ".join(f":{c}" for c in cols)
        updates = ", ".join(f"{c} = VALUES({c})" for c in cols[1:])
        return f"INSERT INTO {table} ({names}) VALUES ({binds}) ON DUPLICATE KEY UPDATE {updates}"

    async def _upsert_many(self, table: str, cols: list[str], rows: list[dict]):
        if not rows:
            return
        payload = [{c: r.get(c) for c in cols} for r in rows]
        await self.session.execute(text(self._upsert_sql(table, cols)), payload)

    async def upsert_order(self, row: dict):
        await self._upsert_many("fact_order", ORDER_COLS, [row])

    async def upsert_items(self, rows: list[dict]):
        await self._upsert_many("fact_order_item", ITEM_COLS, rows)

    async def upsert_production(self, rows: list[dict]):
        await self._upsert_many("fact_production", PROD_COLS, rows)

    async def upsert_processes(self, rows: list[dict]):
        await self._upsert_many("fact_production_process", PROC_COLS, rows)

    async def upsert_quotations(self, rows: list[dict]):
        await self._upsert_many("fact_quotation", QUOT_COLS, rows)

    async def delete_items(self, order_id: str):
        await self.session.execute(
            text("DELETE FROM fact_order_item WHERE order_id = :oid"), {"oid": order_id})

    async def delete_production(self, order_id: str):
        await self.session.execute(
            text("DELETE FROM fact_production_process WHERE production_id IN "
                 "(SELECT production_id FROM fact_production WHERE order_id = :oid)"),
            {"oid": order_id})
        await self.session.execute(
            text("DELETE FROM fact_production WHERE order_id = :oid"), {"oid": order_id})
