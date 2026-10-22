from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from finqon.api.dependencies import (
    AuthServiceDep,
    CurrentUserDep,
    ResourcesDep,
    client_key,
)
from finqon.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisteredUser,
    RegisterRequest,
    TokenPair,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=RegisteredUser, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, request: Request, service: AuthServiceDep, resources: ResourcesDep
) -> RegisteredUser:
    resources.auth_limiter.check(f"register:{client_key(request)}")
    user = await service.register(body.email, body.password, body.display_name)
    return RegisteredUser(
        id=user.id, email=user.email, display_name=user.display_name, created_at=user.created_at
    )


@router.post("/login", response_model=TokenPair)
async def login(
    body: LoginRequest, request: Request, service: AuthServiceDep, resources: ResourcesDep
) -> TokenPair:
    resources.auth_limiter.check(f"login:{client_key(request)}")
    resources.auth_limiter.check(f"login:{body.email}")
    return await service.login(body.email, body.password)


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, service: AuthServiceDep) -> TokenPair:
    return await service.refresh(body.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: LogoutRequest, service: AuthServiceDep) -> Response:
    """Revoke the presented refresh token. Idempotent: unknown tokens also return 204."""
    await service.logout(body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUserDep, service: AuthServiceDep) -> UserResponse:
    record = await service.get_user(user.id)
    return UserResponse(id=record.id, email=record.email, display_name=record.display_name)
