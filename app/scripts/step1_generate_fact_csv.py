
import csv
import io
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

RNG = random.Random(20261009)

from app.services.fact_derive_service import (
    DEFECT_BASE,
    DEFECTS,
    DELAY_REASONS,
    PROCESS_SEQ,
    WORKSHOPS,
    d2id,
    route_of,
)

DATA_DIR = Path(__file__).parents[2] / "data"
SEED_FILE = DATA_DIR / "real_seed.json"

PERIOD_START = date(2025, 1, 1)
TODAY = date(2026, 10, 9)

TARGET_REVENUE_YEAR_USD = 20_000_000
SCALE = 69.0
_ORDER_COUNT_SCALE = 7.0
_QTY_SCALE = SCALE / _ORDER_COUNT_SCALE

_BASE_ORDERS = 65
_BASE_QUOTES = 150
_BASE_SAMPLES = 60

REAL_ORDER_COUNT = 11

TARGET_ORDERS = REAL_ORDER_COUNT + round((_BASE_ORDERS - REAL_ORDER_COUNT) * _ORDER_COUNT_SCALE)
TARGET_QUOTES = round(_BASE_QUOTES * _ORDER_COUNT_SCALE)
TARGET_SAMPLES = round(_BASE_SAMPLES * _ORDER_COUNT_SCALE)


LOST_REASONS = ["报价偏高", "交期太长", "客户转单", "产品不符", "客户预算取消"]
SAMPLE_DELAY_REASONS = ["工艺问题", "车间排产满", "客户确认慢"]

SYNTH_CUSTOMERS = [
    ("荷兰范德堡", "荷兰", "欧洲", "出口"),
    ("德国施瓦茨暖通", "德国", "欧洲", "出口"),
    ("英国格瑞特卫浴", "英国", "欧洲", "出口"),
    ("意大利蒙蒂", "意大利", "欧洲", "出口"),
    ("西班牙卡萨斯", "西班牙", "欧洲", "出口"),
    ("比利时德科", "比利时", "欧洲", "出口"),
    ("挪威诺德海姆", "挪威", "欧洲", "出口"),
    ("丹麦赫曼森", "丹麦", "欧洲", "出口"),
    ("加拿大北纬", "加拿大", "北美洲", "出口"),
    ("墨西哥索尔", "墨西哥", "北美洲", "出口"),
    ("新西兰南十字", "新西兰", "大洋洲", "出口"),
    ("韩国大阳", "韩国", "亚洲", "出口"),
    ("杭州暖通设备", "中国", "亚洲", "内销"),
    ("成都卫浴经销", "中国", "亚洲", "内销"),
]

SYNTH_EMPLOYEES = [
    ("周建国", "技术部", "技术员"),
    ("孙立新", "生产部", "生产质检"),
]



def rand_date(rng, a, b):
    return a + timedelta(days=rng.randint(0, max((b - a).days, 0)))


def on_time(delay):
    if delay is None:
        return ""
    return "准时" if delay <= 0 else "延期"


def actual_within_today(base, delay):
    d = base + timedelta(days=delay)
    if d > TODAY:
        return TODAY, (TODAY - base).days
    return d, delay


def scaled_qty(rng, choices):
    return max(1, int(round(rng.choice(choices) * _QTY_SCALE, -1)))


def spread_months(n, start, end, growth=0.12):
    months, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    weights = [max(1.0 + i * growth, 0.3) for i in range(len(months))]
    counts = [1] * len(months)
    rest = n - len(months)
    if rest > 0:
        tw = sum(weights)
        for i in range(len(months)):
            counts[i] += int(rest * weights[i] / tw)
    out = []
    for (yy, mm), c in zip(months, counts):
        out += [(yy, mm)] * c
    i = len(months) - 1
    while len(out) < n:
        out.append(months[i % len(months)])
        i -= 1
    return out[:n]


def day_in(rng, y, m):
    return rand_date(rng, max(date(y, m, 1), PERIOD_START), min(date(y, m, 28), TODAY))


def write_csv(name, columns, rows):
    path = DATA_DIR / name
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        w.writerows([{k: r.get(k, "") for k in columns} for r in rows])
    print(f"  {name:<34}{len(rows):>5} 行")
    return rows



