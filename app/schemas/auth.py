from pydantic import BaseModel, Field


class AccessCodeRequest(BaseModel):
    access_code: str = Field(min_length=1, max_length=512)


class AuthSessionResponse(BaseModel):
    enabled: bool
    authenticated: bool
    expires_in_seconds: int | None = None
