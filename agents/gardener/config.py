"""Application settings for the gardener agent container."""
from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


LLMProviderName = Literal["mock", "openai", "grok"]


class Settings(BaseSettings):
    """Environment-driven configuration."""

    model_config = SettingsConfigDict(
        env_prefix="GARDENER_",
        case_sensitive=False,
        env_file=".env",
        extra="allow",
        json_schema_extra={
            "example": {
                "GARDENER_LLM_PROVIDER": "openai",
                "GARDENER_OPENAI_API_KEY": "sk-...",
            }
        },
    )

    api_host: str = Field("0.0.0.0", description="Host for the HTTP/MCP server")
    api_port: int = Field(8600, description="Port for the HTTP control API")

    mcp_transport: Literal["stdio", "http"] = Field(
        "http", description="Transport for the MCP server when launched inside the container."
    )
    mcp_http_host: str = Field("0.0.0.0", description="Host for HTTP transport (if enabled)")
    mcp_http_port: int = Field(8601, description="Port for HTTP transport (if enabled)")

    hydro_api_base_url: str = Field(
        "http://127.0.0.1:8001", description="Base URL for the hydro backend FastAPI service"
    )
    hydro_api_timeout_seconds: float = Field(10.0, description="HTTP timeout for hydro backend requests")

    llm_provider: LLMProviderName = Field("mock", description="Primary LLM provider to use for agent runs")
    openai_api_key: Optional[str] = Field(default=None, description="API key for OpenAI-compatible models")
    openai_model: str = Field("gpt-4o-mini", description="Default OpenAI model to use")

    grok_api_key: Optional[str] = Field(default=None, description="API key for xAI Grok")
    grok_model: str = Field("grok-beta", description="Default Grok model variant")

    request_log_sample_rate: float = Field(1.0, ge=0.0, le=1.0, description="Fraction of requests to log verbosely")
    tool_refresh_interval_seconds: int = Field(60, ge=5, description="How often to refresh cached tool metadata")

    actuator_dry_run: bool = Field(
        False,
        description="When enabled, actuator commands are validated but not sent to hardware. Useful for tests.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""

    return Settings()


settings = get_settings()
