"""Historical PayFlow incident knowledge base + similarity search (pgvector, with fallbacks)."""

import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HistoricalIncident
from app.rag.embedding_service import cosine, embed, keyword_similarity, to_pgvector
from app.schemas.investigation import HistoricalMatch

logger = logging.getLogger(__name__)

TYPE_MATCH_WEIGHT = 0.3

HISTORICAL_INCIDENTS: list[dict] = [
    # ---- ledger ----
    {
        "incident_type": "LEDGER_MISMATCH", "title": "Ledger synchronization failure", "severity": "HIGH",
        "symptoms": ["gateway SUCCESS", "bank SETTLED", "merchant FAILED", "ledger FAILED", "webhook RECEIVED", "ledger write lock timeout"],
        "root_cause": "Internal ledger synchronization failure: ledger write aborted after lock wait timeout while order callback failed.",
        "resolution": "Reconcile ledger: post the missing capture entry from the bank settlement record and mark the order paid.",
    },
    {
        "incident_type": "LEDGER_MISMATCH", "title": "Ledger consumer lag after deploy", "severity": "MEDIUM",
        "symptoms": ["gateway SUCCESS", "bank SETTLED", "merchant SUCCESS", "ledger PENDING", "webhook RECEIVED", "consumer lag"],
        "root_cause": "Ledger event consumer lagged after a deploy; capture events queued but not applied.",
        "resolution": "Reconcile ledger from settlement data; replay the consumer offset.",
    },
    {
        "incident_type": "LEDGER_MISMATCH", "title": "Order service outage during fulfilment callback", "severity": "HIGH",
        "symptoms": ["gateway SUCCESS", "bank SETTLED", "merchant FAILED", "order service 503"],
        "root_cause": "Order service returned 503 during the payment callback, so neither order nor ledger were updated.",
        "resolution": "Reconcile ledger and re-mark the merchant order as paid.",
    },
    {
        "incident_type": "LEDGER_MISMATCH", "title": "Ledger database failover dropped writes", "severity": "HIGH",
        "symptoms": ["gateway SUCCESS", "bank SETTLED", "ledger FAILED", "database failover"],
        "root_cause": "Primary ledger database failover dropped in-flight transactions.",
        "resolution": "Reconcile affected transactions against bank settlement files.",
    },
    # ---- webhooks ----
    {
        "incident_type": "WEBHOOK_DELAY", "title": "Webhook delay from merchant endpoint 504", "severity": "LOW",
        "symptoms": ["gateway SUCCESS", "bank SETTLED", "merchant PENDING", "ledger PENDING", "webhook DELAYED", "HTTP 504"],
        "root_cause": "Merchant webhook endpoint timed out (HTTP 504); gateway retry queue backed up.",
        "resolution": "Retry webhook delivery; order and ledger update on receipt.",
    },
    {
        "incident_type": "WEBHOOK_DELAY", "title": "Gateway webhook queue backlog", "severity": "LOW",
        "symptoms": ["gateway SUCCESS", "webhook DELAYED", "merchant PENDING", "gateway incident"],
        "root_cause": "Gateway-side webhook dispatcher backlog during peak traffic.",
        "resolution": "Retry webhook from PayFlow using gateway status API as source of truth.",
    },
    {
        "incident_type": "WEBHOOK_LOST", "title": "Webhook lost after TLS certificate rotation", "severity": "MEDIUM",
        "symptoms": ["gateway SUCCESS", "bank SETTLED", "merchant PENDING", "ledger PENDING", "webhook NOT_RECEIVED", "TLS handshake failure"],
        "root_cause": "Webhook deliveries failed TLS handshake after certificate rotation and exhausted gateway retries.",
        "resolution": "Retry webhook delivery for affected payments; fix certificate chain.",
    },
    {
        "incident_type": "WEBHOOK_LOST", "title": "Webhook signature secret mismatch", "severity": "MEDIUM",
        "symptoms": ["gateway SUCCESS", "webhook NOT_RECEIVED", "signature verification failed"],
        "root_cause": "Webhook secret rotated on gateway but not in PayFlow; deliveries rejected.",
        "resolution": "Sync secret, then retry webhooks for affected payments.",
    },
    # ---- duplicates ----
    {
        "incident_type": "DUPLICATE_PAYMENT", "title": "Duplicate payment after client retry", "severity": "HIGH",
        "symptoms": ["gateway SUCCESS", "bank SETTLED", "merchant DUPLICATE", "ledger SUCCESS", "webhook RECEIVED", "client timeout retry"],
        "root_cause": "Customer retried checkout after a client-side timeout; the second capture also succeeded.",
        "resolution": "Refund the duplicate capture; keep the original payment.",
    },
    {
        "incident_type": "DUPLICATE_PAYMENT", "title": "Missing idempotency key on checkout API", "severity": "HIGH",
        "symptoms": ["gateway SUCCESS", "merchant DUPLICATE", "double capture", "idempotency key missing"],
        "root_cause": "Mobile client omitted idempotency key, allowing two captures for one order.",
        "resolution": "Refund duplicate; enforce idempotency keys at the API gateway.",
    },
    # ---- settlement ----
    {
        "incident_type": "SETTLEMENT_MISMATCH", "title": "Settlement mismatch due to unexpected MDR deduction", "severity": "HIGH",
        "symptoms": ["gateway SUCCESS", "bank SETTLED", "merchant SUCCESS", "ledger SUCCESS", "webhook RECEIVED", "settlement amount lower"],
        "root_cause": "Acquirer deducted an MDR fee not configured in the pricing contract.",
        "resolution": "Escalate to finance for acquirer dispute; do not auto-adjust ledger.",
    },
    {
        "incident_type": "SETTLEMENT_MISMATCH", "title": "Partial settlement from acquirer", "severity": "HIGH",
        "symptoms": ["gateway SUCCESS", "bank SETTLED", "partial settlement", "amount difference"],
        "root_cause": "Acquirer split settlement across two batches; second batch pending.",
        "resolution": "Escalate for manual settlement tracking.",
    },
    # ---- refunds ----
    {
        "incident_type": "REFUND_FAILURE", "title": "Refund failure during issuer bank outage", "severity": "MEDIUM",
        "symptoms": ["gateway REFUND_FAILED", "bank SETTLED", "merchant REFUND_REQUESTED", "ledger PENDING", "webhook RECEIVED", "issuer unavailable"],
        "root_cause": "Refund API call failed because the issuer bank was temporarily unavailable.",
        "resolution": "Retry refund once issuer recovers; high-value refunds require approval.",
    },
    {
        "incident_type": "REFUND_FAILURE", "title": "Refund rejected: insufficient merchant balance", "severity": "HIGH",
        "symptoms": ["gateway REFUND_FAILED", "merchant REFUND_REQUESTED", "insufficient balance"],
        "root_cause": "Merchant settlement balance insufficient for refund at time of request.",
        "resolution": "Retry refund after balance top-up with human approval.",
    },
    # ---- gateway ----
    {
        "incident_type": "GATEWAY_TIMEOUT", "title": "Gateway timeout with no bank debit", "severity": "MEDIUM",
        "symptoms": ["gateway TIMEOUT", "bank NOT_FOUND", "merchant PENDING", "ledger PENDING", "webhook NOT_RECEIVED", "acquirer timeout"],
        "root_cause": "Acquirer did not respond within 30s; no debit reached the bank.",
        "resolution": "Mark payment failed after confirming no bank debit; customer may retry.",
    },
    {
        "incident_type": "GATEWAY_TIMEOUT", "title": "Gateway timeout during acquirer maintenance", "severity": "MEDIUM",
        "symptoms": ["gateway TIMEOUT", "bank NOT_FOUND", "scheduled maintenance"],
        "root_cause": "Acquirer maintenance window caused authorisation timeouts.",
        "resolution": "Mark payments failed; route traffic to secondary acquirer.",
    },
    {
        "incident_type": "UNKNOWN_STATE", "title": "Gateway status unknown with bank settlement", "severity": "CRITICAL",
        "symptoms": ["gateway UNKNOWN", "bank SETTLED", "merchant PENDING", "ledger PENDING", "webhook NOT_RECEIVED", "status API inconsistent"],
        "root_cause": "Gateway status API returned inconsistent state while funds settled at the bank.",
        "resolution": "Escalate to payments on-call; confirm with gateway support before any ledger change.",
    },
    {
        "incident_type": "UNKNOWN_STATE", "title": "Gateway partial outage returns stale status", "severity": "HIGH",
        "symptoms": ["gateway UNKNOWN", "stale status", "gateway incident"],
        "root_cause": "Gateway cache served stale payment status during partial outage.",
        "resolution": "Escalate; wait for gateway recovery before reconciling.",
    },
    # ---- bank / merchant ----
    {
        "incident_type": "BANK_TIMEOUT", "title": "Bank timeout on settlement file ingestion", "severity": "MEDIUM",
        "symptoms": ["gateway SUCCESS", "bank PENDING", "settlement file delayed"],
        "root_cause": "Bank SFTP settlement file delivered late.",
        "resolution": "Re-run reconciliation after settlement file arrives.",
    },
    {
        "incident_type": "MERCHANT_FAILURE", "title": "Merchant order service rejected callback", "severity": "MEDIUM",
        "symptoms": ["gateway SUCCESS", "merchant FAILED", "ledger SUCCESS", "validation error"],
        "root_cause": "Merchant order service rejected callback due to schema validation error.",
        "resolution": "Re-send order confirmation after merchant fix.",
    },
    {
        "incident_type": "MERCHANT_FAILURE", "title": "Merchant inventory lock expired", "severity": "LOW",
        "symptoms": ["gateway SUCCESS", "merchant FAILED", "inventory lock expired"],
        "root_cause": "Inventory reservation expired before payment confirmation.",
        "resolution": "Re-reserve inventory or refund customer.",
    },
    {
        "incident_type": "LEDGER_MISMATCH", "title": "Ledger rounding error on multi-currency capture", "severity": "LOW",
        "symptoms": ["gateway SUCCESS", "bank SETTLED", "ledger amount mismatch", "rounding"],
        "root_cause": "Ledger stored FX-rounded amount instead of settled INR amount.",
        "resolution": "Reconcile ledger to settled INR amount.",
    },
    {
        "incident_type": "WEBHOOK_LOST", "title": "Duplicate webhook redelivery after listener timeout", "severity": "LOW",
        "symptoms": ["gateway SUCCESS", "bank SETTLED", "merchant SUCCESS", "ledger SUCCESS", "webhook RECEIVED", "duplicate event id"],
        "root_cause": "Provider retried a webhook after the listener responded slowly; same event id delivered twice.",
        "resolution": "Deduplicate by provider event id; no second financial action.",
    },
    {
        "incident_type": "PROVIDER_DECLINED", "title": "Provider declined capture: instrument declined", "severity": "MEDIUM",
        "symptoms": ["gateway FAILED", "bank PENDING", "merchant PENDING", "ledger PENDING", "webhook AWAITING", "INSTRUMENT_DECLINED"],
        "root_cause": "Payment provider declined the funding instrument at capture time; merchant order left open.",
        "resolution": "Mark payment failed and release the order; buyer may retry with another instrument.",
    },
    {
        "incident_type": "REFUND_REQUESTED", "title": "Merchant cancellation requiring refund", "severity": "MEDIUM",
        "symptoms": ["gateway SUCCESS", "bank SETTLED", "merchant REFUND_REQUESTED", "ledger SUCCESS", "webhook RECEIVED"],
        "root_cause": "Customer cancelled a completed order; captured funds must be returned.",
        "resolution": "Refund via provider API; amounts above the auto-approve limit need human approval.",
    },
]


