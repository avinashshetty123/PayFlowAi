import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ACTIVE_INCIDENT_STATUSES, AuditEvent, FailureSource, IncidentStatus, IncidentType, Provider
from app.core.errors import NotFoundError
from app.models import Action, Incident, Investigation, Payment
from app.models.base import utcnow
from app.services.audit_service import AuditService
from app.services.reconciliation_service import ReconciliationResult
from app.utils.serialization import money

DETECTOR_ACTOR = "reconciliation-engine"


class IncidentService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.audit = AuditService(session)

    async def get(self, incident_id: uuid.UUID) -> Incident:
        incident = await self.session.get(Incident, incident_id)
        if incident is None:
            raise NotFoundError(f"Incident {incident_id} not found")
        return incident

    async def reload(self, incident_id: uuid.UUID) -> Incident:
        incident = await self.session.get(Incident, incident_id, populate_existing=True)
        if incident is None:
            raise NotFoundError(f"Incident {incident_id} not found")
        return incident

    async def _next_number(self) -> str:
        current = await self.session.scalar(
            text(
                "SELECT COALESCE(MAX(CAST(SUBSTRING(incident_number FROM 5) AS INTEGER)), 1000) "
                "FROM incidents WHERE incident_number ~ '^INC-[0-9]+$'"
            )
        )
        return f"INC-{int(current) + 1}"

    async def create_from_reconciliation(self, payment: Payment, result: ReconciliationResult) -> tuple[Incident, bool]:
        """Open an incident for a detected mismatch (deduplicated per payment + type)."""
        assert result.incident_type is not None
        existing = await self.session.scalar(
            select(Incident).where(
                Incident.payment_id == payment.id,
                Incident.type == str(result.incident_type),
                Incident.status.in_([str(s) for s in ACTIVE_INCIDENT_STATUSES]),
            )
        )
        if existing:
            return existing, False

        now = utcnow()
        incident = Incident(
            incident_number=await self._next_number(),
            payment_id=payment.id,
            type=str(result.incident_type),
            severity=str(result.severity),
            status=str(IncidentStatus.OPEN),
            requires_human=False,
            initial_snapshot=result.snapshot,
            detection_findings=result.findings,
            created_at=now,
        )
        self.session.add(incident)
        await self.session.flush()
        await self._attribute(incident, payment)

        await self.audit.record(
            transaction_id=payment.transaction_id,
            incident_id=incident.id,
            event=AuditEvent.MISMATCH_DETECTED,
            actor=DETECTOR_ACTOR,
            reason=result.summary,
            evidence={"snapshot": result.snapshot, "findings": result.findings,
                      "amount": money(payment.amount, payment.currency), "provider": payment.provider,
                      "provider_status": payment.provider_status},
            result={"incident_type": str(result.incident_type), "severity": str(result.severity),
                    "failure_source": incident.failure_source, "injected_scenario": incident.injected_scenario},
            at=now,
        )
        await self.audit.record(
            transaction_id=payment.transaction_id,
            incident_id=incident.id,
            event=AuditEvent.INCIDENT_CREATED,
            actor=DETECTOR_ACTOR,
            reason=f"{incident.incident_number} opened for {result.incident_type}",
            result={"incident_number": incident.incident_number, "status": incident.status},
            at=now,
        )
        from app.services.audit_service import SUPPRESS_EVENTS

        if not self.session.info.get(SUPPRESS_EVENTS):
            from app.notifications.service import raise_alert

            await raise_alert(self.session, incident=incident, payment=payment, kind="INCIDENT_OPENED",
                              detail=result.summary)
        return incident, True

    async def _attribute(self, incident: Incident, payment: Payment) -> None:
        """Label where the failure came from. Injected demo failures are never blamed on PayPal."""
        from app.failure_injection.service import FailureInjectionService

        injection = await FailureInjectionService(self.session).attribute(payment, incident.type, incident.id)
        if injection is not None:
            incident.failure_source = str(FailureSource.PAYFLOW_INFRASTRUCTURE_FAILURE)
            incident.injected_scenario = injection.scenario
        elif payment.provider == Provider.PAYPAL_SANDBOX and incident.type == IncidentType.PROVIDER_DECLINED:
            incident.failure_source = str(FailureSource.PAYPAL_PROVIDER_FAILURE)
        elif payment.provider == Provider.PAYFLOW_HISTORICAL:
            incident.failure_source = str(FailureSource.HISTORICAL)
        elif payment.provider == Provider.PAYPAL_SANDBOX and incident.type != IncidentType.REFUND_REQUESTED:
            # A real (non-injected) PayFlow-side problem, e.g. webhook never reached the tunnel.
            incident.failure_source = str(FailureSource.PAYFLOW_INFRASTRUCTURE_FAILURE)

    # ---- read models -----------------------------------------------------------------

    async def list_with_payments(
        self, *, limit: int = 50, offset: int = 0, status: str | None = None, active: bool | None = None
    ) -> tuple[list[tuple[Incident, Payment]], int]:
        stmt = select(Incident, Payment).join(Payment, Payment.id == Incident.payment_id)
        count_stmt = select(func.count()).select_from(Incident)
        if status:
            stmt = stmt.where(Incident.status == status)
            count_stmt = count_stmt.where(Incident.status == status)
        if active is True:
            cond = Incident.status.in_([str(s) for s in ACTIVE_INCIDENT_STATUSES])
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)
        total = await self.session.scalar(count_stmt) or 0
        rows = await self.session.execute(stmt.order_by(Incident.created_at.desc()).limit(limit).offset(offset))
        return [(i, p) for i, p in rows.all()], total

    async def latest_actions(self, incident_ids: list[uuid.UUID]) -> dict[uuid.UUID, Action]:
        if not incident_ids:
            return {}
        rows = await self.session.scalars(
            select(Action).where(Action.incident_id.in_(incident_ids)).order_by(Action.created_at)
        )
        latest: dict[uuid.UUID, Action] = {}
        for action in rows:
            latest[action.incident_id] = action
        return latest

    async def actions(self, incident_id: uuid.UUID) -> list[Action]:
        rows = await self.session.scalars(
            select(Action).where(Action.incident_id == incident_id).order_by(Action.created_at)
        )
        return list(rows)

    async def latest_investigation(self, incident_id: uuid.UUID) -> Investigation | None:
        return await self.session.scalar(
            select(Investigation)
            .where(Investigation.incident_id == incident_id)
            .order_by(Investigation.created_at.desc())
            .limit(1)
        )

    async def investigated_ids(self, incident_ids: list[uuid.UUID]) -> dict[uuid.UUID, Investigation]:
        if not incident_ids:
            return {}
        rows = await self.session.scalars(
            select(Investigation).where(Investigation.incident_id.in_(incident_ids)).order_by(Investigation.created_at)
        )
        return {inv.incident_id: inv for inv in rows}
