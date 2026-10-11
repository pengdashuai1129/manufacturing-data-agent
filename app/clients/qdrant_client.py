import random
from typing import Optional

from qdrant_client import AsyncQdrantClient, models

from app.conf.app_config import QdrantConfig, app_config


class QdrantClientManager:

    def __init__(self, qdrant_config: QdrantConfig):
        self.qdrant_config = qdrant_config

        self.client: Optional[AsyncQdrantClient] = None

    def _get_url(self):
        return f"http://{self.qdrant_config.host}:{self.qdrant_config.port}"

    def init(self):
        self.client = AsyncQdrantClient(url=self._get_url(), timeout=120)

    async def close(self):
        await self.client.close()


qdrant_client_manager = QdrantClientManager(app_config.qdrant)











if __name__ == "__main__":
    import asyncio

    async def _test():
        qdrant_client_manager.init()
        client = qdrant_client_manager.client

        TEST_COLLECTION = "my_collection"
        if await client.collection_exists(TEST_COLLECTION):
            await client.delete_collection(TEST_COLLECTION)

        await client.create_collection(
            collection_name=TEST_COLLECTION,
            vectors_config=models.VectorParams(
                size=10,
                distance=models.Distance.COSINE,
            ),
        )

        await client.upsert(
            collection_name=TEST_COLLECTION,
            points=[
                models.PointStruct(
                    id=i,
                    vector=[random.random() for _ in range(10)],
                )
                for i in range(100)
            ],
        )

        res = await client.query_points(
            collection_name=TEST_COLLECTION,
            query=[random.random() for _ in range(10)],
            limit=10,
            score_threshold=0.8,
        )

        print(f"查询结果（score >= 0.8）: {len(res.points)} 条")
        for p in res.points[:3]:
            print(f"  id={p.id}, score={p.score:.4f}")

        await qdrant_client_manager.close()
        print("Qdrant 客户端测试通过 ✓")

    asyncio.run(_test())
