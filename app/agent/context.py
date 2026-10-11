
from typing import TypedDict
from langchain_ollama import OllamaEmbeddings

from app.clients.rerank_client import RerankClient
from app.entities.user import User
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository

class DataAgentContext(TypedDict):
    column_qdrant_repository: ColumnQdrantRepository

    metric_qdrant_repository: MetricQdrantRepository

    value_es_repository: ValueESRepository

    embedding_client: OllamaEmbeddings

    meta_mysql_repository: MetaMySQLRepository

    dw_mysql_repository: DWMySQLRepository

    rerank_client: RerankClient

    user: User
