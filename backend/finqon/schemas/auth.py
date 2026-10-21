from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, field_validator

Password = Annotated[str, Field(min_length=10, max_length=128)]


def _normalize_email(value: str) -> str:
    return value.strip().lower()


class RegisterRequest(BaseModel):
    email: EmailStr
    password: Password

    @field_validator("email", mode="after")
    @classmethod
    def _lowercase(cls, value: str) -> str:
        return _normalize_email(value)


class LoginRequest(BaseModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=128)]

    @field_validator("email", mode="after")
    @classmethod
    def _lowercase(cls, value: str) -> str:
        return _normalize_email(value)


class RefreshRequest(BaseModel):
    refresh_token: Annotated[str, Field(min_length=20, max_length=256)]


class LogoutRequest(RefreshRequest):
    pass


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str


class RegisteredUser(UserResponse):
    created_at: dt.datetime


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds.")
