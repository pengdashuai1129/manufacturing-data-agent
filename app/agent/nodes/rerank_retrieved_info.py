
"""召回候选的重排节点。

放在 ``merge_retrieved_info`` 之后、``filter_table`` / ``filter_metric`` 之前：

    三路召回 → merge（并集成候选表） → **rerank** → filter_table / filter_metric → ...

合并之后才是「一份带表名、字段描述、别名、样例值的候选清单」，也才凑得出
给 cross-encoder 用的 ``[问题] [候选]`` 文本对。放在 merge 之前则三类元数据
（字段 / 取值 / 指标）混在一起，没有共同的打分文本。

**这个节点不会让正确结果变少**，这是刻意的：

- 默认只**重排序**不裁剪（``max_columns_per_table: 0``）
- 即使开了裁剪，主键 / 外键列与「指标依赖列」也永远保留 ——
  它们是 JOIN 的骨架，丢了不是排序变差，是 SQL 直接写不出来
- 下游 ``filter_table`` 本来就有「补回指标必需字段」的兜底

所以重排最坏的情况是「没帮上忙」，不是「改错了」。

排序与裁剪是纯函数（``rerank_tables`` / ``rerank_metrics``），不碰 IO，
可以脱离整条链路单独测。
"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.ranking import lexical_score
from app.agent.state import (
    ColumnInfoState,
    DataAgentState,
    MetricInfoState,
    TableInfoState,
)
from app.conf.app_config import app_config
from app.core.logging import logger

PROTECTED_ROLES = frozenset({"primary_key", "foreign_key"})

# 兜底打分里 RRF 共识与词面重合各占的权重。
# 给了 cross-encoder 之后这个常量就用不上了。
FALLBACK_RRF_WEIGHT = 0.5


def blend_fallback_scores(
    query: str,
    column_ids: list[str],
    documents: list[str],
    recall_scores: dict[str, float],
) -> list[float]:

    """cross-encoder 不在线时的兜底打分：RRF 共识与词面重合各占一半。

    **不直接用词面分排序**，因为那会把 RRF 算出来的名次信号整个丢掉 ——
    实测下来纯词面重排的 MRR 比纯 RRF 还略低（见 app/scripts/eval_recall.py）。
    混合等于「以融合结果为主，用词面做微调」。
    """

    prior_max = max(recall_scores.values(), default=0.0)

    scores = []
    for column_id, document in zip(column_ids, documents):
        prior = recall_scores.get(column_id, 0.0) / prior_max if prior_max else 0.0
        scores.append(
            FALLBACK_RRF_WEIGHT * prior
            + (1.0 - FALLBACK_RRF_WEIGHT) * lexical_score(query, document)
        )
    return scores


def column_text(table_name: str, column: ColumnInfoState) -> str:

    """拼给重排模型看的文本。字段名带上表名，否则不同表里的同名列无法区分。"""

    parts = [
        f"{table_name}.{column['name']}",
        column.get("description") or "",
        " ".join(column.get("alias") or []),
        " ".join(str(v) for v in (column.get("examples") or [])),
    ]
    return " ".join(part for part in parts if part)


def metric_text(metric: MetricInfoState) -> str:

    return " ".join(
        part
        for part in (
            metric["name"],
            metric.get("description") or "",
            " ".join(metric.get("alias") or []),
        )
        if part
    )


def protected_columns(metric_infos: list[MetricInfoState]) -> set[tuple[str, str]]:

    """指标直接依赖的列，格式是 ``表名.字段名``，统一小写后放进集合。"""

    protected: set[tuple[str, str]] = set()
    for metric in metric_infos:
        for reference in metric.get("relevant_columns") or []:
            if "." in reference:
                table_name, column_name = reference.split(".", 1)
                protected.add((table_name.lower(), column_name.lower()))
    return protected


def is_protected(
    table_name: str,
    column: ColumnInfoState,
    protected: set[tuple[str, str]],
) -> bool:

    """主键 / 外键（JOIN 的骨架）与指标依赖列（口径的落点）都不许被裁掉。"""

    if column.get("role") in PROTECTED_ROLES:
        return True
    return (table_name.lower(), column["name"].lower()) in protected


def rerank_tables(
    table_infos: list[TableInfoState],
    score_of: dict[tuple[str, str], float],
    protected: set[tuple[str, str]],
    max_per_table: int = 0,
) -> tuple[list[TableInfoState], int]:

    """按分数重排每张表的列、再按相关度重排表的先后。返回 (表, 被裁掉的列数)。

    ``max_per_table <= 0`` 表示只排序不裁剪。
    """

    dropped_total = 0

    for table in table_infos:

        table_name = table["name"]

        # 受保护的列永远排最前；其余按重排分数降序。list.sort 是稳定的，
        # 所以同分时保持 merge 阶段 RRF 融合出来的顺序。
        columns = sorted(
            table["columns"],
            key=lambda column: (
                not is_protected(table_name, column, protected),
                -score_of.get((table_name, column["name"]), 0.0),
            ),
        )

        if max_per_table and len(columns) > max_per_table:
            guarded = [
                column for column in columns if is_protected(table_name, column, protected)
            ]
            rest = [
                column for column in columns if not is_protected(table_name, column, protected)
            ]
            # 上限说的是「总列数」，但受保护列一个都不能少，所以实际保留数是二者较大值
            keep = max(max_per_table, len(guarded))
            dropped_total += len(columns) - keep
            columns = (guarded + rest)[:keep]

        table["columns"] = columns

    # 表也按「该表里最高分的那一列」重排，让最相关的表先进 prompt。
    # 全部分数为 0 时稳定排序保持 merge 的原始顺序。
    def _table_score(table: TableInfoState) -> float:
        return max(
            (
                score_of.get((table["name"], column["name"]), 0.0)
                for column in table["columns"]
            ),
            default=0.0,
        )

    table_infos.sort(key=lambda table: -_table_score(table))

    return table_infos, dropped_total


def rerank_metrics(
    metric_infos: list[MetricInfoState],
    scores: list[float],
) -> list[MetricInfoState]:

    """稳定排序：分数打平时保持召回的原始顺序。"""

    return [
        metric
        for _, metric in sorted(zip(scores, metric_infos), key=lambda pair: -pair[0])
    ]


async def rerank_retrieved_info(
    state: DataAgentState, runtime: Runtime[DataAgentContext]
):

    query = state["query"]
    table_infos: list[TableInfoState] = list(state["table_infos"])
    metric_infos: list[MetricInfoState] = list(state["metric_infos"])

    rerank_client = runtime.context["rerank_client"]
    recall_scores: dict[str, float] = state.get("recall_scores") or {}

    protected = protected_columns(metric_infos)

    owners: list[tuple[str, str]] = []
    documents: list[str] = []
    for table in table_infos:
        for column in table["columns"]:
            owners.append((table["name"], column["name"]))
            documents.append(column_text(table["name"], column))

    if not documents:
        return {"rerank_note": "候选为空，跳过重排"}

    # 字段 id 就是「表名.字段名」（见 meta_knowledge_service 里 id=f"{table}.{column}"），
    # 所以能直接拿它去 recall_scores 里取融合分
    column_ids = [f"{table_name}.{column_name}" for table_name, column_name in owners]

    scores = await rerank_client.score(query, documents)

    if scores is None:
        backend = "词面+RRF 混合重排（确定性）"
        scores = blend_fallback_scores(query, column_ids, documents, recall_scores)
    else:
        backend = f"cross-encoder 重排（{rerank_client.rerank_config.base_url}）"

    score_of = dict(zip(owners, scores))

    table_infos, dropped_total = rerank_tables(
        table_infos, score_of, protected, app_config.rerank.max_columns_per_table
    )

    # 指标走自己的打分文本（指标是口径描述，没有列名可借），
    # 与字段分数不是同一个尺度，所以单独算一轮、不混在一起排。
    if metric_infos:
        metric_documents = [metric_text(metric) for metric in metric_infos]
        metric_scores = await rerank_client.score(query, metric_documents)
        if metric_scores is None:
            metric_scores = [
                lexical_score(query, document) for document in metric_documents
            ]
        metric_infos = rerank_metrics(metric_infos, metric_scores)

    note = f"已用{backend}重排 {len(documents)} 个候选字段"
    if dropped_total:
        note += (
            f"，每表最多 {app_config.rerank.max_columns_per_table} 列，"
            f"裁掉 {dropped_total} 个（主键 / 外键与指标依赖列已保留）"
        )
    logger.info(note)

    return {
        "table_infos": table_infos,
        "metric_infos": metric_infos,
        "rerank_note": note,
    }
