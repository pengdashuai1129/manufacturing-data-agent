
import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState, TableInfoState
from app.core.logging import logger
from app.prompt.prompt_loader import load_prompt


async def filter_table(state: DataAgentState, runtime: Runtime[DataAgentContext]):

    writer = runtime.stream_writer
    writer("过滤表信息")

    query = state["query"]
    table_infos: list[TableInfoState] = state["table_infos"]

    prompt = PromptTemplate(
        template=load_prompt("filter_table_info"),
        input_variables=["query", "table_infos"],
    )
    chain = prompt | llm | JsonOutputParser()

    result = await chain.ainvoke(
        {
            "query": query,
            "table_infos": yaml.dump(table_infos, allow_unicode=True, sort_keys=False),
        }
    )

    filtered_table_infos: list[TableInfoState] = []
    for table_info in table_infos:
        table_name = table_info["name"]
        if table_name not in result:
            continue

        kept_columns = [
            column_info
            for column_info in table_info["columns"]
            if column_info["name"] in result[table_name]
        ]
        new_table_info = dict(table_info)
        new_table_info["columns"] = kept_columns
        filtered_table_infos.append(new_table_info)

    required_columns: dict[str, set[str]] = {}
    for metric_info in state.get("metric_infos") or []:
        for column_id in metric_info["relevant_columns"]:
            table_name, _, column_name = column_id.partition(".")
            required_columns.setdefault(table_name, set()).add(column_name)

    original_column_index = {
        table_info["name"]: {c["name"]: c for c in table_info["columns"]}
        for table_info in table_infos
    }

    re_added: dict[str, list[str]] = {}
    for table_info in filtered_table_infos:
        table_name = table_info["name"]
        need = required_columns.get(table_name, set())
        if not need:
            continue
        have = {c["name"] for c in table_info["columns"]}
        missing = need - have
        if not missing:
            continue
        recoverable = [
            original_column_index[table_name][name]
            for name in sorted(missing)
            if name in original_column_index.get(table_name, {})
        ]
        if recoverable:
            table_info["columns"] = table_info["columns"] + recoverable
            re_added[table_name] = [c["name"] for c in recoverable]

    kept_table_names = {t["name"] for t in filtered_table_infos}
    for table_name, need in required_columns.items():
        if table_name in kept_table_names:
            continue
        original = next(
            (t for t in table_infos if t["name"] == table_name),
            None,
        )
        if original is None:
            continue
        filtered_table_infos.append(original)
        re_added[table_name] = ["<整表补回>"]

    if re_added:
        logger.info("补回指标必需字段：{}", re_added)

    logger.info("过滤后的表信息：{}", [t["name"] for t in filtered_table_infos])
    return {"table_infos": filtered_table_infos}
