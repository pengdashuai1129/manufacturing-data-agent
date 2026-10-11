
import random
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.core.context import request_id_ctx_var
from app.core.logging import logger
from app.entities.user import User
from app.repositories.mysql.app.app_mysql_repository import AppMySQLRepository
from app.repositories.mysql.dw.dw_fact_write_repository import DWFactsWriteRepository
from app.repositories.mysql.dw.dw_mysql_repository import json_safe_value
from app.services import fact_derive_service as dv
from app.services.data_quality_service import InvariantChecker, failures

CURRENCIES = ("USD", "CNY")

DEFAULT_LEAD_DAYS = 20

QUOTE_LEAD_DAYS = (5, 45)

ALLOWED_EXPLICIT_STATUS = (dv.STATUS_IN_PRODUCTION, dv.STATUS_DONE)

MAX_ITEMS = 100


class FactWriteError(Exception):

    def __init__(self, errors: list[tuple[str, str]]):
        self.errors = errors
        super().__init__("；".join(f"{f}: {m}" for f, m in errors))



def _txt(v) -> str:
    return str(v).strip() if v is not None else ""


def _date_id(v, field: str, errors: list) -> int | None:
    s = _txt(v)
    if not s:
        return None
    try:
        return dv.d2id(date.fromisoformat(s[:10]))
    except ValueError:
        errors.append((field, f"日期看不懂：{v!r}（要 YYYY-MM-DD）"))
        return None


def _int(v, field: str, errors: list) -> int | None:
    s = _txt(v)
    if not s:
        return None
    try:
        n = int(float(s))
    except ValueError:
        errors.append((field, f"要填整数，收到 {v!r}"))
        return None
    return n


def _price(v, field: str, errors: list) -> float | None:
    s = _txt(v)
    if not s:
        return None
    try:
        p = float(s)
    except ValueError:
        errors.append((field, f"要填数字，收到 {v!r}"))
        return None
    return round(p, 2)


def _fmt(date_id) -> str:
    return dv.id2date(date_id).isoformat() if date_id else "空"


def _jsonable(v):
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (Decimal, date, datetime)):
        return json_safe_value(v)
    return v


def _bump_until_free(candidate: str, reserved: set, width: int = 0) -> str:
    head, sep, tail = candidate.rpartition("-")
    if not sep or not tail.isdigit():
        return candidate

    def fmt(k: int) -> str:
        return f"{head}-{k:0{width}d}" if width else f"{head}-{k}"

    n = int(tail)
    while fmt(n) in reserved:
        n += 1
    return fmt(n)



