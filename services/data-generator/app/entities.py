"""Static universe of simulated entities and their baseline dynamics.

Parameters are hand-tuned to look plausible (annualized crypto vol in the
40-90% range, card-present decline rates around 2-4%) rather than calibrated
to any real feed — this is a synthetic benchmark, not a market data replay.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MarketSymbolSpec:
    symbol: str
    base_price: float
    annual_vol: float          # baseline annualized volatility of log returns
    base_spread_bps: float     # baseline bid/ask spread in basis points
    weight: float               # relative share of market event volume


@dataclass
class MerchantSpec:
    merchant_id: str
    category: str
    home_country: str
    mean_amount: float
    amount_sigma: float         # log-normal sigma of amount distribution
    base_decline_rate: float
    channel_mix: dict[str, float]
    customer_pool_size: int
    weight: float               # relative share of payments event volume


MARKET_SYMBOLS: list[MarketSymbolSpec] = [
    MarketSymbolSpec("BTC-USD", 65_000.0, 0.55, 1.5, 0.32),
    MarketSymbolSpec("ETH-USD", 3_400.0, 0.65, 2.0, 0.24),
    MarketSymbolSpec("SOL-USD", 165.0, 0.85, 4.0, 0.18),
    MarketSymbolSpec("XRP-USD", 0.62, 0.75, 5.0, 0.14),
    MarketSymbolSpec("DOGE-USD", 0.14, 0.95, 7.0, 0.12),
]

MERCHANTS: list[MerchantSpec] = [
    MerchantSpec(
        "merch_grocery_01", "grocery", "US", 38.0, 0.55, 0.015,
        {"card_present": 0.85, "card_not_present": 0.15, "wire": 0.0, "ach": 0.0},
        50_000, 0.22,
    ),
    MerchantSpec(
        "merch_electronics_02", "electronics", "US", 420.0, 0.75, 0.03,
        {"card_present": 0.35, "card_not_present": 0.60, "wire": 0.0, "ach": 0.05},
        18_000, 0.18,
    ),
    MerchantSpec(
        "merch_travel_03", "travel", "GB", 780.0, 0.9, 0.04,
        {"card_present": 0.10, "card_not_present": 0.80, "wire": 0.05, "ach": 0.05},
        9_000, 0.14,
    ),
    MerchantSpec(
        "merch_subscription_04", "subscription", "US", 16.0, 0.35, 0.02,
        {"card_present": 0.0, "card_not_present": 0.97, "wire": 0.0, "ach": 0.03},
        120_000, 0.16,
    ),
    MerchantSpec(
        "merch_luxury_05", "luxury_retail", "FR", 2_100.0, 1.0, 0.035,
        {"card_present": 0.55, "card_not_present": 0.40, "wire": 0.03, "ach": 0.02},
        4_000, 0.08,
    ),
    MerchantSpec(
        "merch_marketplace_06", "marketplace", "DE", 95.0, 0.8, 0.045,
        {"card_present": 0.05, "card_not_present": 0.90, "wire": 0.0, "ach": 0.05},
        60_000, 0.12,
    ),
    MerchantSpec(
        "merch_saas_07", "b2b_saas", "US", 260.0, 0.6, 0.01,
        {"card_present": 0.0, "card_not_present": 0.55, "wire": 0.30, "ach": 0.15},
        6_000, 0.06,
    ),
    MerchantSpec(
        "merch_gaming_08", "gaming", "JP", 28.0, 0.65, 0.06,
        {"card_present": 0.0, "card_not_present": 0.95, "wire": 0.0, "ach": 0.05},
        80_000, 0.04,
    ),
]

FOREIGN_COUNTRIES = ["US", "GB", "FR", "DE", "JP", "BR", "NG", "SG", "AE", "CA"]
