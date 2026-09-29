import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AuditEvent
from app.failure_injection.scenarios import EXPLAINS, SCENARIOS, FailureScenario
from app.models import FailureInjection, Payment
from app.models.base import utcnow
from app.services.audit_service import AuditService

ACTOR = "failure-injection (demo)"
LABEL = "DEMO FAILURE INJECTION"


class InjectedFailure(Exception):
    """Raised by an injection point. Always an intentional PayFlow infrastructure failure."""

    scenario = "UNKNOWN"

    def __init__(self, message: str | None = None):
        super().__init__(message or f"{LABEL}: {self.scenario}")


class LedgerWriteFailure(InjectedFailure):
    scenario = "LEDGER_WRITE_FAILURE"


class MerchantUpdateFailure(InjectedFailure):
    scenario = "MERCHANT_UPDATE_FAILURE"


class VerificationTimeout(InjectedFailure):
    scenario = "VERIFICATION_TIMEOUT"


class FailureInjectionService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.audit = AuditService(session)

    async def arm(
        self, payment: Payment, scenario: FailureScenario | str, *, by: str = "demo-operator", metadata: dict | None = None
    ) -> FailureInjection:
        scenario = FailureScenario(scenario)
        injection = FailureInjection(
            payment_id=payment.id, scenario=str(scenario), enabled=True, injected_by=by, metadata_=metadata or {},
            created_at=utcnow(),
        )
        self.session.add(injection)
        await self.session.flush()
        await self.audit.record(
            transaction_id=payment.transaction_id, event=AuditEvent.FAILURE_INJECTED, actor=ACTOR,
            reason=f"{LABEL} armed: {scenario} ({SCENARIOS[scenario].description}) PayPal is not affected.",
            evidence={"scenario": str(scenario), "stage": SCENARIOS[scenario].stage, "armed_by": by},
            result={"state": "ARMED"},
        )
        return injection

    async def armed(self, payment: Payment, scenario: FailureScenario | str) -> FailureInjection | None:
        return await self.session.scalar(
            select(FailureInjection)
            .where(FailureInjection.payment_id == payment.id, FailureInjection.scenario == str(scenario),
                   FailureInjection.enabled.is_(True))
            .order_by(FailureInjection.created_at)
            .limit(1)
        )

    async def fire(self, injection: FailureInjection, payment: Payment, detail: str) -> None:
        injection.enabled = False
        injection.injected_at = utcnow()
        injection.metadata_ = {**(injection.metadata_ or {}), "detail": detail}
        await self.audit.record(
            transaction_id=payment.transaction_id, event=AuditEvent.FAILURE_INJECTED, actor=ACTOR,
            reason=f"{LABEL} triggered: {injection.scenario}. {detail}",
            evidence={"scenario": injection.scenario, "provider_status": payment.provider_status},
            result={"state": "TRIGGERED"},
        )

    async def take(self, payment: Payment, scenario: FailureScenario | str, detail: str) -> bool:
        """Fire the scenario if armed. Returns True when the failure should happen now."""
        injection = await self.armed(payment, scenario)
        if injection is None:
            return False
        await self.fire(injection, payment, detail)
        return True

    async def for_payment(self, payment_id: uuid.UUID) -> list[FailureInjection]:
        rows = await self.session.scalars(
            select(FailureInjection).where(FailureInjection.payment_id == payment_id).order_by(FailureInjection.created_at)
        )
        return list(rows)

    async def attribute(self, payment: Payment, incident_type: str, incident_id: uuid.UUID) -> FailureInjection | None:
        """Link the fired injection that explains a detected incident (if any)."""
        candidates = EXPLAINS.get(incident_type, set())
        for injection in reversed(await self.for_payment(payment.id)):
            meta = injection.metadata_ or {}
            if injection.injected_at and injection.scenario in candidates and not meta.get("incident_id"):
                injection.metadata_ = {**meta, "incident_id": str(incident_id)}
                return injection
        return None
