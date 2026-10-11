from typing import Optional
from langchain_ollama import OllamaEmbeddings

from app.conf.app_config import EmbeddingConfig, app_config


class EmbeddingClientManager:

    def __init__(self, config: EmbeddingConfig):
        self.config = config
        self.client: Optional[OllamaEmbeddings] = None

    def init(self):
        self.client = OllamaEmbeddings(
            model=self.config.model,
            base_url=f"http://{self.config.host}:{self.config.port}",
        )



embedding_client_manager = EmbeddingClientManager(app_config.embedding)


if __name__ == "__main__":
    embedding_client_manager.init()
    client = embedding_client_manager.client

    vector = client.embed_query("测试一段中文文本")
    print(f"向量维度: {len(vector)}")
    print(f"前 5 个维度: {vector[:5]}")
    print("Embedding 客户端测试通过 ✓")
