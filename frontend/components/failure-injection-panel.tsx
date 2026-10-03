"use client";

import { FlaskConical } from "lucide-react";
import { useState } from "react";

import { useLiveRefresh } from "@/components/event-stream";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label, Select } from "@/components/ui/input";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Controlled test panel. Injects failures into PayFlow's own systems for a real PayPal Sandbox payment. */
export function FailureInjectionPanel({ transactionId, compact }: { transactionId?: string; compact?: boolean }) {
  const { data: catalog } = useApi(api.failureScenarios, [], undefined);
  const { data: payments, refresh } = useApi(
    () => api.payments({ provider: "PAYPAL_SANDBOX", limit: 15 }),
    [],
    undefined,
  );
  useLiveRefresh(refresh, (e) => e.event === "PAYMENT_CREATED" || e.event === "PAYMENT_CAPTURE_COMPLETED");
  const [chosen, setTxn] = useState(transactionId ?? "");
  const txn = chosen || payments?.items[0]?.transaction_id || "";
  const [scenario, setScenario] = useState("LEDGER_WRITE_FAILURE");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function inject() {
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.injectFailure(txn, scenario);
      setMessage({ ok: true, text: result.message ?? `${scenario} ${result.state.toLowerCase()}` });
    } catch (err) {
      setMessage({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  const selected = catalog?.scenarios.find((s) => s.scenario === scenario);
  const options = (catalog?.scenarios ?? []).filter((s) => s.scenario !== "NONE");

  return (
    <Card className="border-warning/40">
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5 text-warning">
          <FlaskConical className="size-3.5" /> DEMO FAILURE INJECTION
        </CardTitle>
        <span className="text-[10px] uppercase tracking-wider text-subtle">PayFlow demo environment</span>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted">
          {catalog?.note ?? "Failures are injected into PayFlow's own infrastructure. PayPal results are never altered."}
        </p>
        <div className={cn("grid gap-3", compact ? "grid-cols-1" : "grid-cols-1 sm:grid-cols-2")}>
          {!transactionId && (
            <div>
              <Label htmlFor="fi-payment">Payment (PayPal Sandbox)</Label>
              <Select id="fi-payment" value={txn} onChange={(e) => setTxn(e.target.value)} className="font-mono text-xs">
                {!payments?.items.length && <option value="">No PayPal payments yet: start a live demo</option>}
                {payments?.items.map((p) => (
                  <option key={p.id} value={p.transaction_id}>
                    {p.transaction_id} · {money(p.amount, p.currency)} · {p.provider_status ?? p.overall_status}
                  </option>
                ))}
              </Select>
            </div>
          )}
          <div>
            <Label htmlFor="fi-scenario">Scenario</Label>
            <Select id="fi-scenario" value={scenario} onChange={(e) => setScenario(e.target.value)} className="font-mono text-xs">
              {options.map((s) => (
                <option key={s.scenario} value={s.scenario} disabled={!s.available}>
                  {s.label}{!s.available ? " (needs webhooks)" : ""}
                </option>
              ))}
            </Select>
          </div>
        </div>
        {selected && (
          <p className="text-[11px] text-subtle">
            {selected.description}
            {selected.before_capture_only && " Arm before the PayPal capture."}
            {selected.expected_incident && ` Expected detection: ${selected.expected_incident}.`}
          </p>
        )}
        <Button variant="outline" size="sm" onClick={inject} disabled={busy || !txn}
          className="border-warning/50 text-warning hover:bg-warning/10">
          <FlaskConical /> {busy ? "Injecting…" : "Inject Failure"}
        </Button>
        {message && <p className={cn("text-xs", message.ok ? "text-good" : "text-critical")}>{message.text}</p>}
      </CardContent>
    </Card>
  );
}
