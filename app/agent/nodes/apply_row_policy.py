
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.logging import logger
from app.services.row_policy import RowPolicyDenied, inject_row_policy


async def apply_row_policy(state: DataAgentState, runtime: Runtime[DataAgentContext]):

    sql = state["sql"]
    user = runtime.context["user"]
    dw_mysql_repository = runtime.context["dw_mysql_repository"]

    result = inject_row_policy(sql, user)

    if not result.ok:
        logger.warning(
            "行级权限拒绝执行：user={} role={} 原因={}", user.user_id, user.role, result.reason
        )
        raise RowPolicyDenied(result.reason)

    if not result.applied:
        logger.info("行级权限：user={} role={} 无需改写", user.user_id, user.role)
        return {}

    try:
        await dw_mysql_repository.validate(result.sql)
    except Exception as e:
        logger.exception("行级权限改写后的 SQL 未能通过解析")
        raise RowPolicyDenied(
            "按你的数据权限改写后，这条查询无法执行，请换一种问法或联系管理员"
        ) from e

    logger.info(
        "行级权限已改写：user={} role={} SQL={}", user.user_id, user.role, result.sql
    )

    return {"sql": result.sql, "row_policy_note": result.note}
