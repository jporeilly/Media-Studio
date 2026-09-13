"""Pydantic request/response models for the API layer."""

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str


class TranscribeRequest(BaseModel):
    model: str | None = None  # Whisper model size; None = recommended default


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class TranscriptUpdate(BaseModel):
    transcript: list[TranscriptSegment]


class GenerateRequest(BaseModel):
    voice_id: str
    speed: float = 1.0
    preset: str = "youtube_1080p"
