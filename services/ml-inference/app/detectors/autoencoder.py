"""Small PyTorch autoencoder, retrained periodically on the same rolling
buffer as the Isolation Forest (see isolation_forest.py for why "online"
means periodic batch refit here, not per-sample updates).

Isolation Forest and the autoencoder are deliberately redundant in *purpose*
(both score "how unlike recent normal traffic is this window") but different
in *shape*: Isolation Forest partitions on individual feature thresholds and
is strong on features that are anomalous in isolation, while a reconstruction
error is strong on anomalous *feature correlations* — e.g. high volume with
normal-looking volatility, which no single feature threshold would flag but
which the autoencoder can't reconstruct because it never saw that
volume/volatility combination during training.
"""

from __future__ import annotations

from collections import deque

import numpy as np
import torch
from torch import nn


class _AE(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, latent_dim: int) -> None:
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, latent_dim),
        )
        self.decoder = nn.Sequential(
            nn.Linear(latent_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, input_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.decoder(self.encoder(x))


class RollingAutoencoder:
    def __init__(
        self,
        buffer_size: int,
        min_buffer: int,
        retrain_every: int,
        hidden_dim: int,
        latent_dim: int,
        epochs: int,
        lr: float,
    ) -> None:
        self._buffer: deque[list[float]] = deque(maxlen=buffer_size)
        self._min_buffer = min_buffer
        self._retrain_every = retrain_every
        self._hidden_dim = hidden_dim
        self._latent_dim = latent_dim
        self._epochs = epochs
        self._lr = lr
        self._since_retrain = 0

        self._model: _AE | None = None
        self._mean: np.ndarray | None = None
        self._std: np.ndarray | None = None
        self._err_mean = 0.0
        self._err_std = 1.0

    @property
    def ready(self) -> bool:
        return self._model is not None

    def observe(self, vector: list[float]) -> None:
        self._buffer.append(vector)
        self._since_retrain += 1
        if len(self._buffer) >= self._min_buffer and (self._model is None or self._since_retrain >= self._retrain_every):
            self._retrain()

    def _retrain(self) -> None:
        data = np.asarray(self._buffer, dtype=np.float64)
        mean = data.mean(axis=0)
        std = data.std(axis=0)
        std[std < 1e-9] = 1.0
        normalized = (data - mean) / std

        torch.manual_seed(42)
        model = _AE(normalized.shape[1], self._hidden_dim, self._latent_dim)
        optimizer = torch.optim.Adam(model.parameters(), lr=self._lr)
        loss_fn = nn.MSELoss()

        x = torch.tensor(normalized, dtype=torch.float32)
        model.train()
        for _ in range(self._epochs):
            optimizer.zero_grad()
            recon = model(x)
            loss = loss_fn(recon, x)
            loss.backward()
            optimizer.step()

        model.eval()
        with torch.no_grad():
            recon = model(x)
            per_sample_err = ((recon - x) ** 2).mean(dim=1).numpy()

        self._model = model
        self._mean = mean
        self._std = std
        self._err_mean = float(per_sample_err.mean())
        self._err_std = max(float(per_sample_err.std()), 1e-6)
        self._since_retrain = 0

    def score(self, vector: list[float]) -> float:
        """0..1, higher = more anomalous. `0.0` before the first fit."""
        if self._model is None or self._mean is None or self._std is None:
            return 0.0
        x_np = (np.asarray(vector, dtype=np.float64) - self._mean) / self._std
        x = torch.tensor(x_np, dtype=torch.float32).unsqueeze(0)
        with torch.no_grad():
            recon = self._model(x)
            err = float(((recon - x) ** 2).mean().item())
        z = (err - self._err_mean) / self._err_std
        return float(1.0 / (1.0 + np.exp(-1.5 * z)))