SYSTEM_ORDER = ("gateway", "bank", "merchant", "ledger", "webhook")

# Historical PayFlow incident metadata (not PayPal data): reference, how it was resolved, how fast.
_HUMAN_TYPES = {"SETTLEMENT_MISMATCH", "UNKNOWN_STATE", "MERCHANT_FAILURE"}
for _index, _item in enumerate(HISTORICAL_INCIDENTS):
    _item.setdefault("reference", f"INC-{892 - _index * 13}")
    _human = _item["incident_type"] in _HUMAN_TYPES or "insufficient" in _item["title"].lower()
    _item.setdefault("resolution_mode", "HUMAN" if _human else "AUTOMATIC")
    _item.setdefault("resolved_in_seconds", (1800 + _index * 240) if _human else (9 + (_index * 7) % 40))


def document_text(item: dict) -> str:
    """Symptom signature: incident type + observed system states (title/root cause are shown, not embedded)."""
    return " ".join([item["incident_type"], *item["symptoms"]])


def query_text(incident_type: str, snapshot: dict[str, str]) -> str:
    states = [f"{system} {snapshot[system]}" for system in SYSTEM_ORDER if system in snapshot]
    return " ".join([incident_type, *states])


class HistoricalIncidentService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def pgvector_enabled(self) -> bool:
        found = await self.session.scalar(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'historical_incidents' AND column_name = 'embedding'"
            )
        )
        return bool(found)

    async def seed(self) -> int:
        use_vector = await self.pgvector_enabled()
        for item in HISTORICAL_INCIDENTS:
            vector = embed(document_text(item))
            row = HistoricalIncident(**item, embedding_json=vector)
            self.session.add(row)
            await self.session.flush()
            if use_vector:
                await self.session.execute(
                    text("UPDATE historical_incidents SET embedding = CAST(:v AS vector) WHERE id = :id"),
                    {"v": to_pgvector(vector), "id": row.id},
                )
        return len(HISTORICAL_INCIDENTS)

    async def search(self, *, incident_type: str, snapshot: dict[str, str], limit: int = 3) -> list[HistoricalMatch]:
        query = query_text(incident_type, snapshot)
        qvec = embed(query)
        try:
            if await self.pgvector_enabled():
                return await self._search_pgvector(qvec, incident_type, limit)
        except Exception as exc:  # noqa: BLE001 - never let RAG break the pipeline
            logger.warning("pgvector search failed (%s); falling back", exc)
        return await self._search_python(query, qvec, incident_type, limit)

    async def _search_pgvector(self, qvec: list[float], incident_type: str, limit: int) -> list[HistoricalMatch]:
        async with self.session.begin_nested():
            rows = (
                await self.session.execute(
                    text(
                        "SELECT id, title, incident_type, root_cause, resolution, severity, reference, "
                        "resolution_mode, resolved_in_seconds, "
                        "1 - (embedding <=> CAST(:q AS vector)) AS similarity "
                        "FROM historical_incidents WHERE embedding IS NOT NULL "
                        "ORDER BY embedding <=> CAST(:q AS vector) LIMIT 12"
                    ),
                    {"q": to_pgvector(qvec)},
                )
            ).mappings().all()
        matches = [
            self._to_match(row, float(row["similarity"]), incident_type, "pgvector") for row in rows
        ]
        return sorted(matches, key=lambda m: m.similarity, reverse=True)[:limit]

    async def _search_python(
        self, query: str, qvec: list[float], incident_type: str, limit: int
    ) -> list[HistoricalMatch]:
        rows = list(await self.session.scalars(select(HistoricalIncident)))
        matches = []
        for row in rows:
            if row.embedding_json:
                sim, mode = cosine(qvec, row.embedding_json), "python-cosine"
            else:
                sim, mode = keyword_similarity(query, document_text(self._as_item(row))), "keyword"
            matches.append(self._to_match(self._as_mapping(row), sim, incident_type, mode))
        return sorted(matches, key=lambda m: m.similarity, reverse=True)[:limit]

    @staticmethod
    def _as_item(row: HistoricalIncident) -> dict:
        return {"incident_type": row.incident_type, "title": row.title, "symptoms": row.symptoms, "root_cause": row.root_cause}

    @staticmethod
    def _as_mapping(row: HistoricalIncident) -> dict:
        return {
            "id": row.id, "title": row.title, "incident_type": row.incident_type,
            "root_cause": row.root_cause, "resolution": row.resolution, "severity": row.severity,
            "reference": row.reference, "resolution_mode": row.resolution_mode,
            "resolved_in_seconds": row.resolved_in_seconds,
        }

    @staticmethod
    def _to_match(row, semantic: float, incident_type: str, mode: str) -> HistoricalMatch:
        # Hybrid score: semantic similarity + metadata (incident type) agreement.
        type_match = 1.0 if row["incident_type"] == incident_type else 0.0
        score = (1 - TYPE_MATCH_WEIGHT) * max(semantic, 0.0) + TYPE_MATCH_WEIGHT * type_match
        return HistoricalMatch(
            id=str(row["id"]),
            title=row["title"],
            incident_type=row["incident_type"],
            similarity=round(min(score, 0.99), 4),
            root_cause=row["root_cause"],
            resolution=row["resolution"],
            severity=row["severity"],
            retrieval=mode,
            reference=row.get("reference"),
            resolution_mode=row.get("resolution_mode"),
            resolved_in_seconds=row.get("resolved_in_seconds"),
        )
