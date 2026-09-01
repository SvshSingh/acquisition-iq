"""Application settings, loaded from environment with sane local defaults."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AcquisitionIQ"
    environment: str = "development"
    debug: bool = True

    # Storage — Supabase Postgres in production, local container in development.
    database_url: str = "postgresql+asyncpg://aiq:aiq@localhost:5432/aiq"

    # Cache — Upstash Redis in production. Empty string disables Redis and the
    # cache transparently falls back to the Postgres-backed cache table.
    redis_url: str = ""
    http_cache_ttl_seconds: int = 86_400  # 24h — company sites change slowly
    score_cache_ttl_seconds: int = 604_800  # 7d — keyed by content hash anyway

    # Scraping politeness. These are deliberately conservative: we are a guest
    # on every host we touch.
    max_concurrent_requests: int = 16
    max_concurrent_per_domain: int = 2
    request_timeout_seconds: float = 15.0
    max_retries: int = 3
    respect_robots_txt: bool = True
    circuit_breaker_threshold: int = 5
    circuit_breaker_cooldown_seconds: int = 300

    contact_email: str = "hello@example.com"  # advertised in our User-Agent

    overpass_url: str = "https://overpass-api.de/api/interpreter"

    # Optional LLM pass for qualitative signal extraction. Absent key => the
    # pipeline degrades to heuristic extraction. Scores never depend on it.
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-4-5"

    seed_dataset_path: str = "../data/seed_leads.json"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
