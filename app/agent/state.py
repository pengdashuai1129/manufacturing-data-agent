
from typing import TypedDict

from app.entities.column_info import ColumnInfo
from app.entities.metric_info import MetricInfo
from app.entities.value_info import ValueInfo


class ColumnInfoState(TypedDict):

    name: str
    type: str
    role: str
    examples: list
    description: str
    alias: list[str]
 


class TableInfoState(TypedDict):

    name: str
    role: str
    description: str
    columns: list[ColumnInfoState]


class MetricInfoState(TypedDict):

    name: str
    description: str
    relevant_columns: list[str]
    alias: list[str]


class DateInfoState(TypedDict):

    date: str
    weekday: str
    quarter: str


class DBInfoState(TypedDict):

    dialect: str
    version: str


class DataAgentState(TypedDict):

    query: str

    keywords: list[str]

    retrieved_column_infos: list[ColumnInfo]

    # 字段 id → RRF 融合分。单独放一个键，是为了不把分数塞进 table_infos ——
    # 那份结构会被 yaml.dump 进 LLM 提示词，多一个字段就是多一份噪声。
    # 重排节点靠它把「多路召回的共识」和「词面重合度」混起来。
    recall_scores: dict[str, float]

    retrieved_value_infos: list[ValueInfo]

    retrieved_metric_infos: list[MetricInfo]

    table_infos: list[TableInfoState]

    metric_infos: list[MetricInfoState]

    rerank_note: str | None

    date_info: DateInfoState

    db_info: DBInfoState

    sql: str

    row_policy_note: str | None

    error: str | None

    result: list[dict]
