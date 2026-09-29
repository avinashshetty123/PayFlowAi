"use client";

import { Activity, ArrowRight, CheckCircle2, XCircle } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { SimulationResult } from "@/types/api";

const TONE: Record<string, string> = {
  SUCCESS: "text-good", SETTLED: "text-good",
  FAILED: "text-[#f87171]", REFUND_FAILED: "text-[#f87171]",
  PENDING: "text-warning", DELAYED: "text-warning",
  UNKNOWN: "text-muted", NOT_RECEIVED: "text-muted",
};

function StatusDot({ value }: { value: string }) {
  return (
    <span className={cn("font-mono text-[11px] font-semibold", TONE[value] ?? "text-muted")}>
      {value}
    </span>
  );
}

export function SimulationPanel() {
  const { data: scenarios } = useApi(api.simulatorScenarios, [], undefined);
  const [scenario, setScenario] = useState("LEDGER_MISMATCH");
  const [amount, setAmount] = useState("25.00");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selected = scenarios?.find((s) => s.scenario === scenario);

  async function run() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.simulate({ scenario, amount: Number(amount), sync: true });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5">
          <Activity className="size-3.5 text-primary" /> Simulate Payment
        </CardTitle>
        <span className="text-[10px] uppercase tracking-wider text-subtle">
          No PayPal needed · instant pipeline
        </span>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted">
          Inject a synthetic payment through the full PayFlow pipeline — reconciliation, AI investigation,
          policy engine, action executor and verification — without touching PayPal.
        </p>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_140px]">
          <div>
            <Label htmlFor="sim-scenario">Scenario</Label>
            <Select id="sim-scenario" value={scenario} onChange={(e) => setScenario(e.target.value)} className="font-mono text-xs">
              {(scenarios ?? []).map((s) => (
                <option key={s.scenario} value={s.scenario}>
                  {s.scenario}{s.expects_incident ? " ⚡" : " ✓"}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="sim-amount">Amount (USD)</Label>
            <Input id="sim-amount" type="number" min={1} step="0.01" value={amount}
              onChange={(e) => setAmount(e.target.value)} className="font-mono" />
          </div>
        </div>

        {selected && (
          <div className="rounded-md border border-border bg-background/60 p-2.5 text-xs text-muted">
            <p className="mb-1.5">{selected.description}</p>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              {Object.entries(selected.systems).map(([k, v]) => (
                <span key={k} className="capitalize">
                  {k}: <StatusDot value={v} />
                </span>
              ))}
            </div>
          </div>
        )}

        <Button size="sm" onClick={run} disabled={busy || !(Number(amount) > 0)} className="w-full">
          <Activity /> {busy ? "Running pipeline…" : "Run Simulation"}
        </Button>

        {error && (
          <p className="flex items-center gap-1.5 text-xs text-[#f87171]">
            <XCircle className="size-3.5 shrink-0" /> {error}
          </p>
        )}

        {result && (
          <div className="space-y-2 rounded-md border border-border bg-background/60 p-3">
            <div className="flex items-center justify-between">
              <div>
                <span className="font-mono text-sm font-semibold">{result.payment.transaction_id}</span>
                <span className="ml-2 text-xs text-muted">{money(result.payment.amount, result.payment.currency)}</span>
              </div>
              {result.reconciliation.consistent ? (
                <span className="flex items-center gap-1 text-xs text-good"><CheckCircle2 className="size-3.5" /> Consistent</span>
              ) : (
                <span className="flex items-center gap-1 text-xs text-warning"><Activity className="size-3.5" /> Incident created</span>
              )}
            </div>

            <p className="text-xs text-muted">{result.reconciliation.summary}</p>

            <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px]">
              {Object.entries(result.reconciliation.snapshot).map(([k, v]) => (
                <span key={k} className="capitalize">{k}: <StatusDot value={String(v)} /></span>
              ))}
            </div>

            {result.incident_id && (
              <div className={cn(
                "flex items-center justify-between rounded border px-2.5 py-1.5",
                result.incident_status === "RESOLVED" ? "border-good/40 bg-good/5" :
                result.incident_status === "ESCALATED" ? "border-[#f87171]/40 bg-[#f87171]/5" :
                "border-warning/40 bg-warning/5"
              )}>
                <span className="text-xs">
                  {result.incident_number} · <span className="font-mono">{result.incident_status}</span>
                </span>
                <Button asChild size="sm" variant="outline">
                  <Link href={`/incidents/${result.incident_id}`}>
                    View incident <ArrowRight className="size-3" />
                  </Link>
                </Button>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