def build_employees(seed):
    rows = [dict(e) for e in seed["employees"]]
    for name, dept, role in SYNTH_EMPLOYEES:
        rows.append({"employee_id": f"E{len(rows) + 1:03d}", "employee_name": name,
                     "department": dept, "employee_role": role})
    return rows


def initials_of(seed_orders):
    m = {}
    for o in seed_orders:
        head = o["order_id"].split("-")[0]
        if o["employee_name"]:
            m[o["employee_name"]] = head
    return m


def build_customers(seed):
    rows = [{"customer_id": c["customer_id"], "customer_name": c["customer_name"],
             "country": c["country"], "continent": c["continent"],
             "trade_type": c["trade_type"]} for c in seed["customers"]]
    for name, country, continent, trade in SYNTH_CUSTOMERS:
        rows.append({"customer_id": f"C{len(rows) + 1:03d}", "customer_name": name,
                     "country": country, "continent": continent, "trade_type": trade})
    for r in rows:
        r["customer_level"] = ""
        r["customer_status"] = ""
    return rows


def build_products(seed):
    return [dict(p) for p in seed["products"]]


def unit_price(p):
    r = random.Random(p["product_id"])
    w = float(p["unit_weight"] or 0)
    if w > 0:
        rate = 5.8 if p["material"] == "不锈钢" else 4.3
        return round(w * rate + r.uniform(8, 22), 2)
    if p["heating_type"] == "电热":
        return round(r.uniform(18, 70), 2)
    if p["product_type"] == "配件":
        return round(r.uniform(1.5, 12), 2)
    return round(r.uniform(60, 180), 2)


def price_in(p, currency):
    usd = unit_price(p)
    return round(usd * 7.2, 2) if currency == "CNY" else usd



def pick_detail_products(rng, products, currency, want_finished=True):
    finished = [p for p in products if p["product_type"] == "成品"]
    parts = [p for p in products if p["product_type"] == "配件"]
    rows = []
    if want_finished and rng.random() < 0.62:
        n = 2 if rng.random() < 0.15 else 1
        for p in rng.sample(finished, min(n, len(finished))):
            rows.append((p, scaled_qty(rng, [20, 30, 50, 60, 100, 150, 200, 300, 500])))
    if parts and rng.random() < 0.55:
        p = rng.choice(parts)
        rows.append((p, scaled_qty(rng, [1, 5, 10, 15, 30, 50, 100, 120])))
    if not rows:
        p = rng.choice(parts or finished)
        rows.append((p, scaled_qty(rng, [10, 30, 50, 100])))
    return rows


