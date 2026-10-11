
import csv
import io
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.api.dependencies import get_fact_write_service, require_writer
from app.api.schemas.order_schema import OrderUpsertSchema
from app.entities.user import User
from app.services.fact_write_service import FactWriteError, FactWriteService

order_router = APIRouter()

CSV_HEADERS = [
    "order_id", "customer_id", "employee_id", "order_date", "ship_date",
    "actual_ship_date", "currency", "order_status", "delay_reason",
    "product_id", "quantity", "unit_price",
    "plan_done_date", "actual_done_date", "production_delay_reason",
]

HEADER_FIELDS = ["customer_id", "employee_id", "order_date", "ship_date",
                 "actual_ship_date", "currency", "order_status", "delay_reason",
                 "plan_done_date", "actual_done_date", "production_delay_reason"]


def _to_payload(header: dict, rows: list[dict]) -> dict:
    production = None
    if header.get("plan_done_date") or header.get("actual_done_date") \
            or header.get("production_delay_reason"):
        production = {
            "plan_done_date": header.get("plan_done_date"),
            "actual_done_date": header.get("actual_done_date"),
            "delay_reason": header.get("production_delay_reason"),
        }
    return {
        "order_id": header.get("order_id"),
        "customer_id": header.get("customer_id"),
        "employee_id": header.get("employee_id"),
        "order_date": header.get("order_date"),
        "ship_date": header.get("ship_date"),
        "actual_ship_date": header.get("actual_ship_date"),
        "currency": header.get("currency"),
        "order_status": header.get("order_status"),
        "delay_reason": header.get("delay_reason"),
        "items": [{"product_id": r.get("product_id"),
                   "quantity": r.get("quantity"),
                   "unit_price": r.get("unit_price")} for r in rows],
        "production": production,
    }


def _parse_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames:
        raise FactWriteError([("文件", "CSV 是空的，没有表头")])
    missing = [c for c in ("order_id", "customer_id", "employee_id",
                           "order_date", "product_id", "quantity", "unit_price")
               if c not in reader.fieldnames]
    if missing:
        raise FactWriteError([("文件", f"CSV 缺少必需的列：{'、'.join(missing)}")])

    groups: dict[str, tuple[dict, list[dict]]] = {}
    errors: list[tuple[str, str]] = []
    for lineno, row in enumerate(reader, start=2):
        oid = (row.get("order_id") or "").strip()
        if not oid:
            errors.append((f"第 {lineno} 行",
                           "合同号为空——批量导入必须写合同号，不能自动生成"))
            continue
        if oid not in groups:
            groups[oid] = (row, [row])
            continue
        header, rows = groups[oid]
        for f in HEADER_FIELDS:
            a = (header.get(f) or "").strip()
            b = (row.get(f) or "").strip()
            if a != b:
                errors.append((f"第 {lineno} 行 · {f}",
                               f"同一合同号 {oid} 的「{f}」前后不一致（{a!r} vs {b!r}）"))
        rows.append(row)
    if errors:
        raise FactWriteError(errors)

    return [_to_payload(header, rows) for header, rows in groups.values()]


def _fail(e: FactWriteError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"message": "数据没通过校验，一条都没有写入",
                "errors": [{"field": f, "message": m} for f, m in e.errors]},
    )



@order_router.get("/api/orders/options")
async def options_handler(
    service: Annotated[FactWriteService, Depends(get_fact_write_service)],
    user: Annotated[User, Depends(require_writer)],
):

    dims = await service.repo.load_dimensions()
    employees = [r for r in dims["dim_employee"] if r["employee_role"] == "业务员"]
    return {
        "customers": dims["dim_customer"],
        "products": dims["dim_product"],
        "employees": employees,
        "currencies": ["USD", "CNY"],
        "csv_headers": CSV_HEADERS,
        "me": {"user_id": user.user_id, "display_name": user.display_name,
               "role": user.role, "employee_id": user.employee_id},
    }


@order_router.get("/api/orders/next-id")
async def next_id_handler(
    service: Annotated[FactWriteService, Depends(get_fact_write_service)],
    user: Annotated[User, Depends(require_writer)],
    employee_id: Annotated[str, Query(min_length=1)],
):

    return {"order_id": await service.repo.next_order_id(employee_id)}


@order_router.get("/api/orders/csv-template")
async def csv_template_handler(user: Annotated[User, Depends(require_writer)]):

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADERS)
    writer.writerow(["DEMO-0001", "C001", "E001", "2026-03-01", "2026-04-15", "", "USD",
                     "", "", "P001", "100", "12.5", "2026-03-26", "", ""])
    writer.writerow(["DEMO-0001", "C001", "E001", "2026-03-01", "2026-04-15", "", "USD",
                     "", "", "P002", "50", "8.8", "2026-03-26", "", ""])
    return {"filename": "order_import_template.csv", "content": buf.getvalue()}


@order_router.get("/api/orders")
async def list_orders_handler(
    service: Annotated[FactWriteService, Depends(get_fact_write_service)],
    user: Annotated[User, Depends(require_writer)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):

    return {"orders": await service.repo.recent_orders(limit)}


@order_router.get("/api/orders/{order_id}")
async def get_order_handler(
    order_id: str,
    service: Annotated[FactWriteService, Depends(get_fact_write_service)],
    user: Annotated[User, Depends(require_writer)],
):

    try:
        return await service.get_order(order_id)
    except FactWriteError as e:
        raise _fail(e)



@order_router.post("/api/orders", status_code=201)
async def create_order_handler(
    payload: OrderUpsertSchema,
    service: Annotated[FactWriteService, Depends(get_fact_write_service)],
    user: Annotated[User, Depends(require_writer)],
):

    try:
        return await service.create_order(payload.model_dump(), user)
    except FactWriteError as e:
        raise _fail(e)


@order_router.put("/api/orders/{order_id}")
async def update_order_handler(
    order_id: str,
    payload: OrderUpsertSchema,
    service: Annotated[FactWriteService, Depends(get_fact_write_service)],
    user: Annotated[User, Depends(require_writer)],
):

    try:
        return await service.update_order(order_id, payload.model_dump(), user)
    except FactWriteError as e:
        raise _fail(e)


@order_router.post("/api/orders/import")
async def import_orders_handler(
    service: Annotated[FactWriteService, Depends(get_fact_write_service)],
    user: Annotated[User, Depends(require_writer)],
    file: Annotated[UploadFile, File()],
    dry_run: Annotated[bool, Query()] = False,
):

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail={
            "message": "文件不是 UTF-8 编码",
            "errors": [{"field": "文件", "message": "请用 UTF-8 或 Excel 导出的 CSV 重存一次"}]})

    try:
        payloads = _parse_csv(text)
    except FactWriteError as e:
        raise _fail(e)

    if not payloads:
        raise HTTPException(status_code=422, detail={
            "message": "CSV 里没有数据行",
            "errors": [{"field": "文件", "message": "只有表头，没有内容"}]})

    if dry_run:
        return await service.preview_import(payloads)

    try:
        return await service.import_orders(payloads, user)
    except FactWriteError as e:
        raise _fail(e)
