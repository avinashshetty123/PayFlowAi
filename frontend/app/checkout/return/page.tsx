"use client";

import { CheckCircle2, CircleDashed, XCircle } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import type { CaptureResult } from "@/types/api";

function CaptureAfterApproval() {
  const params = useSearchParams();
  const orderId = params.get("token");
  const txn = params.get("txn");
  const [result, setResult] = useState<CaptureResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);
  const missing = !orderId && !txn;

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const body = orderId ? { order_id: orderId } : txn ? { transaction_id: txn } : null;
    if (!body) return;
    api
      .capture(body)
      .then((r) => {
        setResult(r);
        window.opener?.postMessage({ type: "payflow:capture", message: r.message, status: r.status }, "*");
        if (window.opener) setTimeout(() => window.close(), 1800);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [orderId, txn]);

  return (
    <Card className="mx-auto mt-10 max-w-lg p-6 text-center">
      {missing && <p className="text-sm text-[#f87171]">Missing PayPal order token in the return URL.</p>}
      {!missing && !result && !error && (
        <>
          <CircleDashed className="mx-auto size-8 animate-spin text-primary" />
          <h1 className="mt-3 text-lg font-semibold">Capturing your PayPal Sandbox payment…</h1>
          <p className="mt-1 text-sm text-muted">Order {orderId}</p>
        </>
      )}
      {result && (
        <>
          {result.status === "PROVIDER_FAILED" ? (
            <XCircle className="mx-auto size-8 text-critical" />
          ) : (
            <CheckCircle2 className="mx-auto size-8 text-good" />
          )}
          <h1 className="mt-3 text-lg font-semibold">
            {result.status === "PROVIDER_FAILED" ? "PayPal declined the capture" : "Payment captured by PayPal Sandbox"}
          </h1>
          <p className="mt-1 text-sm text-muted">
            {result.payment.transaction_id} · {money(result.payment.amount, result.payment.currency)} · {result.message}
          </p>
          <p className="mt-3 text-xs text-subtle">PayFlow is now processing downstream systems. Watch the dashboard live.</p>
        </>
      )}
      {error && (
        <>
          <XCircle className="mx-auto size-8 text-critical" />
          <h1 className="mt-3 text-lg font-semibold">Capture did not complete</h1>
          <p className="mt-1 text-sm text-[#f87171]">{error}</p>
        </>
      )}
      <Button asChild variant="outline" size="sm" className="mt-5">
        <Link href={txn ? `/payments/${txn}` : "/"}>Back to PayFlow</Link>
      </Button>
    </Card>
  );
}

export default function CheckoutReturnPage() {
  return (
    <Suspense fallback={null}>
      <CaptureAfterApproval />
    </Suspense>
  );
}
