"""Polymarket CLOB taker fees.

https://docs.polymarket.com/trading/fees

    fee = C × feeRate × p × (1 - p)

C is shares traded, p is fill price in [0, 1]. BTC 5m is crypto (feeRate = 0.07).
Fees are USDC, rounded to 5 decimal places. Below 0.00001 USDC they become zero.
Makers pay nothing. Settlement at 0 or 1 is free (the formula is already 0 there).
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


def shares_for_stake(stake: float, price: float) -> float:
    if price <= 0:
        raise ValueError("fill price must be positive")
    return stake / price