def build_orders(seed, customers, employees, products, rng):
    by_name = {c["customer_name"]: c for c in customers}
    sales = [e for e in employees if e["employee_role"] == "业务员"]
    prod_by_id = {p["product_id"]: p for p in products}
    initials = initials_of(seed["orders"])

    orders, items = [], []
    seq = 0

    def add_item(order_id, p, qty):
        i = len([x for x in items if x["order_id"] == order_id]) + 1
        w = float(p["unit_weight"] or 0) * qty
        orders_currency = next(o["currency"] for o in orders if o["order_id"] == order_id)
        items.append({
            "item_id": f"{order_id}-{i:02d}", "order_id": order_id,
            "product_id": p["product_id"], "quantity": qty,
            "total_weight": round(w, 2) if w else 0,
            "amount": round(price_in(p, orders_currency) * qty, 2),
        })

    for o in seed["orders"]:
        c = by_name[o["customer_name"]]
        orders.append({
            "order_id": o["order_id"], "customer_id": c["customer_id"],
            "employee_id": next(e["employee_id"] for e in sales if e["employee_name"] == o["employee_name"]),
            "order_date_id": d2id(date.fromisoformat(o["order_date"])),
            "ship_date_id": d2id(date.fromisoformat(o["ship_date"])) if o["ship_date"] else "",
            "actual_ship_date_id": "", "currency": o["currency"],
            "contract_type": "一般合同", "order_status": "",
            "delay_days": "", "is_on_time": "", "delay_reason": "",
            "_real": True, "_has_detail": o["has_erp_detail"],
            "_order_date": date.fromisoformat(o["order_date"]),
            "_ship_date": date.fromisoformat(o["ship_date"]) if o["ship_date"] else None,
        })
        rows = [(prod_by_id[i["product_id"]], i["quantity"]) for i in seed["order_items"]
                if i["order_id"] == o["order_id"]]
        if not rows:
            rows = pick_detail_products(rng, products, o["currency"])
        for p, qty in rows:
            add_item(o["order_id"], p, qty)

    n_synth = TARGET_ORDERS - len(orders)
    months = spread_months(n_synth, PERIOD_START, TODAY, growth=0.0)

    recent = next(i for i, (y, m) in enumerate(months) if (y, m) >= (2026, 1))
    onboard_rng = random.Random("onboard")
    join_at = {}
    for c in customers:
        pick = onboard_rng.random()
        if pick < 0.6:
            join_at[c["customer_id"]] = 0
        elif pick < 0.8:
            join_at[c["customer_id"]] = onboard_rng.randrange(1, recent)
        else:
            join_at[c["customer_id"]] = onboard_rng.randrange(recent, len(months))

    for idx, (y, m) in enumerate(months):
        od = day_in(rng, y, m)
        pool = [c for c in customers if join_at[c["customer_id"]] <= idx] or customers
        c = rng.choice(pool)
        e = rng.choice(sales)
        seq += 1
        order_id = f"{initials[e['employee_name']]}-{1200000 + seq}"
        ship = od + timedelta(days=rng.choice([25, 30, 35, 40, 45, 55, 60]))
        orders.append({
            "order_id": order_id, "customer_id": c["customer_id"],
            "employee_id": e["employee_id"], "order_date_id": d2id(od),
            "ship_date_id": d2id(ship), "actual_ship_date_id": "",
            "currency": "CNY" if c["trade_type"] == "内销" else "USD",
            "contract_type": "一般合同", "order_status": "",
            "delay_days": "", "is_on_time": "", "delay_reason": "",
            "_real": False, "_has_detail": False,
            "_order_date": od, "_ship_date": ship,
        })
        for p, qty in pick_detail_products(rng, products, orders[-1]["currency"]):
            add_item(order_id, p, qty)

    for o in orders:
        amount_cny = sum(i["amount"] for i in items if i["order_id"] == o["order_id"])
        if o["currency"] == "USD":
            amount_cny *= 7.2
        o["contract_type"] = "大额合同" if amount_cny >= 700000 else "一般合同"

        o["_lead"] = rng.choice([12, 15, 18, 20, 22, 25])

        if o["_ship_date"] is None:
            o["order_status"] = "生产中"
            continue
        if o["_ship_date"] - timedelta(days=o["_lead"]) > TODAY:
            o["order_status"] = "生产中"
            continue
        if not any(i["order_id"] == o["order_id"] for i in items):
            o["order_status"] = "生产中"
            continue
        if o["_ship_date"] > TODAY:
            o["order_status"] = "已完工" if rng.random() < 0.25 else "生产中"
            continue
        delay = rng.choices([-5, -3, -1, 0, 1, 3, 7, 12, 18],
                            weights=[10, 12, 14, 20, 12, 10, 8, 5, 2])[0]
        actual, delay = actual_within_today(o["_ship_date"], delay)
        o["actual_ship_date_id"] = d2id(actual)
        o["_actual_ship"] = actual
        o["order_status"] = "已发货"
        o["delay_days"] = delay
        o["is_on_time"] = on_time(delay)
        o["delay_reason"] = rng.choice(DELAY_REASONS) if delay > 0 else ""

    return orders, items



