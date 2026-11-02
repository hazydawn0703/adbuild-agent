from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore")
    database_url: str
    supabase_url: str
    supabase_service_key: str
    token_encryption_key: str
    openai_api_key: str = ""
    openai_model: str = "gpt-5.4-2026-03-05"
    app_origin: str = "http://localhost:3000"
    api_origin: str = "http://localhost:8000"
    tiktok_mcp_url: str = "https://business-api.tiktok.com/open_mcp/tt-ads-mcp-flat"


@lru_cache
def settings():
    return Settings()
