from qdrant_client import AsyncQdrantClient, models

from app.conf.app_config import app_config

from app.entities.column_info import ColumnInfo


class ColumnQdrantRepository:

    collection_name = "column_info_collection"

    def __init__(self, client: AsyncQdrantClient):
        self.client = client

    async def ensure_collection(self):
        if not await self.client.collection_exists(self.collection_name):
            await self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=app_config.qdrant.embedding_size,
                    distance=models.Distance.COSINE,
                ),
            )

    async def upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        payloads: list[dict],
        batch_size: int = 10,
    ):
        points: list[models.PointStruct] = [
            models.PointStruct(id=id, vector=embedding, payload=payload)
            for id, embedding, payload in zip(ids, embeddings, payloads)
        ]

        for i in range(0, len(points), batch_size):
            await self.client.upsert(
                collection_name=self.collection_name, points=points[i : i + batch_size]
            )

    async def search(
        self, embedding: list[float], score_threshold: float = 0.45, limit: int = 50
    ) -> list[ColumnInfo]:
        result = await self.client.query_points(
            collection_name=self.collection_name,
            query=embedding,
            limit=limit,
            score_threshold=score_threshold,
        )
        return [ColumnInfo(**point.payload) for point in result.points]
