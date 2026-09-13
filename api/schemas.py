"""Pydantic request/response models for the API layer."""

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
