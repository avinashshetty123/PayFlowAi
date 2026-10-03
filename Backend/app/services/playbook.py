"""Remediation playbook: which actions are legitimate for which incident type.

Single source of truth shared by the AI prompt (so the model is told the valid
options) and the policy engine (which rejects anything outside the matrix and
falls back to the playbook action). Prevents e.g. RETRY_WEBHOOK being executed
for a LEDGER_MISMATCH, a real bug seen in production.
"""

from app.core.enums import ActionType, IncidentType

A, I = ActionType, IncidentType

ALLOWED_ACTIONS: dict[str, tuple[ActionType, ...]] = {
    I.LEDGER_MISMATCH: (A.RECONCILE_LEDGER, A.ESCALATE),
    I.WEBHOOK_DELAY: (A.RETRY_WEBHOOK, A.ESCALATE),
    I.WEBHOOK_LOST: (A.RETRY_WEBHOOK, A.ESCALATE),
    I.SETTLEMENT_MISMATCH: (A.ESCALATE,),
    I.DUPLICATE_PAYMENT: (A.REFUND, A.ESCALATE),
    I.REFUND_FAILURE: (A.REFUND, A.ESCALATE),
    I.REFUND_REQUESTED: (A.REFUND, A.ESCALATE),
    I.GATEWAY_TIMEOUT: (A.MARK_PAYMENT_FAILED, A.ESCALATE),
    I.PROVIDER_DECLINED: (A.MARK_PAYMENT_FAILED, A.ESCALATE),
    I.UNKNOWN_STATE: (A.ESCALATE,),
}

# Deterministic first choice per incident type.
PLAYBOOK_ACTION: dict[str, ActionType] = {kind: actions[0] for kind, actions in ALLOWED_ACTIONS.items()}


def allowed_actions(incident_type: str) -> tuple[ActionType, ...]:
    return ALLOWED_ACTIONS.get(incident_type, (A.ESCALATE,))


def playbook_action(incident_type: str) -> ActionType:
    return PLAYBOOK_ACTION.get(incident_type, A.ESCALATE)


def is_compatible(incident_type: str, action: str) -> bool:
    return action in {a.value for a in allowed_actions(incident_type)}
