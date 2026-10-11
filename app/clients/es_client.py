import asyncio
from typing import Optional

from elasticsearch import AsyncElasticsearch

from app.conf.app_config import ESConfig, app_config


class ESClientManager:

    def __init__(self, es_config: ESConfig):
        self.es_config = es_config
        self.client: Optional[AsyncElasticsearch] = None

    def _get_url(self):
        return f"http://{self.es_config.host}:{self.es_config.port}"

    def init(self):
        self.client = AsyncElasticsearch(hosts=[self._get_url()], request_timeout=120)

    async def close(self):
        await self.client.close()


es_client_manager = ESClientManager(app_config.es)


if __name__ == "__main__":
    es_client_manager.init()
    client = es_client_manager.client

    async def _test():
        await client.indices.create(
            index="my-books",
            mappings={
                "dynamic": False,
                "properties": {
                    "name": {"type": "text"},

                    "author": {"type": "text"},

                    "release_date": {"type": "date", "format": "yyyy-MM-dd"},

                    "page_count": {"type": "integer"},
                },
            },
        )

        await client.bulk(
            operations=[
                {"index": {"_index": "my-books"}},
                {
                    "name": "Revelation Space",
                    "author": "Alastair Reynolds",
                    "release_date": "2000-03-15",
                    "page_count": 585,
                },
                {"index": {"_index": "my-books"}},
                {
                    "name": "1984",
                    "author": "George Orwell",
                    "release_date": "1985-06-01",
                    "page_count": 328,
                },
                {"index": {"_index": "my-books"}},
                {
                    "name": "Fahrenheit 451",
                    "author": "Ray Bradbury",
                    "release_date": "1953-10-15",
                    "page_count": 227,
                },
                {"index": {"_index": "my-books"}},
                {
                    "name": "Brave New World",
                    "author": "Aldous Huxley",
                    "release_date": "1932-06-01",
                    "page_count": 268,
                },
                {"index": {"_index": "my-books"}},
                {
                    "name": "The Handmaids Tale",
                    "author": "Margaret Atwood",
                    "release_date": "1985-06-01",
                    "page_count": 311,
                },
            ],
        )

        resp = await client.search(
            index="my-books",
            query={"match": {"name": "brave"}},
        )

        print(f"搜索 'brave'，命中 {resp['hits']['total']['value']} 条:")
        for hit in resp["hits"]["hits"]:
            src = hit["_source"]
            print(f"  {src['name']} — {src['author']} ({src['release_date']}) {src['page_count']}页")

        await es_client_manager.close()
        print("\nES 客户端测试通过 ✓")

    asyncio.run(_test())
