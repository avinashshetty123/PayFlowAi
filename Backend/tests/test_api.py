async def test_health(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["database"] == "HEALTHY"


async def test_demo_1_ledger_mismatch_via_api(client):
    response = await client.post("/api/simulator/payments", json={"amount": 4850, "scenario": "LEDGER_MISMATCH", "sync": True})
    assert response.status_code == 201, response.text
    body = response.json()
    txn = body["payment"]["transaction_id"]
    assert body["reconciliation"]["incident_type"] == "LEDGER_MISMATCH"
    assert body["incident_status"] == "RESOLVED"
    assert body["payment"]["ledger_status"] == "SUCCESS"

    incident_id = body["incident_id"]
    detail = (await client.get(f"/api/incidents/{incident_id}")).json()
    assert detail["status"] == "RESOLVED"
    assert detail["initial_snapshot"]["merchant"] == "FAILED"
    assert detail["current_snapshot"]["merchant"] == "SUCCESS"
    assert detail["investigation"]["recommendation"]["recommendedAction"] == "RECONCILE_LEDGER"
    assert detail["actions"][0]["idempotency_key"] == f"payflow:reconcile:{txn}"

    by_number = await client.get(f"/api/incidents/{detail['incident_number']}")
    assert by_number.status_code == 200

    timeline = (await client.get(f"/api/incidents/{incident_id}/timeline")).json()
    labels = [item["label"] for item in timeline]
    for expected in ("Payment created", "Gateway SUCCESS", "Webhook received", "Merchant FAILED", "Bank SETTLED",
                     "Mismatch detected", "AI investigation started", "Root cause identified", "Policy evaluated",
                     "Ledger reconciled", "Verification passed", "Incident resolved"):
        assert expected in labels, labels

    audit = (await client.get(f"/api/incidents/{incident_id}/audit")).json()
    assert {"MISMATCH_DETECTED", "ACTION_EXECUTED", "VERIFICATION_PASSED", "INCIDENT_RESOLVED"} <= {a["event"] for a in audit}
    assert all(a["actor"] and a["reason"] for a in audit)

    payment = (await client.get(f"/api/payments/{txn}")).json()
    assert payment["payment"]["merchant_status"] == "SUCCESS"
    assert payment["incidents"][0]["status"] == "RESOLVED"


async def test_demo_2_refund_approval_via_api(client):
    body = (await client.post("/api/simulator/payments", json={"amount": 85, "scenario": "REFUND_FAILURE", "sync": True})).json()
    txn = body["payment"]["transaction_id"]
    assert body["incident_status"] == "AWAITING_APPROVAL"

    queue = (await client.get("/api/actions")).json()
    assert len(queue) == 1
    item = queue[0]
    assert item["transaction_id"] == txn
    assert item["action"]["action_type"] == "REFUND"
    assert item["risk"] == "HIGH"

    approved = await client.post(f"/api/actions/{item['action']['id']}/approve", json={"approver": "demo.judge"})
    assert approved.status_code == 200, approved.text
    result = approved.json()
    assert result["incident_status"] == "RESOLVED"
    assert result["verification"]["status"] == "PASSED"
    assert (await client.get("/api/actions")).json() == []

    # A second approval must not refund again.
    again = (await client.post(f"/api/actions/{item['action']['id']}/approve")).json()
    assert again["deduplicated"] is True

    decided = (await client.get("/api/actions", params={"view": "decided"})).json()
    assert decided[0]["action"]["approved_by"] == "demo.judge"


async def test_reject_unknown_action_404(client):
    response = await client.post("/api/actions/00000000-0000-0000-0000-000000000000/approve")
    assert response.status_code == 404


async def test_validation_errors(client):
    assert (await client.post("/api/simulator/payments", json={"amount": -5, "scenario": "SUCCESS"})).status_code == 422
    assert (await client.post("/api/simulator/payments", json={"amount": 10, "scenario": "NOPE"})).status_code == 422


async def test_lists_dashboard_and_reconciliation(client):
    for scenario in ("SUCCESS", "FAILED", "LEDGER_MISMATCH", "WEBHOOK_DELAY"):
        await client.post("/api/simulator/payments", json={"amount": 1299, "scenario": scenario, "sync": True})

    payments = (await client.get("/api/payments")).json()
    assert payments["total"] == 4
    incidents = (await client.get("/api/incidents")).json()
    assert incidents["total"] == 2
    stats = (await client.get("/api/dashboard/stats")).json()
    assert stats["total_payments"] == 4
    assert stats["auto_recovered"] == 2
    assert stats["amount_recovered"] == 2598.0
    assert len(stats["payment_volume"]) == 14
    recon = (await client.get("/api/reconciliation")).json()
    assert recon["summary"]["checked"] == 4
    assert recon["summary"]["mismatched"] == 0
    audit = (await client.get("/api/audit", params={"event": "INCIDENT_RESOLVED"})).json()
    assert audit["total"] == 2


async def test_manual_investigate_endpoint(client):
    body = (await client.post("/api/simulator/payments", json={"amount": 2799, "scenario": "TIMEOUT", "sync": True})).json()
    assert body["incident_status"] == "RESOLVED"
    response = await client.post(f"/api/incidents/{body['incident_id']}/investigate", params={"sync": True})
    assert response.json()["message"].startswith("Incident already resolved")


async def test_demo_reset(client):
    response = await client.post("/api/demo/reset")
    assert response.status_code == 200
    body = response.json()
    assert body["payments"] >= 100
    assert body["historical_incidents"] >= 20
    stats = (await client.get("/api/dashboard/stats")).json()
    assert stats["pending_approvals"] >= 2
    assert stats["active_incidents"] >= 3
    assert stats["auto_recovered"] >= 10
