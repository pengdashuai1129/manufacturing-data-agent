
import json
import time
from datetime import datetime

from langchain_ollama import OllamaEmbeddings

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.agent.state import DataAgentState
from app.clients.rerank_client import RerankClient
from app.core.context import request_id_ctx_var
from app.core.logging import logger
from app.entities.user import User
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.app.app_mysql_repository import AppMySQLRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.services.row_policy import RowPolicyDenied

NODE_LABELS = {
    "extract_keywords": "抽取关键词",
    "recall_column": "召回字段",
    "recall_value": "召回取值",
    "recall_metric": "召回指标",
    "merge_retrieved_info": "合并召回信息",
    "rerank_retrieved_info": "重排候选",
    "filter_table": "过滤表结构",
    "filter_metric": "过滤指标",
    "add_extra_context": "补充上下文",
    "generate_sql": "生成SQL",
    "validate_sql": "校验SQL",
    "correct_sql": "修正SQL",
    "apply_row_policy": "应用数据权限",
    "run_sql": "执行查询",
}


def _to_sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


class QueryService:

    def __init__(
        self,
        meta_mysql_repository: MetaMySQLRepository,
        embedding_client: OllamaEmbeddings,
        dw_mysql_repository: DWMySQLRepository,
        column_qdrant_repository: ColumnQdrantRepository,
        metric_qdrant_repository: MetricQdrantRepository,
        value_es_repository: ValueESRepository,
        app_mysql_repository: AppMySQLRepository,
        rerank_client: RerankClient,
    ):
        self.meta_mysql_repository = meta_mysql_repository
        self.dw_mysql_repository = dw_mysql_repository
        self.embedding_client = embedding_client
        self.column_qdrant_repository = column_qdrant_repository
        self.metric_qdrant_repository = metric_qdrant_repository
        self.value_es_repository = value_es_repository
        self.app_mysql_repository = app_mysql_repository
        self.rerank_client = rerank_client

    async def query(self, query: str, user: User):

        state = DataAgentState(query=query)

        context = DataAgentContext(
            column_qdrant_repository=self.column_qdrant_repository,
            metric_qdrant_repository=self.metric_qdrant_repository,
            value_es_repository=self.value_es_repository,
            embedding_client=self.embedding_client,
            meta_mysql_repository=self.meta_mysql_repository,
            dw_mysql_repository=self.dw_mysql_repository,
            rerank_client=self.rerank_client,
            user=user,
        )

        started_at = time.monotonic()
        final_sql: str | None = None
        row_count: int | None = None
        status = "ok"

        try:
            async for chunk in graph.astream(
                input=state, context=context, stream_mode="updates"
            ):
                for node_name, node_update in chunk.items():
                    yield _to_sse(
                        {
                            "type": "progress",
                            "node": node_name,
                            "label": NODE_LABELS.get(node_name, node_name),
                        }
                    )

                    if node_name == "generate_sql":
                        yield _to_sse({"type": "sql", "sql": node_update["sql"]})

                    if node_name == "apply_row_policy" and node_update.get("row_policy_note"):
                        yield _to_sse(
                            {
                                "type": "sql_final",
                                "sql": node_update.get("sql"),
                                "note": node_update["row_policy_note"],
                            }
                        )

                    if node_name in ("generate_sql", "correct_sql", "apply_row_policy"):
                        final_sql = node_update.get("sql") or final_sql

                    if node_name == "run_sql":
                        result = node_update.get("result") or []
                        row_count = len(result)
                        yield _to_sse({"type": "result", "result": result})

        except RowPolicyDenied as e:
            status = "rejected"
            logger.warning("问数请求被行级权限拒绝：{}", e)
            yield _to_sse({"type": "error", "message": str(e)})

        except Exception as e:

            status = "error"
            logger.exception("问数工作流执行失败")
            yield _to_sse({"type": "error", "message": f"{type(e).__name__}: {e}"})

        finally:
            try:
                self.app_mysql_repository.add_query_log(
                    user_id=user.user_id,
                    question=query,
                    final_sql=final_sql,
                    status=status,
                    row_count=row_count,
                    elapsed_ms=int((time.monotonic() - started_at) * 1000),
                    request_id=request_id_ctx_var.get(),
                    created_at=datetime.now(),
                )
                await self.app_mysql_repository.session.commit()
            except Exception:
                logger.exception("写查询日志失败（不影响本次回答）")
