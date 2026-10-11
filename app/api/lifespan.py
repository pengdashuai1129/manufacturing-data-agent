
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.clients.embedding_client import embedding_client_manager
from app.clients.es_client import es_client_manager
from app.clients.mysql_client import (
    app_mysql_client_manager,
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client import qdrant_client_manager


@asynccontextmanager
async def lifespan(app: FastAPI):

    qdrant_client_manager.init()
    embedding_client_manager.init()
    es_client_manager.init()
    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()
    app_mysql_client_manager.init()

    yield

    await qdrant_client_manager.close()
    await es_client_manager.close()
    await meta_mysql_client_manager.close()
    await dw_mysql_client_manager.close()
    await app_mysql_client_manager.close()
