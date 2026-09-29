"""Payment provider registry. PayPal Sandbox is the only real provider."""

from app.payments.base import PaymentProvider, ProviderError
from app.payments.paypal import PayPalProvider

_provider: PayPalProvider | None = None


def get_provider() -> PayPalProvider:
    global _provider
    if _provider is None:
        _provider = PayPalProvider()
    return _provider


def set_provider(provider: PayPalProvider | None) -> None:
    """Swap the provider (tests inject a PayPalProvider backed by a mocked transport)."""
    global _provider
    _provider = provider


__all__ = ["PaymentProvider", "PayPalProvider", "ProviderError", "get_provider", "set_provider"]