class FactWriteService:
    def __init__(
        self,
        write_repository: DWFactsWriteRepository,
        app_mysql_repository: AppMySQLRepository,
    ):
        self.repo = write_repository
        self.app_repo = app_mysql_repository
        self.rng = random.Random()


    @asynccontextmanager
    async def _transaction(self):
        session = self.repo.session
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    async def create_order(self, payload: dict, user: User) -> dict:
        prep = await self._prepare(payload, order_id=None, is_update=False)
        async with self._transaction():
            await self._persist(prep)
        await self._log_change(prep, user)
        return self._summary(prep)

    async def update_order(self, order_id: str, payload: dict, user: User) -> dict:
        prep = await self._prepare(payload, order_id=order_id, is_update=True)
        async with self._transaction():
            await self._persist(prep)
        await self._log_change(prep, user)
        return self._summary(prep)

    async def _prepare_batch(self, payloads: list[dict]) -> list[dict]:
        prepared, errors = [], []
        reserved_orders: set[str] = set()
        reserved_quotes: set[str] = set()
        for lineno, payload in enumerate(payloads, start=1):
            try:
                prepared.append(await self._prepare(
                    payload, order_id=None, is_update=False,
                    reserved_orders=reserved_orders, reserved_quotes=reserved_quotes))
            except FactWriteError as e:
                errors += [(f"第 {lineno} 行 · {f}", m) for f, m in e.errors]
        if errors:
            raise FactWriteError(errors)
        return prepared

    async def preview_import(self, payloads: list[dict]) -> dict:
        prepared = await self._prepare_batch(payloads)
        return {"dry_run": True, "orders": len(prepared),
                "results": [self._summary(p) for p in prepared]}

    async def import_orders(self, payloads: list[dict], user: User) -> dict:
        prepared = await self._prepare_batch(payloads)

        async with self._transaction():
            for prep in prepared:
                await self._persist(prep)
        for prep in prepared:
            await self._log_change(prep, user, commit=False)
        await self.app_repo.session.commit()

        return {"orders": len(prepared), "results": [self._summary(p) for p in prepared]}

    async def get_order(self, order_id: str) -> dict:
        order = await self.repo.get_order(order_id)
        if order is None:
            raise FactWriteError([("order_id", f"合同 {order_id} 不存在")])
        items = await self.repo.get_items(order_id)
        prod = await self.repo.get_production(order_id)
        procs = await self.repo.get_processes([r["production_id"] for r in prod])
        return _jsonable({"order": order, "items": items,
                          "production": prod, "processes": procs})


    async def _prepare(self, payload: dict, order_id: str | None,
                       is_update: bool, reserved_orders: set | None = None,
                       reserved_quotes: set | None = None) -> dict:
        reserved_orders = reserved_orders if reserved_orders is not None else set()
        reserved_quotes = reserved_quotes if reserved_quotes is not None else set()
        errors: list[tuple[str, str]] = []

        dims = await self.repo.load_dimensions()
        valid_dates = await self.repo.valid_date_ids()
        emp_by_id = {r["employee_id"]: r for r in dims["dim_employee"]}
        cust_by_id = {r["customer_id"]: r for r in dims["dim_customer"]}
        prod_by_id = {r["product_id"]: r for r in dims["dim_product"]}

        employee_id = _txt(payload.get("employee_id"))
        customer_id = _txt(payload.get("customer_id"))
        currency = (_txt(payload.get("currency")) or "USD").upper()
        order_date_id = _date_id(payload.get("order_date"), "order_date", errors)
        ship_date_id = _date_id(payload.get("ship_date"), "ship_date", errors)
        actual_ship_date_id = _date_id(
            payload.get("actual_ship_date"), "actual_ship_date", errors)
        delay_reason = _txt(payload.get("delay_reason")) or None
        explicit_status = _txt(payload.get("order_status")) or None

        if order_id is None:
            order_id = _txt(payload.get("order_id"))
            if not order_id and employee_id:
                order_id = await self.repo.next_order_id(employee_id)
                if order_id is None:
                    errors.append(("order_id",
                                   "该业务员名下还没有任何合同，无法自动生成单号，请手填"))
                else:
                    order_id = _bump_until_free(order_id, reserved_orders)
        if order_id and not (1 <= len(order_id) <= 40):
            errors.append(("order_id", "合同号长度要在 1~40 之间"))
        if order_id:
            reserved_orders.add(order_id)

        existing = await self.repo.get_order(order_id) if order_id else None
        if is_update and existing is None:
            raise FactWriteError([("order_id", f"合同 {order_id} 不存在")])
        if not is_update and existing is not None:
            raise FactWriteError([("order_id", f"合同 {order_id} 已存在，请改用修改")])

        if employee_id not in emp_by_id:
            errors.append(("employee_id", f"员工 {employee_id!r} 不在员工档案里"))
        elif emp_by_id[employee_id]["employee_role"] != "业务员":
            errors.append(("employee_id",
                           f"{emp_by_id[employee_id]['employee_role']}不能作为合同负责人，"
                           f"合同必须是业务员签的"))
        if customer_id not in cust_by_id:
            errors.append(("customer_id", f"客户 {customer_id!r} 不在客户档案里"))
        if currency not in CURRENCIES:
            errors.append(("currency", f"币种只能是 {'/'.join(CURRENCIES)}"))
        if order_date_id is None:
            errors.append(("order_date", "下单日期必填"))
        if ship_date_id and order_date_id and order_date_id > ship_date_id:
            errors.append(("ship_date",
                           f"承诺交期({_fmt(ship_date_id)})不能早于下单日期({_fmt(order_date_id)})"))
        if actual_ship_date_id and not ship_date_id:
            errors.append(("ship_date", "填了实际发货日期就必须有承诺交期，否则算不出延期天数"))
        if explicit_status and explicit_status not in ALLOWED_EXPLICIT_STATUS:
            errors.append(("order_status",
                           f"状态只能填 {' / '.join(ALLOWED_EXPLICIT_STATUS)}；"
                           f"「已发货」由实际发货日期决定，不能直接指定"))

        raw_items = payload.get("items") or []
        if not isinstance(raw_items, list) or not raw_items:
            errors.append(("items", "至少要有一行产品明细"))
            raw_items = []
        if len(raw_items) > MAX_ITEMS:
            errors.append(("items", f"一张合同最多 {MAX_ITEMS} 行明细"))

        items: list[dict] = []
        for k, raw in enumerate(raw_items, start=1):
            where = f"items[{k}]"
            pid = _txt(raw.get("product_id"))
            qty = _int(raw.get("quantity"), f"{where}.quantity", errors)
            price = _price(raw.get("unit_price"), f"{where}.unit_price", errors)
            if pid not in prod_by_id:
                errors.append((f"{where}.product_id", f"品号 {pid!r} 不在产品档案里"))
            if qty is None or qty <= 0:
                errors.append((f"{where}.quantity", "数量必须是大于 0 的整数"))
            if price is None or price <= 0:
                errors.append((f"{where}.unit_price", "单价必须大于 0"))
            if pid not in prod_by_id or qty is None or qty <= 0 or price is None or price <= 0:
                continue
            product = prod_by_id[pid]
            items.append({
                "item_id": f"{order_id}-{k:02d}",
                "order_id": order_id,
                "product_id": pid,
                "quantity": qty,
                "total_weight": dv.derive_total_weight(product["unit_weight"], qty),
                "amount": dv.derive_item_amount(price, qty),
            })

        if errors:
            raise FactWriteError(errors)

        amount_cny = sum(dv.amount_in_cny(r["amount"], currency) for r in items)
        contract_type = dv.derive_contract_type(amount_cny)
        delay_days, is_on_time = dv.derive_delay(ship_date_id, actual_ship_date_id)
        if delay_days is not None and delay_days > 0 and not delay_reason:
            errors.append(("delay_reason", f"延期了 {delay_days} 天，必须填延期原因"))
        if (delay_days or 0) <= 0:
            delay_reason = None
        if errors:
            raise FactWriteError(errors)

        old_production = await self.repo.get_production(order_id) if existing else []
        old_processes = await self.repo.get_processes(
            [r["production_id"] for r in old_production])
        prod_rows, proc_rows = self._build_production(
            payload, order_id, items, prod_by_id, order_date_id, ship_date_id,
            existing["ship_date_id"] if existing else None,
            old_production, old_processes, errors)
        if errors:
            raise FactWriteError(errors)

        order_row = {
            "order_id": order_id,
            "customer_id": customer_id,
            "employee_id": employee_id,
            "order_date_id": order_date_id,
            "ship_date_id": ship_date_id,
            "actual_ship_date_id": actual_ship_date_id,
            "currency": currency,
            "contract_type": contract_type,
            "order_status": dv.derive_order_status(
                actual_ship_date_id,
                has_production_done=any(r["actual_done_date_id"] for r in prod_rows),
                requested=explicit_status),
            "delay_days": delay_days,
            "is_on_time": is_on_time,
            "delay_reason": delay_reason,
        }

        quotations = await self._sync_winning_quotation(
            order_row, items, existing, errors, reserved_quotes)
        if errors:
            raise FactWriteError(errors)

        for field, did in (("order_date", order_date_id), ("ship_date", ship_date_id),
                           ("actual_ship_date", actual_ship_date_id)):
            if did is not None and did not in valid_dates:
                errors.append((field, f"{_fmt(did)} 超出日历范围（dim_date 到 2026-12-31）"))
        for r in prod_rows:
            for col in ("plan_done_date_id", "actual_done_date_id"):
                if r[col] is not None and r[col] not in valid_dates:
                    errors.append((f"production.{col}",
                                   f"{_fmt(r[col])} 超出日历范围（{r['production_id']}）"))
        for r in proc_rows:
            if r["process_done_date_id"] not in valid_dates:
                errors.append(("production.process_done_date_id",
                               f"{_fmt(r['process_done_date_id'])} 超出日历范围（{r['process_id']}）"))
        for r in quotations:
            if r["quote_date_id"] not in valid_dates:
                errors.append(("quotation.quote_date_id",
                               f"{_fmt(r['quote_date_id'])} 超出日历范围"))
        if errors:
            raise FactWriteError(errors)

        self._assert_slice(dims, valid_dates, order_row, items, prod_rows, proc_rows,
                           quotations)

        return {
            "order_row": order_row,
            "items": items,
            "prod_rows": prod_rows,
            "proc_rows": proc_rows,
            "quotations": quotations,
            "existing": existing,
            "action": "update" if existing else "insert",
            "contract_type": contract_type,
            "amount_cny": amount_cny,
        }


    async def _persist(self, prep: dict):
        order_id = prep["order_row"]["order_id"]
        await self.repo.upsert_order(prep["order_row"])
        await self.repo.delete_items(order_id)
        await self.repo.upsert_items(prep["items"])
        await self.repo.delete_production(order_id)
        await self.repo.upsert_production(prep["prod_rows"])
        await self.repo.upsert_processes(prep["proc_rows"])
        await self.repo.upsert_quotations(prep["quotations"])

    def _summary(self, prep: dict) -> dict:
        return {
            "order_id": prep["order_row"]["order_id"],
            "action": prep["action"],
            "items": len(prep["items"]),
            "production": len(prep["prod_rows"]),
            "processes": len(prep["proc_rows"]),
            "contract_type": prep["contract_type"],
            "order_status": prep["order_row"]["order_status"],
            "amount": round(sum(r["amount"] for r in prep["items"]), 2),
            "amount_cny": round(prep["amount_cny"], 2),
        }


    def _build_production(self, payload, order_id, items, prod_by_id,
                          order_date_id, ship_date_id, old_ship_date_id,
                          old_production, old_processes,
                          errors) -> tuple[list[dict], list[dict]]:
        if old_production and not isinstance(payload.get("production"), (dict, list)):
            return self._shift_plan_dates(old_production, ship_date_id,
                                          old_ship_date_id), old_processes

        finished = [r for r in items if prod_by_id[r["product_id"]]["product_type"] == "成品"]
        if not finished:
            return [], []

        prod_cfg = payload.get("production") or {}
        if isinstance(prod_cfg, list):
            prod_cfg = prod_cfg[0] if prod_cfg else {}

        plan_done_id = _date_id(prod_cfg.get("plan_done_date"),
                                "production.plan_done_date", errors)
        actual_done_id = _date_id(prod_cfg.get("actual_done_date"),
                                  "production.actual_done_date", errors)
        if plan_done_id is None:
            base = ship_date_id or (dv.d2id(dv.id2date(order_date_id) + timedelta(days=45))
                                    if order_date_id else None)
            if base is None:
                errors.append(("production.plan_done_date", "无法推算计划完工日，请手填"))
                return [], []
            plan_done_id = dv.d2id(dv.id2date(base) - timedelta(days=DEFAULT_LEAD_DAYS))

        prod_reason = _txt(prod_cfg.get("delay_reason")) or None
        prod_rows, proc_rows = [], []
        for k, it in enumerate(finished, start=1):
            pid = f"{order_id}-{k:02d}"
            delay, on_time = dv.derive_delay(plan_done_id, actual_done_id)
            if (delay or 0) > 0 and not prod_reason:
                errors.append(("production.delay_reason",
                               f"生产延期了 {delay} 天，必须填延期原因"))
                continue
            prod_rows.append({
                "production_id": pid,
                "order_id": order_id,
                "product_id": it["product_id"],
                "plan_quantity": it["quantity"],
                "output_quantity": int(it["quantity"] * self.rng.uniform(0.96, 1.02))
                if actual_done_id else 0,
                "plan_done_date_id": plan_done_id,
                "actual_done_date_id": actual_done_id,
                "delay_days": delay,
                "is_on_time": on_time,
                "delay_reason": prod_reason if (delay or 0) > 0 else None,
            })
            proc_rows += dv.build_process_rows(
                pid, prod_by_id[it["product_id"]], it["quantity"],
                dv.id2date(plan_done_id), date.today(), self.rng, delay)
        return prod_rows, proc_rows

    def _shift_plan_dates(self, old_production, ship_date_id, old_ship_date_id) -> list[dict]:
        rows = []
        for r in old_production:
            plan = r["plan_done_date_id"]
            if ship_date_id and old_ship_date_id and plan:
                delta = (dv.id2date(ship_date_id) - dv.id2date(old_ship_date_id)).days
                plan = dv.d2id(dv.id2date(plan) + timedelta(days=delta))
            delay, on_time = dv.derive_delay(plan, r["actual_done_date_id"])
            rows.append({
                "production_id": r["production_id"],
                "order_id": r["order_id"],
                "product_id": r["product_id"],
                "plan_quantity": r["plan_quantity"],
                "output_quantity": r["output_quantity"] or 0,
                "plan_done_date_id": plan,
                "actual_done_date_id": r["actual_done_date_id"],
                "delay_days": delay,
                "is_on_time": on_time,
                "delay_reason": r["delay_reason"] if (delay or 0) > 0 else None,
            })
        return rows


    async def _sync_winning_quotation(self, order_row, items, existing, errors,
                                      reserved_quotes: set) -> list[dict]:
        order_id = order_row["order_id"]
        rows = await self.repo.get_quotations(order_id)
        winner = next((r for r in rows if r["quote_status"] == "中标"), None)

        if winner is not None:
            winner = dict(winner)
            winner["customer_id"] = order_row["customer_id"]
            winner["employee_id"] = order_row["employee_id"]
            winner["currency"] = order_row["currency"]
            return [winner]

        if not items:
            return []

        first = items[0]
        prefix = order_id.split("-")[0]
        quote_date = dv.id2date(order_row["order_date_id"]) \
            - timedelta(days=self.rng.randint(*QUOTE_LEAD_DAYS))
        if dv.d2id(quote_date) < 20250101:
            quote_date = date(2025, 1, 1)
        quotation_id = _bump_until_free(
            await self.repo.next_quotation_id(prefix), reserved_quotes, width=4)
        reserved_quotes.add(quotation_id)
        return [{
            "quotation_id": quotation_id,
            "customer_id": order_row["customer_id"],
            "product_id": first["product_id"],
            "employee_id": order_row["employee_id"],
            "quote_date_id": dv.d2id(quote_date),
            "currency": order_row["currency"],
            "quote_quantity": first["quantity"],
            "quote_amount": round(first["amount"] * self.rng.uniform(1.02, 1.15), 2),
            "quote_status": "中标",
            "lost_reason": None,
            "order_id": order_id,
        }]


    def _assert_slice(self, dims, valid_dates, order_row, items, prod_rows, proc_rows,
                      quotations):
        slice_ = {
            **dims,
            "fact_order": [order_row],
            "fact_order_item": items,
            "fact_production": prod_rows,
            "fact_production_process": proc_rows,
            "fact_quotation": quotations,
            "fact_sample": [],
        }
        checker = InvariantChecker(as_of=dv.d2id(date.today()))
        checker.run_all(slice_)
        checker.check_dates_in_dim(slice_, valid_dates)

        bad = failures(checker.results)
        if bad:
            raise FactWriteError([
                ("不变量", f"{r.name}（{r.total} 例：{r.samples[0] if r.samples else ''}）")
                for r in bad
            ])

    async def _log_change(self, prep: dict, user: User, commit: bool = True):
        order_row = prep["order_row"]
        try:
            self.app_repo.add_change_log(
                table_name="fact_order",
                pk_value=order_row["order_id"],
                action=prep["action"],
                before=_jsonable(prep["existing"]),
                after=_jsonable({
                    "order": order_row,
                    "items": prep["items"],
                    "production": prep["prod_rows"],
                    "processes": prep["proc_rows"],
                    "winning_quotation": prep["quotations"],
                }),
                user_id=user.user_id,
                created_at=datetime.now(),
            )
            if commit:
                await self.app_repo.session.commit()
        except Exception:
            logger.exception("写数据变更日志失败（数据已提交，未回滚）：{}",
                             order_row["order_id"])
