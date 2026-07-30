from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATA_GENERATOR_", extra="ignore")

    ws_port: int = 8765
    events_per_sec: float = 200.0
    scenario_probability: float = 0.02  # per entity, per scenario-check tick (1s)
    seed: int | None = None

    # split of aggregate throughput between domains
    market_share: float = 0.6

    log_level: str = "info"


settings = Settings()
