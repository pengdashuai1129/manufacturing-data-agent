
import re

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.logging import logger

_JOIN_ON_RE = re.compile(
    r"\bJOIN\b.*?\bON\b(.*?)"
    r"(?=\bJOIN\b|\bWHERE\b|\bGROUP\b|\bORDER\b|\bLIMIT\b|\bHAVING\b|\bUNION\b|\)|$)",
    re.IGNORECASE | re.DOTALL,
)
_QUALIFIED_COL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\.\s*[A-Za-z_]\w*")


def find_tautological_on(sql: str) -> list[str]:
    bad: list[str] = []

    for on_expr in _JOIN_ON_RE.findall(sql):
        aliases = {m.group(1).lower() for m in _QUALIFIED_COL_RE.finditer(on_expr)}
        if len(aliases) < 2:
            bad.append("ON " + " ".join(on_expr.split()))

    return bad


async def validate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):

    writer = runtime.stream_writer
    writer("校验SQL")

    sql = state["sql"]
    dw_mysql_repository = runtime.context["dw_mysql_repository"]

    bad_on = find_tautological_on(sql)
    if bad_on:
        error = (
            "SQL 语义错误：JOIN 的 ON 条件恒真 —— 两侧引用的是同一个表别名。\n"
            "问题片段：\n"
            + "\n".join(f"  {cond}" for cond in bad_on)
            + "\nJOIN 的 ON 必须写成「子表的外键列 = 另一张表的主键列」，两侧的表别名必须不同，"
            "例如 ON fo.customer_id = dc.customer_id。\n"
            "当前写法在任何数据上都成立，这条 JOIN 会退化成笛卡尔积，"
            "把后面所有 SUM / COUNT 的结果按另一张表的行数成倍放大。"
        )
        logger.warning("SQL 语义自检未通过（ON 恒真）：{}", bad_on)
        return {"error": error}

    try:
        await dw_mysql_repository.validate(sql)
        logger.info("SQL 语法正确")
        return {"error": None}
    except Exception as e:
        logger.info("SQL 语法错误：{}", str(e))
        return {"error": str(e)}
