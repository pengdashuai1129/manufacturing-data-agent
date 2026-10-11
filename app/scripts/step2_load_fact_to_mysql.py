import csv
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pymysql

from app.services.data_quality_service import (
    InvariantChecker,
    failures,
    format_results,
    i,
    num,
)

DATA_DIR = Path(__file__).parents[2] / "data"

TODAY = 20261009

DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "hohohoho",
    "password": "p123456",
    "database": "dw",
    "charset": "utf8mb4",
}

TABLES = [
    ("dim_customer.csv", "dim_customer",
     ["customer_id", "customer_name", "country", "continent", "trade_type",
      "customer_level", "customer_status"]),
    ("dim_product.csv", "dim_product",
     ["product_id", "product_name", "product_type", "heating_type", "material",
      "spec", "unit_weight"]),
    ("dim_employee.csv", "dim_employee",
     ["employee_id", "employee_name", "department", "employee_role"]),
    ("dim_workshop.csv", "dim_workshop", ["workshop_id", "workshop_name"]),
    ("fact_quotation.csv", "fact_quotation",
     ["quotation_id", "customer_id", "product_id", "employee_id", "quote_date_id",
      "currency", "quote_quantity", "quote_amount", "quote_status", "lost_reason", "order_id"]),
    ("fact_sample.csv", "fact_sample",
     ["sample_id", "customer_id", "product_id", "employee_id", "sample_date_id",
      "due_date_id", "done_date_id", "sample_quantity", "sample_status", "delay_days",
      "is_on_time", "delay_reason", "order_id"]),
    ("fact_order.csv", "fact_order",
     ["order_id", "customer_id", "employee_id", "order_date_id", "ship_date_id",
      "actual_ship_date_id", "currency", "contract_type", "order_status", "delay_days",
      "is_on_time", "delay_reason"]),
    ("fact_order_item.csv", "fact_order_item",
     ["item_id", "order_id", "product_id", "quantity", "total_weight", "amount"]),
    ("fact_production.csv", "fact_production",
     ["production_id", "order_id", "product_id", "plan_quantity", "output_quantity",
      "plan_done_date_id", "actual_done_date_id", "delay_days", "is_on_time", "delay_reason"]),
    ("fact_production_process.csv", "fact_production_process",
     ["process_id", "production_id", "workshop_id", "process_seq",
      "process_plan_quantity", "process_output", "defect_quantity", "defect_type",
      "process_done_date_id"]),
]

INT_COLS = {
    "quote_date_id", "sample_date_id", "due_date_id", "done_date_id", "order_date_id",
    "ship_date_id", "actual_ship_date_id", "plan_done_date_id", "actual_done_date_id",
    "process_done_date_id", "process_seq", "process_plan_quantity", "process_output",
    "defect_quantity", "plan_quantity", "output_quantity", "quantity", "quote_quantity",
    "sample_quantity", "delay_days",
}
FLOAT_COLS = {"unit_weight", "total_weight", "amount", "quote_amount"}



def load_all():
    data = {}
    for fname, table, cols in TABLES:
        path = DATA_DIR / fname
        if not path.exists():
            print(f"缺少 {path} —— 先跑 `uv run python -m app.scripts.step1_generate_fact_csv`")
            sys.exit(1)
        with open(path, encoding="utf-8-sig") as fh:
            data[table] = [{c: num(r.get(c)) for c in cols} for r in csv.DictReader(fh)]
    return data




def load_to_mysql(cursor, d):
    for _, table, _ in TABLES:
        cursor.execute(f"DELETE FROM {table}")
    print("  （已清空这 10 张表，保证是全量重载）")

    for fname, table, cols in TABLES:
        rows = [[i(r[c]) if c in INT_COLS else
                 (float(r[c]) if (c in FLOAT_COLS and r[c] is not None) else r[c]) for c in cols]
                for r in d[table]]
        sql = (f"REPLACE INTO {table} ({', '.join(cols)}) "
               f"VALUES ({', '.join(['%s'] * len(cols))})")
        cursor.executemany(sql, rows)
        print(f"  {table:<26}{len(rows):>5} 行")


REFERENCE_SQL = [
    ("2025 Q1 中标率（口径：中标 ÷ 已结案，不含「跟进中」）", """
        SELECT ROUND(100 * SUM(quote_status='中标') / COUNT(*), 1) AS 中标率
        FROM fact_quotation
        WHERE quote_status IN ('中标','落标') AND quote_date_id < 20250401
    """),
    ("2025 Q1 为什么没接到单（落标原因分组）", """
        SELECT lost_reason, COUNT(*) AS 单数
        FROM fact_quotation
        WHERE quote_status='落标' AND quote_date_id < 20250401
        GROUP BY lost_reason ORDER BY 单数 DESC
    """),
    ("产量 vs 车间加工量（口径陷阱：两者不能相加）", """
        SELECT (SELECT SUM(output_quantity) FROM fact_production)                  AS 公司产量,
               (SELECT SUM(process_output)  FROM fact_production_process)          AS 车间加工量合计,
               (SELECT SUM(process_output)  FROM fact_production_process
                 WHERE workshop_id='W05')                                          AS 仅喷塑车间
    """),
    ("不良率最高的车间", """
        SELECT w.workshop_name, ROUND(100*SUM(p.defect_quantity)/SUM(p.process_output), 2) AS 不良率
        FROM fact_production_process p JOIN dim_workshop w ON p.workshop_id=w.workshop_id
        GROUP BY w.workshop_name ORDER BY 不良率 DESC
    """),
]


def verify(cursor):
    print("\n入库校验：")
    for _, table, _ in TABLES:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        print(f"  {table:<26}{cursor.fetchone()[0]:>5} 行")
    print("\n参考 SQL（README 里作为「地面真值」用，验收 agent 时逐题对照）：")
    for title, sql in REFERENCE_SQL:
        cursor.execute(sql)
        rows = cursor.fetchall()
        head = [c[0] for c in cursor.description]
        print(f"\n  ▸ {title}")
        print(f"    {' | '.join(head)}")
        for r in rows:
            print(f"    {' | '.join(str(x) for x in r)}")


if __name__ == "__main__":
    print("===== step2：CSV → MySQL =====\n")

    data = load_all()
    print(f"读到 {sum(len(v) for v in data.values())} 行，开始跑不变量断言\n")
    print("=" * 70)

    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()

    checker = InvariantChecker(as_of=TODAY)
    checker.run_all(data)
    cursor.execute("SELECT date_id FROM dim_date")
    checker.check_dates_in_dim(data, {r[0] for r in cursor.fetchall()})
    print(format_results(checker.results))
    print("=" * 70)

    bad = failures(checker.results)
    if bad:
        print(f"\n✗ 有 {len(bad)} 条不变量没过，**不入库**：")
        for f in bad:
            print(f"    - {f.name}")
        print("\n先修 step1 的造数逻辑再重跑 —— 带着已知的错进库，后面查起来会难十倍。")
        sys.exit(1)

    print("\n全部通过，开始全量入库（先清空再 REPLACE INTO）：")
    load_to_mysql(cursor, data)
    conn.commit()

    verify(cursor)
    cursor.close()
    conn.close()
    print("\n===== 完成 =====")
