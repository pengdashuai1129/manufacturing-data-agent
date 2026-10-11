
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.logging import logger


async def run_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):

    sql = state["sql"]
    dw_mysql_repository = runtime.context["dw_mysql_repository"]

    result = await dw_mysql_repository.run(sql)
    logger.info("SQL 执行结果：{}", result)

    return {"result": result}
