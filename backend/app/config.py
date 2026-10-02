"""Application settings, loaded from environment with sane local defaults."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AcquisitionIQ"
    environment: str = "development"
    debug: bool = True

    # Storage. Empty means "no database": the API serves the committed snapshot
    # and nothing tries to open a socket. Set it and Postgres becomes the serving
    # path — Supabase in production, the compose container locally.
    #
    # The default used to be a localhost URL, which made "no database configured"
    # indistinguishable from "database configured and down", and is why a refresh
    # once died with ConnectionRefusedError on a host that never had one. Absence
    # is now a state the code can see.
    #
    # Any Postgres URL form is accepted (`postgres://`, `postgresql://`, with or
    # without a driver) because that is what dashboards hand out; see
    # `app.db.session.connection_spec` for how it is normalised.
    database_url: str = ""

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

    # Documented public APIs, exempt from the robots.txt check by explicit
    # allowlist. This is a narrow, deliberate carve-out, not a bypass.
    #
    # overpass-api.de publishes `Disallow: /api/`, which exists to stop search
    # engines from spidering API URLs and executing expensive queries — the
    # crawler problem robots.txt was designed for. It is not a prohibition on
    # calling the API: that is what the endpoint is for, and the project governs
    # programmatic use through a separate published usage policy (bounded query
    # timeouts, modest request volume, cache your results) which we follow. Every
    # Overpass client library in the ecosystem takes the same reading.
    #
    # The exemption is per-prefix and covers only endpoints we deliberately add.
    # The website crawler — the part that actually spiders arbitrary third-party
    # sites, where robots.txt genuinely governs — is not exempt from anything.
    # This is stated openly in the README rather than left for a reader to find.
    robots_exempt_prefixes: str = (
        "https://overpass-api.de/api/,"
        "https://overpass.kumi.systems/api/,"
        "https://overpass.private.coffee/api/"
    )
    circuit_breaker_threshold: int = 5
    circuit_breaker_cooldown_seconds: int = 300

    # Advertised in our User-Agent so an operator who wants us to stop has
    # somewhere to say so. A URL rather than an email on purpose: it is the
    # convention every major crawler follows, and overpass-api.de returns 406 to
    # any User-Agent containing an email address (verified 2026-09-02).
    project_url: str = "https://github.com/SvshSingh/acquisition-iq"

    overpass_url: str = "https://overpass-api.de/api/interpreter"
    # Community-run mirrors, tried in order when the primary is unavailable.
    # Overpass instances go down for maintenance routinely and a seed collection
    # that dies because one host is busy is not much of a pipeline.
    overpass_mirrors: str = (
        "https://overpass.kumi.systems/api/interpreter,"
        "https://overpass.private.coffee/api/interpreter"
    )

    # No LLM settings here, and that is a product decision rather than an
    # omission. The scoring path is deterministic on purpose: a searcher
    # committing seven figures cannot audit a model's opinion, and the gap this
    # fills is precisely that lead tools already ship opaque AI scores. See the
    # README.

    seed_dataset_path: str = "../data/seed_glendale.json"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def robots_exempt_list(self) -> list[str]:
        return [p.strip() for p in self.robots_exempt_prefixes.split(",") if p.strip()]

    @property
    def overpass_endpoints(self) -> list[str]:
        """Primary endpoint first, then the mirrors."""
        mirrors = [m.strip() for m in self.overpass_mirrors.split(",") if m.strip()]
        return [self.overpass_url, *mirrors]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
