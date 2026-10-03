"use client";

import {
  Activity, Banknote, Gauge, GitCompareArrows, Radio, ShieldAlert, Sparkles, Trash2, UserCheck, Wallet, XCircle,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ErrorBanner, PageHeader } from "@/components/app-shell";
import { IncidentTypeChart, RecoveryChart, StatusChart, VolumeChart } from "@/components/charts";
import { useLiveRefresh } from "@/components/event-stream";
import { FailureInjectionPanel } from "@/components/failure-injection-panel";
import { IncidentTable } from "@/components/incident-table";
import { useLiveDemo } from "@/components/live-demo";
import { LivePipeline } from "@/components/live-pipeline";
import { SimulationPanel } from "@/components/simulation-panel";
import { SystemStatus } from "@/components/system-status";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { cn } from "@/lib/utils";

function Stat({
  label,
  value,
  sub,
  accent,
  icon: Icon,
  href,
}: {
  label: string;
  value: string | number;
  sub?: string;
  accent?: string;
  icon: typeof Activity;
  href?: string;
}) {
  const body = (
    <Card className="flex h-full items-start gap-3 px-4 py-4 transition-colors hover:border-sky/60">
      <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-sky/15 text-brand">
        <Icon className="size-4.5" />
      </span>
      <div className="min-w-0">
        <div className="text-[11px] font-medium uppercase tracking-wider text-subtle">{label}</div>
        <div className={cn("mt-0.5 text-2xl font-bold tabular tracking-tight", accent ?? "text-foreground")}>{value}</div>
        {sub && <div className="mt-0.5 truncate text-[11px] text-muted">{sub}</div>}
      </div>
    </Card>
  );
  return href ? <Link href={href} className="block">{body}</Link> : body;
}

export default function DashboardPage() {
  const { data, error, refresh } = useApi(api.dashboard, [], 30000);
  const { data: recon, refresh: refreshRecon } = useApi(api.reconSummary, [], 30000);
  useLiveRefresh(() => {
    refresh();
    refreshRecon();
  }, (e) => !e.event.startsWith("PAYMENT_EVENT"));
  const atRisk = recon ? Object.entries(recon.exposure_by_currency).reduce((sum, [, v]) => sum + v, 0) : 0;
  const { open } = useLiveDemo();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function clearDb() {
    if (!confirm("Clear all data? This wipes everything — payments, incidents, audit logs. Cannot be undone.")) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.clearDb();
      setMsg(r.message);
      await refresh();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        description="Real-time payment operations — PayPal Sandbox payments, AI-driven incident investigation, policy enforcement and autonomous recovery."
        actions={
          <>
            <Button variant="ghost" size="sm" onClick={clearDb} disabled={busy} className="text-muted hover:text-critical">
              <Trash2 className="size-3.5" />
              {busy ? "Clearing…" : "Clear DB"}
            </Button>
            <Button size="sm" onClick={() => open({ demo: "PAYMENT_ONLY" })}>
              <Radio className="size-3.5" />
              Start Live Demo
            </Button>
          </>
        }
      />

      {error && <ErrorBanner message={error} />}
      {msg && <p className="text-xs text-muted">{msg}</p>}

      {/* Stats */}
      {!data ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-[88px]" />)}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat icon={Wallet} label="Total payments" value={data.total_payments} href="/payments"
            sub={`${data.paypal_payments} PayPal · ${money(data.total_volume, data.currency)}`} />
          <Stat icon={ShieldAlert} label="Active incidents" value={data.active_incidents} href="/incidents"
            accent={data.active_incidents ? "text-serious" : undefined} sub="open · investigating · escalated" />
          <Stat icon={Banknote} label="Money at risk" value={money(atRisk, "USD")} href="/reconciliation"
            accent={atRisk > 0 ? "text-critical" : "text-good"}
            sub={recon ? `${recon.open_breaks} open breaks · ${recon.sla_breaches.length} SLA breaches` : "open breaks"} />
          <Stat icon={UserCheck} label="Awaiting approval" value={data.pending_approvals} href="/approvals"
            accent={data.pending_approvals ? "text-warning" : undefined} sub="human in the loop" />
          <Stat icon={Sparkles} label="Auto-recovered" value={data.auto_recovered} accent="text-good"
            sub={`${money(data.amount_recovered, data.currency)} recovered`} />
          <Stat icon={Gauge} label="Auto-heal rate" value={recon ? `${recon.auto_heal_rate}%` : "—"} accent="text-good"
            sub="breaks fixed without a human" />
          <Stat icon={GitCompareArrows} label="Reconciliation" value={`${data.reconciliation_rate}%`} accent="text-good"
            sub="payments matched" href="/reconciliation" />
          <Stat icon={XCircle} label="Failed payments" value={data.failed_payments} sub={`${data.refunded_payments} refunded`} />
        </div>
      )}

      {/* Live pipeline + system status */}
      {data && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="lg:col-span-2"><LivePipeline /></div>
          <SystemStatus />
        </div>
      )}

      {/* Demo quick-start */}
      <div>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-wider text-subtle">Quick start</p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {[
            { demo: "PAYMENT_ONLY", label: "Live sandbox payment", desc: "Real PayPal Sandbox order → capture → pipeline" },
            { demo: "LEDGER_MISMATCH", label: "Ledger mismatch", desc: "PayPal succeeds → ledger fails → AI recovers" },
            { demo: "REFUND_REQUIRES_APPROVAL", label: "Refund approval", desc: "Capture → cancel → AI recommends refund → human approves" },
          ].map((d) => (
            <button key={d.demo} onClick={() => open({ demo: d.demo })}
              className="rounded-lg border border-border bg-panel px-4 py-3 text-left transition-colors hover:border-primary/50 hover:bg-panel-2">
              <div className="text-sm font-medium text-foreground">{d.label}</div>
              <div className="mt-0.5 text-xs text-muted">{d.desc}</div>
            </button>
          ))}
        </div>
      </div>

      {/* Recent incidents + tools */}
      {data && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <Card>
              <CardHeader>
                <CardTitle>Recent incidents</CardTitle>
                <Link href="/incidents" className="text-xs text-muted hover:text-foreground">View all →</Link>
              </CardHeader>
              <IncidentTable incidents={data.recent_incidents} empty="No incidents yet — start a live demo or run a simulation." />
            </Card>
          </div>
          <div className="space-y-4">
            <SimulationPanel />
            <FailureInjectionPanel compact />
          </div>
        </div>
      )}

      {/* Charts */}
      {data && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader><CardTitle>Payment volume · last 14 days</CardTitle></CardHeader>
            <CardContent><VolumeChart data={data.payment_volume} /></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Recovery trend</CardTitle></CardHeader>
            <CardContent><RecoveryChart data={data.recovery_trend} /></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Payment status breakdown</CardTitle></CardHeader>
            <CardContent><StatusChart data={data.payment_status} /></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Incident types</CardTitle></CardHeader>
            <CardContent><IncidentTypeChart data={data.incident_types} /></CardContent>
          </Card>
        </div>
      )}

      {data && (
        <p className="text-[11px] text-subtle">
          Live payments are real PayPal Sandbox transactions (USD). Simulated payments are synthetic — no PayPal involved.
          Failures labelled <b>DEMO FAILURE INJECTION</b> are introduced by PayFlow into its own systems only.
        </p>
      )}
    </div>
  );
}
