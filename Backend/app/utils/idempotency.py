from app.core.enums import ActionType

_PREFIX = {
    ActionType.RECONCILE_LEDGER: "reconcile",
    ActionType.RETRY_WEBHOOK: "retry_webhook",
    ActionType.REFUND: "refund",
    ActionType.MARK_PAYMENT_FAILED: "mark_failed",
    ActionType.ESCALATE: "escalate",
}


def make_idempotency_key(action_type: str, transaction_id: str) -> str:
    """Application-level key, e.g. ``payflow:reconcile:TXN92831``.

    One financial operation per (action, transaction): a second request with the
    same key hits the unique constraint and receives the original result.
    """
    try:
        prefix = _PREFIX[ActionType(action_type)]
    except ValueError:
        prefix = action_type.lower()
    return f"payflow:{prefix}:{transaction_id}"


def paypal_order_key(transaction_id: str) -> str:
    return f"paypal:order:{transaction_id}"


def paypal_capture_key(order_id: str) -> str:
    return f"paypal:capture:{order_id}"


def paypal_refund_key(capture_id: str) -> str:
    return f"paypal:refund:{capture_id}"
