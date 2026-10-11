
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import (
    ColumnInfoState,
    DataAgentState,
    MetricInfoState,
    TableInfoState,
)
from app.core.logging import logger
from app.entities.column_info import ColumnInfo
from app.entities.metric_info import MetricInfo
from app.entities.table_info import TableInfo
from app.entities.value_info import ValueInfo


async def merge_retrieved_info(state: DataAgentState, runtime: Runtime[DataAgentContext]):

    writer = runtime.stream_writer
    writer("合并召回信息")

    retrieved_column_infos: list[ColumnInfo] = state["retrieved_column_infos"]
    retrieved_metric_infos: list[MetricInfo] = state["retrieved_metric_infos"]
    retrieved_value_infos: list[ValueInfo] = state["retrieved_value_infos"]

    meta_mysql_repository = runtime.context["meta_mysql_repository"]

    retrieved_column_infos_map: dict[str, ColumnInfo] = {
        retrieved_column_info.id: retrieved_column_info
        for retrieved_column_info in retrieved_column_infos
    }

    for retrieved_metric_info in retrieved_metric_infos:
        for relevant_column in retrieved_metric_info.relevant_columns:
            if relevant_column not in retrieved_column_infos_map:
                column_info: ColumnInfo | None = (
                    await meta_mysql_repository.get_column_info_by_id(relevant_column)
                )
                if column_info is None:
                    logger.warning(
                        f"指标 {retrieved_metric_info.name} 依赖的字段 "
                        f"{relevant_column} 在 meta 库中不存在，已跳过（先重建知识库？）"
                    )
                    continue
                retrieved_column_infos_map[relevant_column] = column_info

    for retrieved_value_info in retrieved_value_infos:
        value = retrieved_value_info.value
        column_id = retrieved_value_info.column_id
        column_info = retrieved_column_infos_map.get(column_id)
        if column_info is None:
            column_info = await meta_mysql_repository.get_column_info_by_id(column_id)
            if column_info is None:
                logger.warning(
                    f"召回取值「{value}」指向的字段 {column_id} 在 meta 库中"
                    f"不存在，已丢弃（ES 里可能有旧场景残留，建议重建知识库）"
                )
                continue
            retrieved_column_infos_map[column_id] = column_info
        if column_info.examples is None:
            column_info.examples = []
        if value not in column_info.examples:
            column_info.examples.append(value)

    table_to_columns_map: dict[str, list[ColumnInfo]] = {}
    for column_info in retrieved_column_infos_map.values():
        table_id = column_info.table_id
        if table_id not in table_to_columns_map:
            table_to_columns_map[table_id] = []
        table_to_columns_map[table_id].append(column_info)

    for table_id in table_to_columns_map.keys():
        key_columns: list[ColumnInfo] = (
            await meta_mysql_repository.get_key_columns_by_table_id(table_id)
        )
        column_ids = [column_info.id for column_info in table_to_columns_map[table_id]]
        for key_column in key_columns:
            if key_column.id not in column_ids:
                table_to_columns_map[table_id].append(key_column)

    table_infos: list[TableInfoState] = []
    for table_id, column_infos in table_to_columns_map.items():
        table_info: TableInfo | None = await meta_mysql_repository.get_table_info_by_id(
            table_id
        )
        if table_info is None:
            logger.warning(f"字段所属的表 {table_id} 在 meta 库中不存在，整表跳过")
            continue
        columns = [
            ColumnInfoState(
                name=column_info.name,
                type=column_info.type,
                role=column_info.role,
                examples=column_info.examples,
                description=column_info.description,
                alias=column_info.alias,
            )
            for column_info in column_infos
        ]
        table_infos.append(
            TableInfoState(
                name=table_info.name,
                role=table_info.role,
                description=table_info.description,
                columns=columns,
            )
        )

    metric_infos: list[MetricInfoState] = [
        MetricInfoState(
            name=retrieved_metric_info.name,
            description=retrieved_metric_info.description,
            relevant_columns=retrieved_metric_info.relevant_columns,
            alias=retrieved_metric_info.alias,
        )
        for retrieved_metric_info in retrieved_metric_infos
    ]

    logger.info(f"合并后的表：{[t['name'] for t in table_infos]}")
    logger.info(f"合并后的指标：{[m['name'] for m in metric_infos]}")

    return {"table_infos": table_infos, "metric_infos": metric_infos}
