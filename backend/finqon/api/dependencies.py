from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from finqon.core.errors import AuthenticationError
from finqon.core.logging import user_id_var
from finqon.core.security import decode_access_token
from finqon.resources import Resources
from finqon.services.auth import AuthService
from finqon.services.chat import ChatService

# ``auto_error=False`` so a missing header is a 401 with our error body, not a 403.
_bearer = HTTPBearer(auto_error=False, description="Access token from /api/v1/auth/login.")


def get_resources(request: Request) -> Resources:
    resources: Resources = request.app.state.resources
    return resources


ResourcesDep = Annotated[Resources, Depends(get_resources)]


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: uuid.UUID


async def get_current_user(
    resources: ResourcesDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    """Verify the bearer JWT. The user id comes only from the verified token."""
    if credentials is None:
        raise AuthenticationError("Missing bearer token.", code="missing_token")
    settings = resources.settings
    user_id = decode_access_token(
        credentials.credentials,
        secret=settings.jwt_secret.get_secret_value(),
        issuer=settings.jwt_issuer,
    )
    user_id_var.set(str(user_id))
    return CurrentUser(id=user_id)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def get_auth_service(resources: ResourcesDep) -> AuthService:
    return AuthService(resources.database, resources.settings)


def get_chat_service(resources: ResourcesDep) -> ChatService:
    return ChatService(resources)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"
