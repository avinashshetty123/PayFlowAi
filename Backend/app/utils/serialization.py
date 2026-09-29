from typing import Any

from fastapi.encoders import jsonable_encoder


def to_jsonable(value: Any) -> Any:
    """Make Decimals, datetimes, UUIDs and Pydantic models safe for JSONB columns."""
    return jsonable_encoder(value, custom_encoder={})


def inr(amount: Any) -> str:
    return f"₹{float(amount):,.2f}".replace(".00", "")


def money(amount: Any, currency: str | None = "INR") -> str:
    if amount is None:
        return "n/a"
    if currency == "USD":
        return f"${float(amount):,.2f}"
    if currency in (None, "INR"):
        return inr(amount)
    return f"{float(amount):,.2f} {currency}"
