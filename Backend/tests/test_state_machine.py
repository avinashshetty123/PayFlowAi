import pytest

from app.core.enums import PaymentState as S
from app.core.errors import InvalidTransitionError
from app.models import Payment
from app.services.payment_service import PaymentStateService


def _payment(state: S) -> Payment:
    return Payment(overall_status=str(state))


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.CREATED, S.PROCESSING),
        (S.PROCESSING, S.SUCCESS),
        (S.PROCESSING, S.PENDING),
        (S.PENDING, S.UNKNOWN),
        (S.UNKNOWN, S.FAILED),
        (S.SUCCESS, S.SETTLED),
        (S.SETTLED, S.REFUND_PENDING),
        (S.REFUND_PENDING, S.REFUNDED),
    ],
)
def test_valid_transitions(current, target):
    payment = _payment(current)
    assert PaymentStateService.transition(payment, target) == (str(current), str(target))
    assert payment.overall_status == target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.CREATED, S.SETTLED),
        (S.FAILED, S.SUCCESS),
        (S.REFUNDED, S.SETTLED),
        (S.SETTLED, S.FAILED),
        (S.SUCCESS, S.CREATED),
        (S.PENDING, S.REFUNDED),
    ],
)
def test_invalid_transitions_are_rejected(current, target):
    payment = _payment(current)
    with pytest.raises(InvalidTransitionError):
        PaymentStateService.transition(payment, target)
    assert payment.overall_status == current  # unchanged


def test_unknown_state_names_are_rejected():
    assert not PaymentStateService.can_transition("CREATED", "TELEPORTED")
    assert not PaymentStateService.can_transition("BOGUS", "SUCCESS")


def test_full_happy_path():
    payment = _payment(S.CREATED)
    PaymentStateService.transition_path(payment, [S.PROCESSING, S.SUCCESS, S.SETTLED, S.REFUND_PENDING, S.REFUNDED])
    assert payment.overall_status == S.REFUNDED


def test_terminal_states_have_no_exits():
    for terminal in (S.FAILED, S.REFUNDED):
        for target in S:
            assert not PaymentStateService.can_transition(terminal, target)
