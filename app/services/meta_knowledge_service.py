import asyncio

import uuid

from dataclasses import asdict

from pathlib import Path

from langchain_ollama import OllamaEmbeddings

from omegaconf import OmegaConf

from app.conf.meta_config import MetaConfig

from app.entities.table_info import TableInfo
from app.entities.column_info import ColumnInfo
from app.entities.column_metric import ColumnMetric
from app.entities.metric_info import MetricInfo
from app.entities.value_info import ValueInfo

from app.core.logging import logger

from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository

from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository

from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository

from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository

from app.repositories.es.value_es_repository import ValueESRepository


def point_id(entity_id: str, embedding_text: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{entity_id}|{embedding_text}"))


class MetaKnowledgeService:

    def __init__(
        self,
        meta_mysql_repository: MetaMySQLRepository,
        dw_mysql_repository: DWMySQLRepository,
        column_qdrant_repository: ColumnQdrantRepository,
        embedding_client: OllamaEmbeddings,
        value_es_repository: ValueESRepository,
        metric_qdrant_repository: MetricQdrantRepository,
    ):
        self.meta_mysql_repository = meta_mysql_repository

        self.dw_mysql_repository = dw_mysql_repository

        self.column_qdrant_repository = column_qdrant_repository

        self.embedding_client = embedding_client

        self.value_es_repository = value_es_repository

        self.metric_qdrant_repository = metric_qdrant_repository

    async def _embed_in_batches(
        self, texts: list[str], batch_size: int = 20, max_retries: int = 3
    ) -> list[list[float]]:
        embeddings: list[list[float]] = []
        batch_count = (len(texts) + batch_size - 1) // batch_size

        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i : i + batch_size]
            batch_no = i // batch_size + 1

            for attempt in range(max_retries + 1):
                try:
                    batch_embeddings = await self.embedding_client.aembed_documents(
                        batch_texts
                    )
                    break
                except Exception as e:
                    if attempt >= max_retries:
                        logger.error(
                            f"向量化失败：第 {batch_no}/{batch_count} 批，"
                            f"重试 {max_retries} 次仍未成功，放弃"
                        )
                        raise
                    wait = 2**attempt
                    logger.warning(
                        f"向量化第 {batch_no}/{batch_count} 批失败"
                        f"（第 {attempt + 1} 次尝试）：{type(e).__name__}: {e}；"
                        f"{wait}s 后重试"
                    )
                    await asyncio.sleep(wait)

            embeddings.extend(batch_embeddings)

        return embeddings

    async def _save_tables_to_meta_db(self, meta_config: MetaConfig) -> list[ColumnInfo]:
        table_infos: list[TableInfo] = []
        column_infos: list[ColumnInfo] = []

        for table in meta_config.tables:
            table_info = TableInfo(
                id=table.name,
                name=table.name,
                role=table.role,
                description=table.description,
            )
            table_infos.append(table_info)

            column_types = await self.dw_mysql_repository.get_column_types(table.name)

            for column in table.columns:
                column_values = await self.dw_mysql_repository.get_column_values(
                    table.name, column.name, 10
                )

                column_info = ColumnInfo(
                    id=f"{table.name}.{column.name}",
                    name=column.name,
                    type=column_types[column.name],
                    role=column.role,
                    examples=column_values,
                    description=column.description,
                    alias=column.alias,
                    table_id=table.name,
                )
                column_infos.append(column_info)

        async with self.meta_mysql_repository.session.begin():
            self.meta_mysql_repository.save_table_infos(table_infos)
            self.meta_mysql_repository.save_column_infos(column_infos)

        return column_infos

    async def _save_column_info_to_qdrant(self, column_infos: list[ColumnInfo]):
        await self.column_qdrant_repository.ensure_collection()

        points: list[dict] = []
        for column_info in column_infos:
            points.append(
                {
                    "id": point_id(column_info.id, column_info.name),
                    "embedding_text": column_info.name,
                    "payload": asdict(column_info),
                }
            )
            points.append(
                {
                    "id": point_id(column_info.id, column_info.description),
                    "embedding_text": column_info.description,
                    "payload": asdict(column_info),
                }
            )
            for alias in column_info.alias:
                points.append(
                    {
                        "id": point_id(column_info.id, alias),
                        "embedding_text": alias,
                        "payload": asdict(column_info),
                    }
                )

        embedding_texts = [point["embedding_text"] for point in points]
        embeddings = await self._embed_in_batches(embedding_texts)

        ids = [point["id"] for point in points]
        payloads = [point["payload"] for point in points]
        await self.column_qdrant_repository.upsert(ids, embeddings, payloads)

    async def _save_value_info_to_es(
        self, meta_config: MetaConfig, column_infos: list[ColumnInfo]
    ):
        await self.value_es_repository.ensure_index()

        column2sync: dict[str, bool] = {}
        for table in meta_config.tables:
            for column in table.columns:
                column2sync[f"{table.name}.{column.name}"] = column.sync

        value_infos: list[ValueInfo] = []
        for column_info in column_infos:
            if not column2sync[column_info.id]:
                continue
            current_column_values = await self.dw_mysql_repository.get_column_values(
                column_info.table_id, column_info.name, 100000
            )
            for value in current_column_values:
                value_infos.append(
                    ValueInfo(
                        id=f"{column_info.id}.{value}",
                        value=value,
                        column_id=column_info.id,
                    )
                )

        await self.value_es_repository.index(value_infos)

    async def _save_metrics_to_meta_db(
        self, meta_config: MetaConfig
    ) -> list[MetricInfo]:
        metric_infos: list[MetricInfo] = []
        column_metrics: list[ColumnMetric] = []

        for metric in meta_config.metrics:
            metric_info = MetricInfo(
                id=metric.name,
                name=metric.name,
                description=metric.description,
                relevant_columns=metric.relevant_columns,
                alias=metric.alias,
            )
            metric_infos.append(metric_info)

            for column_id in metric.relevant_columns:
                column_metrics.append(
                    ColumnMetric(column_id=column_id, metric_id=metric.name)
                )

        async with self.meta_mysql_repository.session.begin():
            self.meta_mysql_repository.save_metric_infos(metric_infos)
            self.meta_mysql_repository.save_column_metrics(column_metrics)

        return metric_infos

    async def _save_metric_info_to_qdrant(self, metric_infos: list[MetricInfo]):
        await self.metric_qdrant_repository.ensure_collection()

        points: list[dict] = []
        for metric_info in metric_infos:
            points.append(
                {
                    "id": point_id(metric_info.name, metric_info.name),
                    "embedding_text": metric_info.name,
                    "payload": asdict(metric_info),
                }
            )
            points.append(
                {
                    "id": point_id(metric_info.name, metric_info.description),
                    "embedding_text": metric_info.description,
                    "payload": asdict(metric_info),
                }
            )
            for alias in metric_info.alias:
                points.append(
                    {
                        "id": point_id(metric_info.name, alias),
                        "embedding_text": alias,
                        "payload": asdict(metric_info),
                    }
                )

        embedding_texts = [point["embedding_text"] for point in points]
        embeddings = await self._embed_in_batches(embedding_texts)

        ids = [point["id"] for point in points]
        payloads = [point["payload"] for point in points]
        await self.metric_qdrant_repository.upsert(ids, embeddings, payloads)

    async def build(self, config_path: Path):
        context = OmegaConf.load(config_path)

        schema = OmegaConf.structured(MetaConfig)

        meta_config: MetaConfig = OmegaConf.to_object(
            OmegaConf.merge(schema, context)
        )

        logger.info("元数据配置文件加载完成")
        logger.info(f"  配置了 {len(meta_config.tables or [])} 张表")
        logger.info(f"  配置了 {len(meta_config.metrics or [])} 个指标")

        if meta_config.tables:
            column_infos = await self._save_tables_to_meta_db(meta_config)
            logger.info("保存表信息和字段信息到 Meta MySQL 成功")

            await self._save_column_info_to_qdrant(column_infos)
            logger.info("字段向量写入 Qdrant 成功")

            await self._save_value_info_to_es(meta_config, column_infos)
            logger.info("字段取值写入 ES 成功")

        if meta_config.metrics:
            metric_infos = await self._save_metrics_to_meta_db(meta_config)
            logger.info("保存指标信息和字段依赖关系到 Meta MySQL 成功")

            await self._save_metric_info_to_qdrant(metric_infos)
            logger.info("指标向量写入 Qdrant 成功")

        logger.info("元数据知识库构建完成")
