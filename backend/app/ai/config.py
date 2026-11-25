from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AISettings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_prefix='AI_', extra='ignore')

    base_url: str = 'https://modelapi.vn/v1'
    api_key: SecretStr | None = None
    model: str = Field(default='gpt-6-luna', min_length=1)
    reasoning_effort: Literal['low', 'medium', 'high'] = 'low'
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    max_output_tokens: int = Field(default=512, ge=128, le=2048)

    @field_validator('base_url')
    @classmethod
    def validate_base_url(cls, value):
        parsed = urlsplit(value)
        parsed.port  # Validate malformed ports before constructing an HTTP request.
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('AI_BASE_URL must be HTTPS without credentials, query or fragment.')
        return value.rstrip('/')
