from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATA_GENERATOR_", extra="ignore")

    ws_port: int = 8765
    events_per_sec: float = 200.0
    # Per entity, per scenario-check tick (1s) — NOT the same as "fraction of
    # windows anomalous". A spawn's *duty cycle* (steady-state P(entity has
    # an active scenario)) is approximately probability * mean_duration_s,
    # since a spawn only fires when the entity is currently idle. Scenario
    # durations run ~10-90s (mean ~35s, see scenarios.py), so 0.002 gives a
    # duty cycle of ~7% — anomalies as the exception, which is the point of
    # calling them anomalies; 0.02 (the first value tried here) gives ~70%
    # and makes precision/recall against a mostly-anomalous stream far less
    # meaningful than against a mostly-normal one.
    scenario_probability: float = 0.002
    seed: int | None = None

    # split of aggregate throughput between domains
    market_share: float = 0.6

    log_level: str = "info"


settings = Settings()
