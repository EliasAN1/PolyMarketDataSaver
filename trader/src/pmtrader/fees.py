"""Polymarket CLOB crypto taker fee (BTC 5m).

fee = shares × feeRate × price × (1 − price), USDC rounded to 5 decimals.
https://docs.polymarket.com/trading/fees
"""

from __future__ import annotations

CRYPTO_TAKER_FEE_RATE = 0.07
FEE_DECIMALS = 5
MIN_FEE = 10 ** (-FEE_DECIMALS)


def taker_fee(
    shares: float,
    price: float,
    fee_rate: float = CRYPTO_TAKER_FEE_RATE,
) -> float:
    if shares <= 0 or fee_rate <= 0 or price <= 0 or price >= 1:
        return 0.0
    fee = round(shares * fee_rate * price * (1.0 - price), FEE_DECIMALS)
    return fee if fee >= MIN_FEE else 0.0
