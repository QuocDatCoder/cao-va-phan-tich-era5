from pydantic import BaseModel, Field


class CredentialsRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    expires_at: int


class UserResponse(BaseModel):
    id: int
    username: str
    is_admin: bool = False


class AdminUserResponse(UserResponse):
    is_active: bool
    created_at: int


class UserAccessRequest(BaseModel):
    is_admin: bool | None = None
    is_active: bool | None = None


class AdminHandoffRequest(BaseModel):
    ticket: str = Field(min_length=32, max_length=128)
