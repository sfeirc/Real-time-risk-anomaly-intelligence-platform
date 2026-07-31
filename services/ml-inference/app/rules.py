from __future__ import annotations

from pathlib import Path

import yaml

from .schemas import Action, Severity


class RulesEngine:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._config: dict = {}
        self.reload()

    def reload(self) -> None:
        self._config = yaml.safe_load(self._path.read_text())

    def evaluate(self, domain: str, anomaly_score: float) -> tuple[Severity | None, Action | None]:
        domain_config = self._config.get("domains", {}).get(domain, self._config["default"])
        for tier in domain_config["thresholds"]:
            if anomaly_score >= tier["min_score"]:
                return tier["severity"], tier["action"]
        return None, None
