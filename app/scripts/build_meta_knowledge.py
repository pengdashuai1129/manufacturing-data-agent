import argparse
import asyncio
from pathlib import Path

from app.clients.mysql_client import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)

from app.clients.embedding_client import embedding_client_manager

from app.clients.qdrant_client import qdrant_client_manager

from app.clients.es_client import es_client_manager

from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.repositories.es.value_es_repository import ValueESRepository

from app.services.meta_knowledge_service import MetaKnowledgeService


async def build(config_path: Path):
    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()
    qdrant_client_manager.init()
    embedding_client_manager.init()
    es_client_manager.init()

    try:
        async with (
            meta_mysql_client_manager.session_factory() as meta_session,
            dw_mysql_client_manager.session_factory() as dw_session,
        ):

            meta_repo = MetaMySQLRepository(meta_session)
            dw_repo = DWMySQLRepository(dw_session)
            column_qdrant_repo = ColumnQdrantRepository(qdrant_client_manager.client)
            metric_qdrant_repo = MetricQdrantRepository(qdrant_client_manager.client)
            value_es_repo = ValueESRepository(es_client_manager.client)

            service = MetaKnowledgeService(
                meta_repo,
                dw_repo,
                column_qdrant_repo,
                embedding_client_manager.client,
                value_es_repo,
                metric_qdrant_repo,
            )

            await service.build(config_path)

    finally:
        await meta_mysql_client_manager.close()
        await dw_mysql_client_manager.close()
        await qdrant_client_manager.close()
        await es_client_manager.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="构建元数据知识库：从 yaml 配置 → MySQL/Qdrant/ES"
    )
    parser.add_argument(
        "-c", "--conf",
        required=True,
        help="配置文件路径，如 conf/meta_config.yaml",
    )
    args = parser.parse_args()

    asyncio.run(build(Path(args.conf)))
