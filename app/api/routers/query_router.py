
from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from app.api.dependencies import get_current_user, get_query_service
from app.api.schemas.query_schema import QuerySchema
from app.entities.user import User
from app.services.query_service import QueryService

query_router = APIRouter()


@query_router.post("/api/query")
async def query_handler(
    query: QuerySchema,
    query_service: Annotated[QueryService, Depends(get_query_service)],
    user: Annotated[User, Depends(get_current_user)],
):

    return StreamingResponse(
        query_service.query(query.query, user),
        media_type="text/event-stream",
    )
