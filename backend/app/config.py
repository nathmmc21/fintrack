from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://fintrack:fintrack_pw@localhost:55432/fintrack"
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    auth_cookie_secure: bool = False
    cors_origins: str = "http://localhost:5173"

    ollama_base_url: str = "http://localhost:11434"
    ai_classifier_model: str = "qwen3:4b"
    ai_timeout_seconds: float = Field(default=120.0, gt=0, le=600)

    @field_validator("jwt_secret")
    @classmethod
    def strong_secret(cls, value: str) -> str:
        if len(value) < 32 or value.lower().startswith("replace-"):
            raise ValueError("JWT_SECRET must be a generated random string of at least 32 characters.")
        return value

    @field_validator("ollama_base_url")
    @classmethod
    def valid_ollama_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("OLLAMA_BASE_URL must start with http:// or https://")
        return normalized


settings = Settings()
