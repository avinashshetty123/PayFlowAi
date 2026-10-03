"""Tamper-evident audit log.

A background sealer hash-chains audit rows in the order it seals them:

    hash_n = SHA-256(hash_{n-1} || canonical_json(row_n))

Any later edit or deletion of a sealed row breaks every following hash, and
``verify_chain`` reports the first broken link. Sealing is asynchronous, so
writers never contend for a global lock on the hot path.
"""

import hashlib
import json

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.models.base import utcnow

GENESIS = "0" * 64
_SEALER_LOCK = 7314159  # pg advisory lock id: one sealer at a time across processes


def row_digest(prev_hash: str, row: AuditLog) -> str:
    payload = json.dumps(
        {
            "id": str(row.id), "seq": row.seq, "transaction_id": row.transaction_id,
            "incident_id": str(row.incident_id) if row.incident_id else None, "actor": row.actor,
            "event": row.event, "reason": row.reason, "evidence": row.evidence, "result": row.result,
            "created_at": row.created_at.isoformat(),
        },
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256((prev_hash + payload).encode()).hexdigest()


async def seal_pending(session: AsyncSession, *, limit: int = 500) -> int:
    got = await session.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _SEALER_LOCK})
    if not got:
        return 0
    last = await session.scalar(select(AuditLog).where(AuditLog.chain_index.is_not(None))
                                .order_by(AuditLog.chain_index.desc()).limit(1))
    prev_hash, index = (last.hash, last.chain_index) if last else (GENESIS, 0)
    rows = list(await session.scalars(
        select(AuditLog).where(AuditLog.chain_index.is_(None)).order_by(AuditLog.seq).limit(limit)
    ))
    now = utcnow()
    for row in rows:
        index += 1
        row.chain_index = index
        row.prev_hash = prev_hash
        row.hash = row_digest(prev_hash, row)
        row.sealed_at = now
        prev_hash = row.hash
    await session.commit()
    return len(rows)


async def verify_chain(session: AsyncSession) -> dict:
    sealed = await session.scalar(select(func.count()).where(AuditLog.chain_index.is_not(None))) or 0
    unsealed = await session.scalar(select(func.count()).where(AuditLog.chain_index.is_(None))) or 0
    prev_hash = GENESIS
    checked = 0
    result = await session.stream_scalars(
        select(AuditLog).where(AuditLog.chain_index.is_not(None)).order_by(AuditLog.chain_index)
    )
    async for row in result:
        checked += 1
        expected = row_digest(prev_hash, row)
        if row.prev_hash != prev_hash or row.hash != expected:
            return {"verified": False, "sealed": sealed, "unsealed": unsealed, "checked": checked,
                    "broken_at": {"chain_index": row.chain_index, "audit_id": str(row.id), "event": row.event,
                                  "transaction_id": row.transaction_id},
                    "head": prev_hash}
        prev_hash = row.hash
    return {"verified": True, "sealed": sealed, "unsealed": unsealed, "checked": checked, "broken_at": None,
            "head": prev_hash}


async def reset_chain(session: AsyncSession) -> None:
    """Used after bulk seeding rewrites timestamps: the sealer re-chains everything."""
    await session.execute(text("UPDATE audit_logs SET chain_index = NULL, prev_hash = NULL, hash = NULL, sealed_at = NULL"))
    await session.commit()
