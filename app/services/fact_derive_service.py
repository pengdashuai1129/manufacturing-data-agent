
import random
from datetime import date, timedelta


FX_USD_CNY = 7.2

LARGE_CONTRACT_CNY = 700_000
LARGE_CONTRACT = "大额合同"
NORMAL_CONTRACT = "一般合同"

ON_TIME = "准时"
DELAYED = "延期"

STATUS_SHIPPED = "已发货"
STATUS_DONE = "已完工"
STATUS_IN_PRODUCTION = "生产中"

WORKSHOPS = [
    ("W01", "机加工"),
    ("W02", "制管"),
    ("W03", "焊接"),
    ("W04", "抛光"),
    ("W05", "喷塑"),
    ("W06", "包装"),
]

DEFECTS = {
    "W01": ["划伤", "毛刺"],
    "W02": ["毛刺", "划伤"],
    "W03": ["缝隙过大", "螺丝凸出"],
    "W04": ["划伤", "电镀发黄"],
    "W05": ["电镀发黄", "麻点", "凹坑"],
    "W06": ["生锈"],
}

DEFECT_BASE = {"W01": 0.008, "W02": 0.012, "W03": 0.015,
               "W04": 0.010, "W05": 0.055, "W06": 0.004}

PROCESS_SEQ = {"W01": 1, "W02": 2, "W03": 3, "W04": 4, "W05": 4, "W06": 5}

DELAY_REASONS = ["车间排产满", "工艺问题", "原材料到货延迟", "客户改单", "客户确认慢", "返工"]



def d2id(d: date | None):
    return int(d.strftime("%Y%m%d")) if d else ""


def id2date(v) -> date | None:
    if v in (None, ""):
        return None
    v = int(v)
    return date(v // 10000, v // 100 % 100, v % 100)


def norm_null(v):
    return "" if v is None else v


def route_of(product: dict):
    fourth = ("W04", "抛光") if product["material"] == "不锈钢" else ("W05", "喷塑")
    return [("W01", "机加工"), ("W02", "制管"), ("W03", "焊接"), fourth, ("W06", "包装")]



def derive_item_amount(unit_price: float, quantity: int) -> float:
    return round(float(unit_price) * int(quantity), 2)


def derive_total_weight(unit_weight: float, quantity: int) -> float:
    w = float(unit_weight or 0) * int(quantity)
    return round(w, 2) if w else 0


def derive_contract_type(amount_cny: float) -> str:
    return LARGE_CONTRACT if amount_cny >= LARGE_CONTRACT_CNY else NORMAL_CONTRACT


def amount_in_cny(amount: float, currency: str) -> float:
    return amount * FX_USD_CNY if currency == "USD" else amount


def derive_delay(base_date_id, actual_date_id) -> tuple:
    if actual_date_id in (None, "") or base_date_id in (None, ""):
        return None, None
    delay = (id2date(actual_date_id) - id2date(base_date_id)).days
    return delay, (ON_TIME if delay <= 0 else DELAYED)


def derive_order_status(actual_ship_date_id, has_production_done: bool,
                        requested=None) -> str:
    if actual_ship_date_id not in (None, ""):
        return STATUS_SHIPPED
    if requested in (STATUS_IN_PRODUCTION, STATUS_DONE):
        return requested
    return STATUS_DONE if has_production_done else STATUS_IN_PRODUCTION



def spread_process_dates(plan_done: date, n_processes: int, as_of: date,
                         rng: random.Random, delay_days) -> list:
    dates = [plan_done]
    for _ in range(n_processes - 1):
        dates.append(dates[-1] - timedelta(days=rng.randint(2, 5)))
    dates.reverse()
    if delay_days and delay_days > 0:
        dates = [d + timedelta(days=delay_days * (i + 1) // n_processes)
                 for i, d in enumerate(dates)]
    return [None if d > as_of else d for d in dates]


def build_process_rows(production_id: str, product: dict, quantity: int,
                       plan_done: date, as_of: date, rng: random.Random,
                       delay_days=None) -> list[dict]:
    route = route_of(product)
    dates = spread_process_dates(plan_done, len(route), as_of, rng, delay_days)
    rows = []
    for k, ((wid, _), pdate) in enumerate(zip(route, dates)):
        if pdate is None:
            continue
        step_out = int(quantity * rng.uniform(0.96, 1.02))
        defective = min(int(round(step_out * DEFECT_BASE[wid] * rng.uniform(0.4, 1.8))),
                        step_out // 3)
        rows.append({
            "process_id": f"{production_id}-P{k + 1}",
            "production_id": production_id,
            "workshop_id": wid,
            "process_seq": PROCESS_SEQ[wid],
            "process_plan_quantity": quantity,
            "process_output": step_out,
            "defect_quantity": defective,
            "defect_type": rng.choice(DEFECTS[wid]) if defective > 0 else "",
            "process_done_date_id": d2id(pdate),
        })
    return rows
