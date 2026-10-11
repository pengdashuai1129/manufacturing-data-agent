from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from omegaconf import OmegaConf

load_dotenv()



@dataclass
class File:
    enable: bool
    level: str
    path: str
    rotation: str
    retention: str


@dataclass
class Console:
    enable: bool
    level: str


@dataclass
class LoggingConfig:
    file: File
    console: Console


@dataclass
class DBConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass
class QdrantConfig:
    host: str
    port: int
    embedding_size: int


@dataclass
class EmbeddingConfig:
    host: str
    port: int
    model: str


@dataclass
class ESConfig:
    host: str
    port: int
    index_name: str


@dataclass
class LLMConfig:
    model_name: str
    api_key: str
    base_url: str


@dataclass
class RerankConfig:
    enable: bool
    base_url: str
    path: str
    timeout: int
    max_columns_per_table: int


@dataclass
class AppConfig:
    logging: LoggingConfig
    db_meta: DBConfig
    db_dw: DBConfig
    db_app: DBConfig
    qdrant: QdrantConfig
    embedding: EmbeddingConfig
    es: ESConfig
    llm: LLMConfig
    rerank: RerankConfig



config_file = Path(__file__).parents[2] / "conf" / "app_config.yaml"

context = OmegaConf.load(config_file)

schema = OmegaConf.structured(AppConfig)

app_config: AppConfig = OmegaConf.to_object(OmegaConf.merge(schema, context))


if __name__ == "__main__":
    print(f"ES host: {app_config.es.host}")
    print(f"LLM model: {app_config.llm.model_name}")
    print(f"Qdrant port: {app_config.qdrant.port}")
    print(f"Embedding: {app_config.embedding.model} @ {app_config.embedding.port}")
