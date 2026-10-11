
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse

from app.api.dependencies import get_auth_service
from app.api.lifespan import lifespan
from app.api.routers.auth_router import auth_router
from app.api.routers.order_router import order_router
from app.api.routers.query_router import query_router
from app.core.context import generate_request_id, request_id_ctx_var
from app.services.auth_service import SESSION_COOKIE_NAME, AuthService

app = FastAPI(lifespan=lifespan)

app.include_router(auth_router)
app.include_router(query_router)
app.include_router(order_router)

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
async def read_index(
    request: Request,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
):

    user = await auth_service.get_user_by_token(request.cookies.get(SESSION_COOKIE_NAME))
    if user is None:
        return FileResponse(STATIC_DIR / "login.html")
    return FileResponse(STATIC_DIR / "index.html")


@app.middleware("http")
async def add_request_id(request: Request, call_next):

    request_id = generate_request_id()
    request_id_ctx_var.set(request_id)

    response = await call_next(request)

    return response
