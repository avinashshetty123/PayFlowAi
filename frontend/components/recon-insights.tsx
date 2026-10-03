"use client";

import { AlarmClock, Banknote, Fingerprint, ShieldCheck, ShieldX, Timer, Wrench } from "lucide-react";
import Link from "next/link";

import { useLiveRefresh } from "@/components/event-stream";
import { SeverityBadge } from "@/components/status";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { cn } from "@/lib/utils";

function Kpi({ icon: Icon, label, value, sub, tone }: {
  icon: typeof Banknote; label: string; value: string; sub?: string; tone?: string;
}) {
  return (
    <Card className="flex items-start gap-3 px-4 py-3.5">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-sky/15 text-brand"><Icon className="size-4" /></span>
      <div>
        <div className="text-[11px] font-medium uppercase tracking-wider text-subtle">{label}</div>
        <div className={cn("text-xl font-bold text-foreground", tone)}>{value}</div>
        {sub && <div className="text-[11px] text-muted">{sub}</div>}
      </div>
    </Card>
  );
}

function duration(seconds: number | null) {
  if (seconds === null) return "—";
  if (seconds < 90) return `${seconds}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

/** Exposure, ageing, SLA, auto-heal and MTTR: what finance-ops actually asks about breaks. */
export function ReconInsights() {
  const { data, refresh } = useApi(api.reconSummary, [], 30000);
  useLiveRefresh(refresh, (e) => e.event.startsWith("INCIDENT") || e.event.startsWith("RECONCILIATION"));
  if (!data) return null;
  const exposure = Object.entries(data.exposure_by_currency);
  const maxAging = Math.max(...data.aging.map((a) => a.count), 1);
  return (
    <div className="mb-5 space-y-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Kpi icon={Banknote} label="Money at risk" tone={exposure.some(([, v]) => v > 0) ? "text-critical" : "text-good"}
          value={exposure.length ? exposure.map(([c, v]) => money(v, c)).join(" + ") : money(0, "USD")}
          sub={`${data.open_breaks} open breaks`} />
        <Kpi icon={Fingerprint} label="Auto-match rate" value={`${data.match_rate}%`} sub="payments fully reconciled" />
        <Kpi icon={Wrench} label="Auto-heal rate" value={`${data.auto_heal_rate}%`} sub="breaks fixed without a human" />
        <Kpi icon={Timer} label="Median time to resolve" value={duration(data.mttr_seconds)} sub={`${data.resolved_incidents} resolved`} />
        <Kpi icon={AlarmClock} label="SLA breaches" value={String(data.sla_breaches.length)}
          tone={data.sla_breaches.length ? "text-critical" : "text-good"} sub={`${data.runs_last_24h} reconciliation runs / 24h`} />
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card>
          <CardHeader><CardTitle>Break ageing</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            {data.aging.map((a) => (
              <div key={a.bucket} className="text-xs">
                <div className="flex justify-between"><span className="text-muted">{a.bucket}</span><span className="font-mono font-semibold">{a.count}</span></div>
                <div className="mt-0.5 h-1.5 rounded-full bg-panel-2">
                  <div className="h-1.5 rounded-full bg-navy" style={{ width: `${(a.count / maxAging) * 100}%` }} />
                </div>
              </div>
            ))}
            <p className="pt-1 text-[10px] text-subtle">
              SLA: {Object.entries(data.sla_policy).map(([s, m]) => `${s} ${m < 60 ? `${m}m` : `${m / 60}h`}`).join(" · ")}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Exposure by break type</CardTitle></CardHeader>
          <CardContent className="space-y-1.5">
            {data.exposure_by_type.length === 0 && <p className="text-xs text-muted">No open breaks.</p>}
            {data.exposure_by_type.map((b) => (
              <div key={b.type} className="flex items-center justify-between rounded-lg bg-panel-2 px-3 py-1.5 text-xs">
                <span className="font-mono">{b.type}</span>
                <span className="text-muted">{b.count} · <b className="text-foreground">{money(b.exposure, "USD")}</b></span>
              </div>
            ))}
          </CardContent>
        </Card>
        <Card className="overflow-hidden">
          <CardHeader><CardTitle>SLA breaches</CardTitle></CardHeader>
          {data.sla_breaches.length === 0 ? (
            <CardContent><p className="text-xs text-good">Every open break is within its SLA.</p></CardContent>
          ) : (
            <Table>
              <TableHeader>
                <TableRow><TableHead>Incident</TableHead><TableHead>Age</TableHead><TableHead className="text-right">At risk</TableHead></TableRow>
              </TableHeader>
              <TableBody>
                {data.sla_breaches.slice(0, 6).map((b) => (
                  <TableRow key={b.incident_id}>
                    <TableCell>
                      <Link href={`/incidents/${b.incident_id}`} className="font-mono text-[11px] font-semibold hover:text-info">{b.incident_number}</Link>
                      <div className="flex items-center gap-1 text-[10px] text-subtle"><SeverityBadge severity={b.severity} /> {b.type}</div>
                    </TableCell>
                    <TableCell className="text-xs text-critical">{duration(b.age_minutes * 60)} / {duration(b.sla_minutes * 60)}</TableCell>
                    <TableCell className="text-right font-mono text-xs">{money(b.exposure, b.currency)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Card>
      </div>
    </div>
  );
}

/** Proof that the audit log has not been altered (SHA-256 hash chain). */
export function AuditIntegrityBanner() {
  const { data, refresh } = useApi(api.auditVerify, [], 60000);
  useLiveRefresh(refresh, (e) => e.event === "INCIDENT_RESOLVED");
  if (!data) return null;
  const ok = data.verified;
  return (
    <div className={cn("mb-4 flex flex-wrap items-center gap-3 rounded-xl border px-4 py-3 text-sm",
      ok ? "border-good/30 bg-good/5" : "border-critical/40 bg-critical/5")}>
      {ok ? <ShieldCheck className="size-5 text-good" /> : <ShieldX className="size-5 text-critical" />}
      <div className="flex-1">
        <div className={cn("font-semibold", ok ? "text-good" : "text-critical")}>
          {ok ? "Audit log integrity verified" : "Audit log tampering detected"}
        </div>
        <div className="text-xs text-muted">
          {ok
            ? `${data.sealed} records sealed into a SHA-256 hash chain${data.unsealed ? ` · ${data.unsealed} awaiting seal` : ""}. Editing or deleting any sealed record breaks the chain.`
            : `Chain breaks at record #${data.broken_at?.chain_index} (${data.broken_at?.event} · ${data.broken_at?.transaction_id}).`}
        </div>
      </div>
      <span className="font-mono text-[10px] text-subtle" title="Chain head hash">head {data.head.slice(0, 16)}…</span>
    </div>
  );
}
