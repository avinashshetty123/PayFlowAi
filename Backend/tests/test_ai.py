from decimal import Decimal

import pytest

from app.ai.groq_client import GroqClient, GroqUnavailableError
from app.ai.investigator import FALLBACK_MODEL, InvestigatorService
from app.core.enums import Scenario
from app.rag.embedding_service import cosine, embed
from app.rag.historical_service import HistoricalIncidentService
from app.schemas.investigation import AIInvestigationResult
from app.services.orchestrator import ingest_simulated_payment

VALID_GROQ_OUTPUT = {
    "incidentType": "LEDGER_MISMATCH",
    "rootCause": "Internal ledger synchronization failure",
    "confidence": 0.96,
    "recommendedAction": "RECONCILE_LEDGER",
    "risk": "LOW",
    "requiresHuman": False,
    "evidence": ["Gateway SUCCESS", "Bank SETTLED", "Ledger FAILED"],
    "summary": "Payment successfully settled but internal ledger was not updated.",
}


class FakeGroq(GroqClient):
    def __init__(self, response=None, error: Exception | None = None):
        super().__init__(api_key="test-key", model="fake-model")
        self.response, self.error = response, error

    async def chat_json(self, system: str, user: str) -> dict:
        assert "JSON" in system and "Evidence bundle" in user
        if self.error:
            raise self.error
        return self.response


async def _incident(db):
    result = await ingest_simulated_payment(db, amount=Decimal("4850"), scenario=Scenario.LEDGER_MISMATCH)
    return result.incident, result.payment


def test_schema_accepts_the_contract():
    parsed = AIInvestigationResult.model_validate(VALID_GROQ_OUTPUT)
    assert parsed.to_public()["recommendedAction"] == "RECONCILE_LEDGER"


@pytest.mark.parametrize(
    "bad",
    [
        {**VALID_GROQ_OUTPUT, "confidence": 1.7},
        {**VALID_GROQ_OUTPUT, "recommendedAction": "DROP_TABLE_LEDGER"},
        {k: v for k, v in VALID_GROQ_OUTPUT.items() if k != "rootCause"},
        {**VALID_GROQ_OUTPUT, "evidence": []},
    ],
)
def test_schema_rejects_invalid_output(bad):
    with pytest.raises(ValueError):
        AIInvestigationResult.model_validate(bad)


async def test_fallback_without_api_key(kb):
    incident, payment = await _incident(kb)
    outcome = await InvestigatorService(kb, client=GroqClient(api_key="")).investigate(incident, payment)
    assert outcome.used_fallback
    assert outcome.model == FALLBACK_MODEL
    assert outcome.result.recommended_action == "RECONCILE_LEDGER"
    assert outcome.fallback_reason == "GROQ_API_KEY not configured"


async def test_groq_output_is_used_when_valid(kb):
    incident, payment = await _incident(kb)
    outcome = await InvestigatorService(kb, client=FakeGroq(VALID_GROQ_OUTPUT)).investigate(incident, payment)
    assert not outcome.used_fallback
    assert outcome.model == "groq:fake-model"
    assert outcome.result.confidence == 0.96


async def test_invalid_groq_json_falls_back(kb):
    incident, payment = await _incident(kb)
    outcome = await InvestigatorService(kb, client=FakeGroq({"answer": "reconcile it"})).investigate(incident, payment)
    assert outcome.used_fallback
    assert "schema validation" in outcome.fallback_reason


async def test_groq_timeout_falls_back(kb):
    incident, payment = await _incident(kb)
    client = FakeGroq(error=GroqUnavailableError("Groq timed out after 15s"))
    outcome = await InvestigatorService(kb, client=client).investigate(incident, payment)
    assert outcome.used_fallback
    assert outcome.result.recommended_action == "RECONCILE_LEDGER"


async def test_evidence_bundle_collected_by_tools(kb):
    incident, payment = await _incident(kb)
    bundle, matches, tools = await InvestigatorService(kb).collect_evidence(incident, payment)
    assert {"get_payment", "search_historical_incidents", "check_refund_eligibility"} <= set(tools)
    assert bundle["bank"]["status"] == "SETTLED"
    assert bundle["ledger"]["entries"][0]["status"] == "FAILED"
    assert len(bundle["timeline"]) == 6


async def test_rag_top_match_for_ledger_mismatch(kb):
    matches = await HistoricalIncidentService(kb).search(
        incident_type="LEDGER_MISMATCH",
        snapshot={"gateway": "SUCCESS", "bank": "SETTLED", "merchant": "FAILED", "ledger": "FAILED", "webhook": "RECEIVED"},
    )
    assert matches[0].title == "Ledger synchronization failure"
    assert matches[0].similarity > 0.8
    assert matches[0].retrieval in ("pgvector", "python-cosine")


def test_embeddings_are_deterministic_and_normalised():
    a, b = embed("gateway SUCCESS bank SETTLED ledger FAILED"), embed("gateway SUCCESS bank SETTLED ledger FAILED")
    assert a == b
    assert abs(cosine(a, a) - 1.0) < 1e-6
    assert cosine(a, embed("refund failed issuer outage")) < cosine(a, embed("ledger FAILED bank SETTLED"))