def build_quotations(orders, items, products, rng):
    prod_by_id = {p["product_id"]: p for p in products}
    rows = []

    for o in orders:
        first = next(i for i in items if i["order_id"] == o["order_id"])
        p = prod_by_id[first["product_id"]]
        qd = o["_order_date"] - timedelta(days=rng.randint(5, 45))
        qd = max(qd, PERIOD_START)
        rows.append({
            "quotation_id": f"BJ-{o['order_id'].split('-')[0]}-{len(rows) + 1:04d}",
            "customer_id": o["customer_id"], "product_id": p["product_id"],
            "employee_id": o["employee_id"], "quote_date_id": d2id(qd),
            "currency": o["currency"], "quote_quantity": first["quantity"],
            "quote_amount": round(first["amount"] * rng.uniform(1.02, 1.15), 2),
            "quote_status": "中标", "lost_reason": "", "order_id": o["order_id"],
        })

    finished = [p for p in products if p["product_type"] == "成品"]
    for y, m in spread_months(TARGET_QUOTES - len(rows), PERIOD_START, TODAY, growth=-0.035):
        o = rng.choice(orders)
        p = rng.choice(finished)
        qd = day_in(rng, y, m)
        qty = scaled_qty(rng, [50, 100, 200, 300, 500])
        q1 = qd < date(2025, 4, 1)
        status = rng.choices(["落标", "跟进中"], weights=[70, 30])[0]
        rows.append({
            "quotation_id": f"BJ-{o['order_id'].split('-')[0]}-{len(rows) + 1:04d}",
            "customer_id": o["customer_id"], "product_id": p["product_id"],
            "employee_id": o["employee_id"], "quote_date_id": d2id(qd),
            "currency": o["currency"], "quote_quantity": qty,
            "quote_amount": round(price_in(p, o["currency"]) * qty * rng.uniform(1.0, 1.2), 2),
            "quote_status": status,
            "lost_reason": (rng.choices(["报价偏高", "交期太长", "客户转单", "产品不符", "客户预算取消"],
                                        weights=[60, 10, 10, 10, 10] if q1 else [20, 20, 20, 20, 20])[0]
                            if status == "落标" else ""),
            "order_id": "",
        })
    return rows



def build_samples(orders, items, products, rng):
    prod_by_id = {p["product_id"]: p for p in products}
    finished = [p for p in products if p["product_type"] == "成品"]
    rows = []
    picked = rng.sample(orders, int(len(orders) * 0.6))
    for o in picked + [None] * (TARGET_SAMPLES - len(picked)):
        if o is not None:
            p = prod_by_id[next(i for i in items if i["order_id"] == o["order_id"])["product_id"]]
            sd = o["_order_date"] - timedelta(days=rng.randint(20, 90))
        else:
            p = rng.choice(finished)
            base = rng.choice(orders)
            sd = base["_order_date"] - timedelta(days=rng.randint(10, 60))
        sd = max(sd, PERIOD_START)
        due = sd + timedelta(days=rng.choice([15, 20, 25, 30, 40]))
        convert = o is not None and rng.random() < 0.8

        if convert:
            status, delay = "已确认", rng.choices([-3, -1, 0, 2, 5, 9], weights=[8, 12, 25, 25, 18, 12])[0]
        else:
            status = rng.choices(["制作中", "已寄出", "已放弃"], weights=[20, 35, 45])[0]
            delay = None if status == "制作中" else rng.choices([-2, 0, 3, 8, 15], weights=[10, 25, 25, 25, 15])[0]

        done = None
        if delay is not None:
            done, delay = actual_within_today(due, delay)
        rows.append({
            "sample_id": f"YP-{p['product_id'][:2]}-{len(rows) + 1:04d}",
            "customer_id": o["customer_id"] if o else base["customer_id"],
            "product_id": p["product_id"],
            "employee_id": o["employee_id"] if o else base["employee_id"],
            "sample_date_id": d2id(sd), "due_date_id": d2id(due),
            "done_date_id": d2id(done) if done else "",
            "sample_quantity": rng.choice([1, 2, 3, 5]),
            "sample_status": status,
            "delay_days": delay if delay is not None else "",
            "is_on_time": on_time(delay),
            "delay_reason": rng.choice(SAMPLE_DELAY_REASONS) if (delay or 0) > 0 else "",
            "order_id": o["order_id"] if convert else "",
        })
    return rows



