"use client";

import { Radio, RotateCcw, ShieldAlert, Sparkles, Wallet } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ErrorBanner, PageHeader } from "@/components/app-shell";
import { IncidentTypeChart, RecoveryChart, StatusChart, VolumeChart } from "@/components/charts";
import { useLiveRefresh } from "@/components/event-stream";
import { FailureInjectionPanel } from "@/components/failure-injection-panel";
import { IncidentTable } from "@/components/incident-table";
import { useLiveDemo } from "@/components/live-demo";
import { LivePipeline } from "@/components/live-pipeline";
import { SystemStatus } from "@/components/system-status";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { cn } from "@/lib/utils";

function Stat({ label, value, sub, accent }: { label: string; value: string | number; sub?: string; accent?: string }) {
  return (
    <Card className="px-4 py-3.5">
      <div className="text-[11px] font-medium uppercase tracking-wider text-subtle">{label}</div>
      <div className={cn("mt-1.5 text-2xl font-semibold tracking-tight text-foreground", accent)}>{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-muted">{sub}</div>}
    </Card>
  );
}

const DEMO_CARDS = [
  { demo: "LEDGER_MISMATCH", icon: Sparkles, tone: "text-primary", title: "Real-time ledger mismatch",
    text: "PayPal Sandbox payment → PayFlow ledger write fails (injected) → AI → policy → reconcile → verify" },
  { demo: "REFUND_REQUIRES_APPROVAL", icon: ShieldAlert, tone: "text-warning", title: "Refund requires approval",
    text: "PayPal Sandbox payment → merchant cancels → AI recommends REFUND → human approves → PayPal refund API" },
  { demo: "PAYMENT_ONLY", icon: Wallet, tone: "text-good", title: "Live sandbox payment",
    text: "Create, approve and capture a real PayPal Sandbox payment; inject failures afterwards" },
];

export default function DashboardPage() {
  const { data, error, refresh } = useApi(api.dashboard, [], 30000);
  useLiveRefresh(refresh, (e) => !e.event.startsWith("PAYMENT_EVENT"));
  const { open } = useLiveDemo();
  const [resetting, setResetting] = useState(false);
  const [resetMessage, setResetMessage] = useState<string | null>(null);

  async function reset() {
    if (!confirm("Reset demo data? Historical PayFlow records are regenerated and live sandbox payments are cleared.")) return;
    setResetting(true);
    setResetMessage(null);
    try {
      const result = await api.resetDemo();
      setResetMessage(`Demo reset: ${result.payments} historical payments, ${result.incidents} incidents in ${result.seconds}s`);
      await refresh();
    } catch (err) {
      setResetMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setResetting(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Payment operations"
        description="PayFlow AI observes real PayPal Sandbox payment events, investigates inconsistencies, applies deterministic policy, executes authorized recovery actions, verifies outcomes, reconciles financial state and keeps a complete audit trail."
        actions={
          <>
            <Button variant="outline" size="sm" onClick={reset} disabled={resetting}>
              <RotateCcw className={cn(resetting && "animate-spin")} />
              {resetting ? "Resetting…" : "RESET DEMO"}
            </Button>
            <Button size="sm" onClick={() => open({ demo: "LEDGER_MISMATCH" })}>
              <Radio />
              START LIVE DEMO
            </Button>
          </>
        }
      />
      {error && <ErrorBanner message={error} />}
      {resetMessage && <p className="mb-4 text-xs text-muted">{resetMessage}</p>}

      <div className="mb-5 grid grid-cols-1 gap-3 md:grid-cols-3">
        {DEMO_CARDS.map((card) => (
          <button
            key={card.demo}
            onClick={() => open({ demo: card.demo })}
            className="group flex items-start gap-3 rounded-lg border border-border bg-panel px-4 py-3 text-left hover:border-primary/50"
          >
            <card.icon className={cn("mt-0.5 size-5 shrink-0", card.tone)} />
            <div>
              <div className="text-sm font-medium">{card.title}</div>
              <div className="text-xs text-muted">{card.text}</div>
            </div>
          </button>
        ))}
      </div>

      {!data ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-[88px]" />
          ))}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Stat label="Payments processed" value={data.total_payments}
              sub={`${data.paypal_payments} PayPal Sandbox · ${money(data.total_volume, data.currency)}`} />
            <Stat label="Active incidents" value={data.active_incidents} accent={data.active_incidents ? "text-serious" : undefined}
              sub="open · awaiting · escalated" />
            <Stat label="Auto-recovered" value={data.auto_recovered} accent="text-good"
              sub={`${money(data.amount_recovered, data.currency)} recovered`} />
            <Stat label="Awaiting approval" value={data.pending_approvals} accent={data.pending_approvals ? "text-warning" : undefined}
              sub="human in the loop" />
            <Stat label="Failed payments" value={data.failed_payments} sub={`${data.refunded_payments} refunded`} />
            <Stat label="Reconciliation rate" value={`${data.reconciliation_rate}%`} accent="text-good" sub="payments matched" />
          </div>

          <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <LivePipeline />
            </div>
            <div className="space-y-4">
              <SystemStatus />
            </div>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <Card>
                <CardHeader>
                  <CardTitle>Recent incidents</CardTitle>
                  <Link href="/incidents" className="text-xs text-muted hover:text-foreground">View all →</Link>
                </CardHeader>
                <IncidentTable incidents={data.recent_incidents} empty="No incidents yet. Start a live demo." />
              </Card>
            </div>
            <FailureInjectionPanel compact />
          </div>

          <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader><CardTitle>Payment volume · last 14 days</CardTitle></CardHeader>
              <CardContent><VolumeChart data={data.payment_volume} /></CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>Payment status</CardTitle></CardHeader>
              <CardContent><StatusChart data={data.payment_status} /></CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>Incident types</CardTitle></CardHeader>
              <CardContent><IncidentTypeChart data={data.incident_types} /></CardContent>
            </Card>
            <Card className="lg:col-span-2">
              <CardHeader><CardTitle>Recovery trend · detected vs resolved</CardTitle></CardHeader>
              <CardContent><RecoveryChart data={data.recovery_trend} /></CardContent>
            </Card>
          </div>
          <p className="mt-4 text-[11px] text-subtle">
            Historical data is labelled <b>Historical PayFlow</b> and was not produced by PayPal. Live
            payments are real PayPal Sandbox transactions processed in USD.
          </p>
        </>
      )}
    </div>
  );
}
