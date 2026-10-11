
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from langchain_ollama import OllamaEmbeddings
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.embedding_client import embedding_client_manager
from app.clients.es_client import es_client_manager
from app.clients.mysql_client import (
    app_mysql_client_manager,
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client import qdrant_client_manager
from app.clients.rerank_client import RerankClient, rerank_client
from app.entities.user import User
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.app.app_mysql_repository import AppMySQLRepository
from app.repositories.mysql.dw.dw_fact_write_repository import DWFactsWriteRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.services.auth_service import SESSION_COOKIE_NAME, AuthService
from app.services.fact_write_service import FactWriteService
from app.services.query_service import QueryService


async def get_meta_session():

    async with meta_mysql_client_manager.session_factory() as meta_session:
        yield meta_session


async def get_meta_mysql_repository(
    session: Annotated[AsyncSession, Depends(get_meta_session)],
) -> MetaMySQLRepository:

    return MetaMySQLRepository(session)


async def get_dw_session():

    async with dw_mysql_client_manager.session_factory() as dw_session:
        yield dw_session


async def get_dw_mysql_repository(
    session: Annotated[AsyncSession, Depends(get_dw_session)],
) -> DWMySQLRepository:

    return DWMySQLRepository(session)


async def get_embedding_client() -> OllamaEmbeddings:

    return embedding_client_manager.client


async def get_column_qdrant_repository() -> ColumnQdrantRepository:

    return ColumnQdrantRepository(qdrant_client_manager.client)


async def get_metric_qdrant_repository() -> MetricQdrantRepository:

    return MetricQdrantRepository(qdrant_client_manager.client)


async def get_value_es_repository() -> ValueESRepository:

    return ValueESRepository(es_client_manager.client)


async def get_rerank_client() -> RerankClient:

    return rerank_client


async def get_app_session():

    async with app_mysql_client_manager.session_factory() as app_session:
        yield app_session


async def get_app_mysql_repository(
    session: Annotated[AsyncSession, Depends(get_app_session)],
) -> AppMySQLRepository:

    return AppMySQLRepository(session)


async def get_auth_service(
    app_mysql_repository: Annotated[
        AppMySQLRepository, Depends(get_app_mysql_repository)
    ],
) -> AuthService:

    return AuthService(app_mysql_repository)


async def get_current_user(
    request: Request,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> User:

    user = await auth_service.get_user_by_token(request.cookies.get(SESSION_COOKIE_NAME))
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return user


async def require_writer(
    user: Annotated[User, Depends(get_current_user)],
) -> User:

    if not user.can_write:
        raise HTTPException(status_code=403, detail="当前角色没有数据录入权限")
    return user


async def get_fact_write_service(
    dw_session: Annotated[AsyncSession, Depends(get_dw_session)],
    app_mysql_repository: Annotated[
        AppMySQLRepository, Depends(get_app_mysql_repository)
    ],
) -> FactWriteService:

    return FactWriteService(
        write_repository=DWFactsWriteRepository(dw_session),
        app_mysql_repository=app_mysql_repository,
    )


async def get_query_service(
    meta_mysql_repository: Annotated[
        MetaMySQLRepository, Depends(get_meta_mysql_repository)
    ],
    embedding_client: Annotated[
        OllamaEmbeddings, Depends(get_embedding_client)
    ],
    dw_mysql_repository: Annotated[
        DWMySQLRepository, Depends(get_dw_mysql_repository)
    ],
    column_qdrant_repository: Annotated[
        ColumnQdrantRepository, Depends(get_column_qdrant_repository)
    ],
    metric_qdrant_repository: Annotated[
        MetricQdrantRepository, Depends(get_metric_qdrant_repository)
    ],
    value_es_repository: Annotated[
        ValueESRepository, Depends(get_value_es_repository)
    ],
    app_mysql_repository: Annotated[
        AppMySQLRepository, Depends(get_app_mysql_repository)
    ],
    rerank_client: Annotated[RerankClient, Depends(get_rerank_client)],
) -> QueryService:

    return QueryService(
        meta_mysql_repository=meta_mysql_repository,
        embedding_client=embedding_client,
        dw_mysql_repository=dw_mysql_repository,
        column_qdrant_repository=column_qdrant_repository,
        metric_qdrant_repository=metric_qdrant_repository,
        value_es_repository=value_es_repository,
        app_mysql_repository=app_mysql_repository,
        rerank_client=rerank_client,
    )