def build_production(orders, items, products, rng):
    prod_by_id = {p["product_id"]: p for p in products}
    order_by_id = {o["order_id"]: o for o in orders}
    prods, procs = [], []

    for it in items:
        p = prod_by_id[it["product_id"]]
        if p["product_type"] != "成品":
            continue
        o = order_by_id[it["order_id"]]
        seq = 1 + len([x for x in prods if x["order_id"] == o["order_id"]])
        pid = f"{o['order_id']}-{seq:02d}"
        qty = it["quantity"]
        plan_done = (o["_ship_date"] or (o["_order_date"] + timedelta(days=45))) \
            - timedelta(days=o["_lead"])

        if o["order_status"] == "已发货":
            delay = rng.choices([-6, -3, -1, 0, 2, 5, 10, 16],
                                weights=[10, 12, 14, 22, 12, 12, 12, 6])[0]
            done, delay = actual_within_today(plan_done, delay)
            ship = o.get("_actual_ship")
            if ship and done > ship:
                done, delay = ship, (ship - plan_done).days
        elif o["order_status"] == "已完工":
            delay = rng.choices([-4, -2, 0, 1, 4, 9], weights=[10, 15, 25, 25, 17, 8])[0]
            done, delay = actual_within_today(plan_done, delay)
        else:
            delay, done = None, None

        prods.append({
            "production_id": pid, "order_id": o["order_id"], "product_id": p["product_id"],
            "plan_quantity": qty,
            "output_quantity": int(qty * rng.uniform(0.96, 1.02)) if done else 0,
            "plan_done_date_id": d2id(plan_done),
            "actual_done_date_id": d2id(done) if done else "",
            "delay_days": delay if delay is not None else "",
            "is_on_time": on_time(delay),
            "delay_reason": rng.choice(DELAY_REASONS) if (delay or 0) > 0 else "",
        })

        n = len(route_of(p))
        pdates = [plan_done]
        for _ in range(n - 1):
            pdates.append(pdates[-1] - timedelta(days=rng.randint(2, 5)))
        pdates.reverse()
        if delay and delay > 0:
            pdates = [d + timedelta(days=delay * (i + 1) // n) for i, d in enumerate(pdates)]

        for k, ((wid, _), pdate) in enumerate(zip(route_of(p), pdates)):
            if pdate > TODAY:
                continue
            step_out = int(qty * rng.uniform(0.96, 1.02))
            rate = DEFECT_BASE[wid] * (1.0 + 0.08 * ((pdate.year - 2025) * 12 + pdate.month - 1) / 12)
            defective = min(int(round(step_out * rate * rng.uniform(0.4, 1.8))), step_out // 3)
            procs.append({
                "process_id": f"{pid}-P{k + 1}",
                "production_id": pid, "workshop_id": wid, "process_seq": PROCESS_SEQ[wid],
                "process_plan_quantity": qty, "process_output": step_out,
                "defect_quantity": defective,
                "defect_type": rng.choice(DEFECTS[wid]) if defective > 0 else "",
                "process_done_date_id": d2id(pdate),
            })
    return prods, procs



def fill_customer_level(customers, orders, items):
    amount = {}
    for o in orders:
        s = sum(i["amount"] for i in items if i["order_id"] == o["order_id"])
        if o["currency"] == "USD":
            s *= 7.2
        amount[o["customer_id"]] = amount.get(o["customer_id"], 0) + s
    ranked = sorted(amount.items(), key=lambda kv: -kv[1])
    for c in customers:
        c["customer_level"] = "C"
    for i, (cid, _) in enumerate(ranked):
        level = "A" if i < len(ranked) * 0.25 else ("B" if i < len(ranked) * 0.6 else "C")
        next(c for c in customers if c["customer_id"] == cid)["customer_level"] = level
    for c in customers:
        first = min((o["_order_date"] for o in orders if o["customer_id"] == c["customer_id"]),
                    default=None)
        c["customer_status"] = "老客户" if (first and first < date(2026, 1, 1)) else "新客户"



def main():
    if not SEED_FILE.exists():
        print(f"缺少真实种子文件：{SEED_FILE}")
        print("先跑 `python tmp_parse_contracts.py --emit-seed` 从合同 PDF 生成它。")
        return

    seed = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    print(f"真实种子：客户 {len(seed['customers'])} / 产品 {len(seed['products'])} / "
          f"合同 {len(seed['orders'])} / 明细 {len(seed['order_items'])}")
    if seed.get("需确认国家"):
        print(f"  ★ 种子里还有国家待确认：{seed['需确认国家']}（会写进 dim_customer 的空值）")

    employees = build_employees(seed)
    customers = build_customers(seed)
    products = build_products(seed)

    orders, items = build_orders(seed, customers, employees, products, RNG)
    quotations = build_quotations(orders, items, products, RNG)
    samples = build_samples(orders, items, products, RNG)
    prods, procs = build_production(orders, items, products, RNG)
    fill_customer_level(customers, orders, items)

    print("\n写出 CSV：")
    write_csv("dim_customer.csv", ["customer_id", "customer_name", "country", "continent",
                                   "trade_type", "customer_level", "customer_status"], customers)
    write_csv("dim_product.csv", ["product_id", "product_name", "product_type", "heating_type",
                                  "material", "spec", "unit_weight"], products)
    write_csv("dim_employee.csv", ["employee_id", "employee_name", "department", "employee_role"],
              employees)
    write_csv("dim_workshop.csv", ["workshop_id", "workshop_name"],
              [{"workshop_id": w, "workshop_name": n} for w, n in WORKSHOPS])
    write_csv("fact_quotation.csv",
              ["quotation_id", "customer_id", "product_id", "employee_id", "quote_date_id",
               "currency", "quote_quantity", "quote_amount", "quote_status", "lost_reason",
               "order_id"], quotations)
    write_csv("fact_sample.csv",
              ["sample_id", "customer_id", "product_id", "employee_id", "sample_date_id",
               "due_date_id", "done_date_id", "sample_quantity", "sample_status", "delay_days",
               "is_on_time", "delay_reason", "order_id"], samples)
    write_csv("fact_order.csv",
              ["order_id", "customer_id", "employee_id", "order_date_id", "ship_date_id",
               "actual_ship_date_id", "currency", "contract_type", "order_status", "delay_days",
               "is_on_time", "delay_reason"],
              [{k: v for k, v in o.items() if not k.startswith("_")} for o in orders])
    write_csv("fact_order_item.csv",
              ["item_id", "order_id", "product_id", "quantity", "total_weight", "amount"], items)
    write_csv("fact_production.csv",
              ["production_id", "order_id", "product_id", "plan_quantity", "output_quantity",
               "plan_done_date_id", "actual_done_date_id", "delay_days", "is_on_time",
               "delay_reason"], prods)
    write_csv("fact_production_process.csv",
              ["process_id", "production_id", "workshop_id", "process_seq",
               "process_plan_quantity", "process_output", "defect_quantity", "defect_type",
               "process_done_date_id"], procs)

    real_orders = sum(1 for o in orders if o["_real"])
    print(f"\n规模对照（目标 / 实际）：")
    print(f"  合同      {TARGET_ORDERS} / {len(orders)}   （其中真实 {real_orders} 份 —— 真实单照录，不放大）")
    print(f"  报价单    {TARGET_QUOTES} / {len(quotations)}")
    print(f"  样品单    {TARGET_SAMPLES} / {len(samples)}")
    print(f"  生产指令  {len(prods)}   （只有成品进生产；配件是外购件，不占本厂产能）")
    print(f"  工序明细  {len(procs)}   （没排到的工序不生成行，所以不是生产指令 ×5）")
    print(f"  CSV 目录 → {DATA_DIR}")

    cur = {o["order_id"]: o["currency"] for o in orders}
    yr = {o["order_id"]: o["_order_date"].year for o in orders}

    def revenue(year=None):
        return sum((i["amount"] if cur[i["order_id"]] == "USD" else i["amount"] / 7.2)
                   for i in items if year is None or yr[i["order_id"]] == year)

    y25, y26 = revenue(2025), revenue(2026)
    span25 = (date(2026, 1, 1) - PERIOD_START).days / 365.25
    span26 = (TODAY - date(2026, 1, 1)).days / 365.25
    print(f"\n实测成交额（CNY 按 7.2 折算；真实合同不参与放大）：")
    print(f"  2025 全年       ${y25:>12,.0f}   年化 ${y25 / span25:>12,.0f}")
    print(f"  2026 年 1-9 月  ${y26:>12,.0f}   年化 ${y26 / span26:>12,.0f}   ← 目标 ${TARGET_REVENUE_YEAR_USD:,}")


if __name__ == "__main__":
    main()
