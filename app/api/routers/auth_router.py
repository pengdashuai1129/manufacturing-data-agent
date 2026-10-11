
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.dependencies import get_auth_service, get_current_user
from app.api.schemas.auth_schema import LoginSchema
from app.entities.user import User, role_label
from app.services.auth_service import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_HOURS,
    AuthService,
)

auth_router = APIRouter()


def _user_payload(user: User) -> dict:

    return {
        "user_id": user.user_id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "role_label": role_label(user.role),
        "employee_id": user.employee_id,
        "can_write": user.can_write,
    }


@auth_router.post("/api/login")
async def login_handler(
    payload: LoginSchema,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
):

    result = await auth_service.login(payload.username, payload.password)
    if result is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token, user = result
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="lax",
        path="/",
        secure=False,
    )
    return _user_payload(user)


@auth_router.post("/api/logout")
async def logout_handler(
    request: Request,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
):

    await auth_service.logout(request.cookies.get(SESSION_COOKIE_NAME))
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@auth_router.get("/api/me")
async def me_handler(user: Annotated[User, Depends(get_current_user)]):

    return _user_payload(user)
