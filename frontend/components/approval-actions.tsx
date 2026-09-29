"use client";

import { Check, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { ActionDecision } from "@/types/api";

export function ApprovalActions({
  actionId,
  onDone,
  size = "default",
}: {
  actionId: string;
  onDone?: (result: ActionDecision) => void;
  size?: "default" | "lg";
}) {
  const [approver, setApprover] = useState("ops.manager");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function decide(kind: "approve" | "reject") {
    setBusy(kind);
    setError(null);
    try {
      const result =
        kind === "approve"
          ? await api.approve(actionId, approver, "Approved from PayFlow console")
          : await api.reject(actionId, approver, "Rejected from PayFlow console");
      onDone?.(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          aria-label="Approver"
          value={approver}
          onChange={(e) => setApprover(e.target.value)}
          className="h-9 w-40 font-mono text-xs"
        />
        <Button variant="success" size={size} onClick={() => decide("approve")} disabled={!!busy || approver.length < 2}>
          <Check />
          {busy === "approve" ? "Approving…" : "APPROVE"}
        </Button>
        <Button variant="destructive" size={size} onClick={() => decide("reject")} disabled={!!busy || approver.length < 2}>
          <X />
          {busy === "reject" ? "Rejecting…" : "REJECT"}
        </Button>
      </div>
      {error && <p className="mt-2 text-xs text-[#f87171]">{error}</p>}
    </div>
  );
}
