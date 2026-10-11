
from dataclasses import dataclass, field
from datetime import date

EMPTY = ""


def num(s):
    if s is None or s == EMPTY:
        return None
    return s


def i(s):
    v = num(s)
    return None if v is None else int(float(v))


def prods_by_id(prods, pid):
    for r in prods:
        if r["production_id"] == pid:
            return r
    raise KeyError(pid)


def _date_of(v):
    v = int(v)
    return date(v // 10000, v // 100 % 100, v % 100)


@dataclass
class CheckResult:

    group: str
    name: str
    total: int
    samples: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.total == 0

    def format(self) -> str:
        if self.ok:
            return f"  ✓ {self.name}"
        lines = [f"  ✗ {self.name} —— {self.total} 例"]
        lines += [f"       {s}" for s in self.samples]
        if self.total > len(self.samples):
            lines.append(f"       …另有 {self.total - len(self.samples)} 例")
        return "\n".join(lines)


class InvariantChecker:

    def __init__(self, as_of: int):
        self.as_of = as_of
        self.results: list[CheckResult] = []
        self._group = ""

    def group(self, title: str):
        self._group = title

    def check(self, name, bad, show=lambda r: str(r)):
        bad = list(bad)
        self.results.append(
            CheckResult(self._group, name, len(bad), [show(r) for r in bad[:3]])
        )


    def run_all(self, d: dict) -> list[CheckResult]:

        cust = {r["customer_id"] for r in d["dim_customer"]}
        prod = {r["product_id"]: r for r in d["dim_product"]}
        emp = {r["employee_id"] for r in d["dim_employee"]}
        work = {r["workshop_id"] for r in d["dim_workshop"]}
        order = {r["order_id"]: r for r in d["fact_order"]}
        item = d["fact_order_item"]
        proc = d["fact_production_process"]
        prods = d["fact_production"]
        prod_ids = {r["production_id"] for r in prods}
        quot = d["fact_quotation"]
        samp = d["fact_sample"]

        self.group("主键唯一")
        for table, key in (("fact_order", "order_id"), ("fact_quotation", "quotation_id"),
                           ("fact_sample", "sample_id"), ("fact_order_item", "item_id"),
                           ("fact_production", "production_id"),
                           ("fact_production_process", "process_id"),
                           ("dim_customer", "customer_id"), ("dim_product", "product_id")):
            seen, dup = set(), []
            for r in d[table]:
                if r[key] in seen:
                    dup.append(r)
                seen.add(r[key])
            self.check(f"{table}.{key}", dup, lambda r, k=key: f"{r[k]} 重复")

        self.group("外键都能落到维度表")
        for table, col, pool in (("fact_quotation", "customer_id", cust),
                                 ("fact_sample", "customer_id", cust),
                                 ("fact_order", "customer_id", cust),
                                 ("fact_quotation", "employee_id", emp),
                                 ("fact_sample", "employee_id", emp),
                                 ("fact_order", "employee_id", emp),
                                 ("fact_order_item", "product_id", set(prod)),
                                 ("fact_production", "product_id", set(prod)),
                                 ("fact_production_process", "workshop_id", work),
                                 ("fact_production_process", "production_id", prod_ids),
                                 ("fact_order_item", "order_id", set(order)),
                                 ("fact_production", "order_id", set(order))):
            self.check(f"{table}.{col}",
                       [r for r in d[table] if r[col] is not None and r[col] not in pool],
                       lambda r, c=col: f'{r.get("order_id") or r.get("quotation_id")} → {r[c]}')

        self.group("接单漏斗（报价 ↔ 合同）")
        winners = [r for r in quot if r["quote_status"] == "中标"]
        self.check("中标 ⇔ order_id 非空",
                   [r for r in quot if (r["quote_status"] == "中标") != bool(r["order_id"])],
                   lambda r: f'{r["quotation_id"]} status={r["quote_status"]} order_id={r["order_id"]}')
        won_orders = [r["order_id"] for r in winners]
        self.check("每份合同都有且只有一条中标报价",
                   [o for o in order if won_orders.count(o) != 1],
                   lambda o: f"{o} 有 {won_orders.count(o)} 条中标报价")
        self.check("落标才有未成交原因",
                   [r for r in quot if (r["quote_status"] == "落标") != bool(r["lost_reason"])],
                   lambda r: f'{r["quotation_id"]} {r["quote_status"]} {r["lost_reason"]}')

        self.group("合同交付")
        self.check("合同日期 ≤ 承诺交期",
                   [r for r in order.values() if r["ship_date_id"] and
                    int(r["order_date_id"]) > int(r["ship_date_id"])],
                   lambda r: f'{r["order_id"]} 下单 {r["order_date_id"]} > 交期 {r["ship_date_id"]}')
        self.check("已发货 ⇔ 有实际发货日期",
                   [r for r in order.values() if (r["order_status"] == "已发货") != bool(r["actual_ship_date_id"])],
                   lambda r: f'{r["order_id"]} {r["order_status"]} actual={r["actual_ship_date_id"]}')

        def bad_delay(r):
            if not r["actual_ship_date_id"]:
                return bool(r["delay_days"])
            return int(r["delay_days"]) != (_date_of(r["actual_ship_date_id"])
                                            - _date_of(r["ship_date_id"])).days

        self.check("delay_days = 实际发货 - 承诺交期（未发货必须为空）",
                   [r for r in order.values() if bad_delay(r)],
                   lambda r: f'{r["order_id"]} delay={r["delay_days"]} '
                             f'{r["ship_date_id"]}→{r["actual_ship_date_id"]}')
        self.check("is_on_time 与 delay_days 自洽",
                   [r for r in order.values()
                    if (r["is_on_time"] or "") != ("" if r["delay_days"] is None
                                                   else ("准时" if int(r["delay_days"]) <= 0 else "延期"))],
                   lambda r: f'{r["order_id"]} delay={r["delay_days"]} is_on_time={r["is_on_time"]}')
        self.check("延期才有延期原因",
                   [r for r in order.values()
                    if (int(r["delay_days"] or 0) > 0) != bool(r["delay_reason"])],
                   lambda r: f'{r["order_id"]} delay={r["delay_days"]} reason={r["delay_reason"]}')

        self.group("实际日期不得越过今天")
        for table, idcol, col in (("fact_order", "order_id", "actual_ship_date_id"),
                                  ("fact_production", "production_id", "actual_done_date_id"),
                                  ("fact_sample", "sample_id", "done_date_id"),
                                  ("fact_production_process", "process_id", "process_done_date_id")):
            self.check(f"{table}.{col} ≤ 今天",
                       [r for r in d[table] if r[col] not in (None, EMPTY)
                        and int(r[col]) > self.as_of],
                       lambda r, idc=idcol, c=col: f'{r[idc]} {c}={r[c]}')

        self.group("生产与工序")
        self.check("生产指令只针对成品（配件是外购件，不进生产）",
                   [r for r in prods if prod[r["product_id"]]["product_type"] != "成品"],
                   lambda r: f'{r["production_id"]} {r["product_id"]} '
                             f'{prod[r["product_id"]]["product_type"]}')
        self.check("完成才有实际完工日期与延期天数",
                   [r for r in prods if bool(r["actual_done_date_id"]) != (r["delay_days"] is not None)],
                   lambda r: f'{r["production_id"]} done={r["actual_done_date_id"]} delay={r["delay_days"]}')
        self.check("生产完工不晚于发货",
                   [r for r in prods
                    if r["actual_done_date_id"] and order.get(r["order_id"], {}).get("actual_ship_date_id")
                    and int(r["actual_done_date_id"]) > int(order[r["order_id"]]["actual_ship_date_id"])],
                   lambda r: f'{r["production_id"]} 完工 {r["actual_done_date_id"]} > '
                             f'发货 {order[r["order_id"]]["actual_ship_date_id"]}')
        self.check("不良数 ≤ 该工序加工数",
                   [r for r in proc if int(r["defect_quantity"]) > int(r["process_output"])],
                   lambda r: f'{r["process_id"]} 不良{r["defect_quantity"]} > 加工{r["process_output"]}')
        self.check("有不良才有不良类型",
                   [r for r in proc if (int(r["defect_quantity"]) > 0) != bool(r["defect_type"])],
                   lambda r: f'{r["process_id"]} 不良{r["defect_quantity"]} type={r["defect_type"]}')

        route_err = []
        for r in proc:
            p = prod[prods_by_id(prods, r["production_id"])["product_id"]]
            expect = "W04" if p["material"] == "不锈钢" else "W05"
            if r["process_seq"] == 4 and r["workshop_id"] != expect:
                route_err.append((r, p, expect))
        self.check("第 4 道工序与材质一致（不锈钢→抛光 / 碳钢→喷塑）", route_err,
                   lambda t: f'{t[0]["process_id"]} {t[1]["material"]} 却走了 {t[0]["workshop_id"]}，应为 {t[2]}')
        self.check("每道工序顺序 1..5 且同一生产指令内不重复",
                   [r for r in proc if not 1 <= int(r["process_seq"]) <= 5],
                   lambda r: f'{r["process_id"]} seq={r["process_seq"]}')

        self.group("明细与样品")
        self.check("数量 > 0", [r for r in item if int(r["quantity"]) <= 0],
                   lambda r: f'{r["item_id"]} qty={r["quantity"]}')
        self.check("金额 > 0", [r for r in item if float(r["amount"]) <= 0],
                   lambda r: f'{r["item_id"]} amount={r["amount"]}')
        self.check("只重 × 数量 = 总重（只重为 0 的件跳过）",
                   [r for r in item
                    if float(prod[r["product_id"]]["unit_weight"]) > 0
                    and abs(float(r["total_weight"])
                            - float(prod[r["product_id"]]["unit_weight"]) * int(r["quantity"])) > 0.01],
                   lambda r: f'{r["item_id"]} {r["total_weight"]} ≠ '
                             f'{prod[r["product_id"]]["unit_weight"]}×{r["quantity"]}')
        self.check("样品：已寄出才有寄出日期和延期天数",
                   [r for r in samp if bool(r["done_date_id"]) != (r["delay_days"] is not None)],
                   lambda r: f'{r["sample_id"]} {r["sample_status"]} done={r["done_date_id"]} '
                             f'delay={r["delay_days"]}')
        self.check("样品：转成订单的必须是已确认",
                   [r for r in samp if r["order_id"] and r["sample_status"] != "已确认"],
                   lambda r: f'{r["sample_id"]} → {r["order_id"]} 但状态是 {r["sample_status"]}')

        self.group("空的日期外键只能出现在「还没发生」的位置")
        self.check("合同订单日期不能为空", [r for r in order.values() if not r["order_date_id"]],
                   lambda r: r["order_id"])
        self.check("报价/样品日期不能为空",
                   [r for r in quot if not r["quote_date_id"]] + [r for r in samp if not r["sample_date_id"]],
                   lambda r: r.get("quotation_id") or r.get("sample_id"))

        return self.results

    def check_dates_in_dim(self, d: dict, valid_date_ids: set) -> list[CheckResult]:
        if not valid_date_ids:
            self.check("dim_date 非空（空表说明数据卷要按 docker/mysql/dw.sql 重建）",
                       [{"_": "dim_date 是空的"}], lambda r: "dim_date 是空的")
            return self.results

        self.group(f"日期外键都在 dim_date 范围内（共 {len(valid_date_ids)} 天）")
        for table, cols in (("fact_quotation", ["quote_date_id"]),
                            ("fact_sample", ["sample_date_id", "due_date_id", "done_date_id"]),
                            ("fact_order", ["order_date_id", "ship_date_id", "actual_ship_date_id"]),
                            ("fact_production", ["plan_done_date_id", "actual_done_date_id"]),
                            ("fact_production_process", ["process_done_date_id"])):
            bad = [(r, c) for r in d[table] for c in cols
                   if r[c] not in (None, EMPTY) and int(r[c]) not in valid_date_ids]
            self.check(f"{table}.<日期列>", bad,
                       lambda t: f'{t[0].get("order_id") or t[0].get("production_id")} '
                                 f'{t[1]}={t[0][t[1]]}')
        return self.results


def format_results(results: list[CheckResult]) -> str:
    lines, group = [], None
    for r in results:
        if r.group != group:
            lines.append(("\n" if lines else "") + f"{r.group}：")
            group = r.group
        lines.append(r.format())
    return "\n".join(lines)


def failures(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if not r.ok]
