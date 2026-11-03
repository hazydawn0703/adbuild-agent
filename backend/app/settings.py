from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore")
    database_url: str = Field(min_length=1)
    session_secret: str = Field(min_length=32)
    storage_directory: str = "/opt/adbuild-agent/data/workbooks"
    token_encryption_key: str = Field(min_length=1)
    llm_api_key: str = ""
    llm_model: str = "deepseek-flash"
    llm_base_url: str = "https://api.deepseek.com"
    execution_enabled: bool = False
    app_origin: str = "http://localhost:3000"
    api_origin: str = "http://localhost:8000"
    tiktok_mcp_url: str = "https://business-api.tiktok.com/open_mcp/tt-ads-mcp-flat"


@lru_cache
def settings():
    return Settings()
