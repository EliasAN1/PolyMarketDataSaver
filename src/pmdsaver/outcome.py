"""Binary UP/DOWN outcome from a close price vs price-to-beat."""

from __future__ import annotations


def binary_outcome(final_price: str | None, ptb: str | None) -> str | None:
    if final_price is None or ptb is None:
        return None
    try:
        final = float(final_price)
        beat = float(ptb)
    except (TypeError, ValueError):
        return None
    # Polymarket: "resolves to Up if the Chainlink TWAP is greater than or equal
    # to the price at the beginning of the range. Otherwise Down."
    if final >= beat:
        return "up"
    return "down"
