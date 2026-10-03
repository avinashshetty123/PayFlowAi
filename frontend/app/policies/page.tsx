"use client";

import { CheckCircle2, FlaskConical, Gauge, OctagonPause, Play, ShieldCheck, XCircle } from "lucide-react";
import { useState } from "react";

import { AgentGraph } from "@/components/agent-graph";
import { ErrorBanner, PageHeader } from "@/components/app-shell";
import { useLiveRefresh } from "@/components/event-stream";
import { RiskMeter } from "@/components/risk-meter";
import { StatusBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { PolicySimulation } from "@/types/api";

const EFFECT_TONE: Record<string, string> = {
  DENY: "bg-critical/10 text-critical",
  HUMAN_APPROVAL_REQUIRED: "bg-warning/10 text-warning",
  ALLOW: "bg-good/10 text-good",
};
const ACTIONS = ["", "RECONCILE_LEDGER", "RETRY_WEBHOOK", "REFUND", "MARK_PAYMENT_FAILED", "ESCALATE"];

function KillSwitch({ enabled, reason, by, at, onChange }: {
  enabled: boolean; reason: string | null; by: string | null; at: string | null; onChange: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function toggle() {
    setBusy(true);
    setError(null);
    try {
      await api.setKillSwitch(!enabled, text || (enabled ? "Automation resumed" : "Operator pause"), "ops.console");
      setText("");
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card className={cn(enabled && "border-critical/50")}>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5"><OctagonPause className="size-3.5" /> Automation kill switch</CardTitle>
        <StatusBadge status={enabled ? "FAILED" : "PASSED"} label={enabled ? "ENGAGED" : "AUTOMATION ON"} />
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted">
          One switch pauses every autonomous financial action. Incidents keep being detected and investigated, but
          every fix waits for a human.
        </p>
        {enabled && <p className="rounded-lg bg-critical/5 px-3 py-2 text-xs text-critical">
          Engaged by {by} · {at ? dateTime(at) : ""}: {reason}
        </p>}
        <Input placeholder={enabled ? "Reason for resuming…" : "Reason (e.g. provider incident)"} value={text}
          onChange={(e) => setText(e.target.value)} />
        <Button variant={enabled ? "success" : "destructive"} size="sm" onClick={toggle} disabled={busy}>
          {enabled ? "Resume automation" : "Pause all automation"}
        </Button>
        {error && <p className="text-xs text-critical">{error}</p>}
      </CardContent>
    </Card>
  );
}

function Simulator({ transactions }: { transactions: { txn: string; label: string }[] }) {
  const [txn, setTxn] = useState("");
  const [action, setAction] = useState("");
  const [human, setHuman] = useState(false);
  const [result, setResult] = useState<PolicySimulation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const chosen = txn || transactions[0]?.txn || "";
  async function run() {
    setError(null);
    try {
      setResult(await api.simulatePolicy({ transaction_id: chosen, action: action || undefined, human_approved: human }));
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5"><FlaskConical className="size-3.5" /> Policy simulator · what-if</CardTitle>
        <span className="text-[10px] uppercase tracking-wider text-subtle">read-only</span>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div>
            <Label>Incident</Label>
            <Select value={chosen} onChange={(e) => setTxn(e.target.value)} className="font-mono text-xs">
              {transactions.map((t) => <option key={t.txn} value={t.txn}>{t.label}</option>)}
            </Select>
          </div>
          <div>
            <Label>Action</Label>
            <Select value={action} onChange={(e) => setAction(e.target.value)} className="font-mono text-xs">
              {ACTIONS.map((a) => <option key={a} value={a}>{a || "AI recommendation"}</option>)}
            </Select>
          </div>
          <label className="flex items-end gap-2 pb-2 text-xs text-muted">
            <input type="checkbox" checked={human} onChange={(e) => setHuman(e.target.checked)} /> with human approval
          </label>
        </div>
        <Button size="sm" onClick={run} disabled={!chosen}><Play /> Evaluate</Button>
        {error && <p className="text-xs text-critical">{error}</p>}
        {result && (
          <div className="space-y-3 rounded-xl border border-border bg-panel-2 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs font-semibold">{result.evaluation.action_type}</span>
              <span className="text-xs text-subtle">for {result.incident_number} {result.incident_type}</span>
              <span className="ml-auto"><StatusBadge status={result.evaluation.decision} label={result.evaluation.label} /></span>
            </div>
            <ul className="space-y-1">
              {result.evaluation.checks.map((c) => (
                <li key={c.name} className="flex items-start gap-2 text-xs">
                  {c.passed ? <CheckCircle2 className="mt-px size-3.5 text-good" /> : <XCircle className="mt-px size-3.5 text-critical" />}
                  <span className="font-mono">{c.name}</span>
                  <span className="ml-auto text-right font-mono text-subtle">{c.detail}</span>
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap gap-1">
              {result.evaluation.fired_rules.map((r) => <span key={r} className="rounded bg-navy px-1.5 py-px font-mono text-[10px] text-white">{r}</span>)}
            </div>
            <RiskMeter risk={result.risk} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function PoliciesPage() {
  const { data, error, refresh } = useApi(api.policies, [], 30000);
  const { data: graph } = useApi(api.agentGraph, [], undefined);
  const { data: incidents } = useApi(() => api.incidents({ limit: 50 }), [], undefined);
  useLiveRefresh(refresh, (e) => e.event.startsWith("ACTION") || e.transactionId === "SYSTEM");

  const transactions = (incidents?.items ?? []).map((i) => ({
    txn: i.transaction_id, label: `${i.incident_number} · ${i.transaction_id} · ${i.type}`,
  }));
  const breaker = data?.circuit_breaker;

  return (
    <div className="space-y-5">
      <PageHeader
        title="Policies & Agent"
        description="Policy-as-code that the AI cannot override, operator controls, and the LangGraph agent that orchestrates every incident."
      />
      {error && <ErrorBanner message={error} />}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {data && (
          <KillSwitch enabled={data.kill_switch.enabled} reason={data.kill_switch.reason} by={data.kill_switch.updated_by}
            at={data.kill_switch.updated_at} onChange={refresh} />
        )}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-1.5"><Gauge className="size-3.5" /> Automation circuit breaker</CardTitle>
            {breaker && <StatusBadge status={breaker.tripped ? "FAILED" : "PASSED"} label={breaker.tripped ? "TRIPPED" : "HEALTHY"} />}
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="text-3xl font-bold text-foreground">
              {breaker?.count ?? 0}<span className="text-base font-medium text-subtle"> / {breaker?.limit ?? 0}</span>
            </div>
            <div className="h-2 rounded-full bg-panel-2">
              <div className={cn("h-2 rounded-full", breaker?.tripped ? "bg-critical" : "bg-sky")}
                style={{ width: `${Math.min(((breaker?.count ?? 0) / (breaker?.limit || 1)) * 100, 100)}%` }} />
            </div>
            <p className="text-xs text-muted">
              Automated financial actions in the last {breaker?.window_minutes ?? 10} min. Beyond the limit, runaway
              automation is contained: every fix needs a human.
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-1.5"><ShieldCheck className="size-3.5" /> Thresholds</CardTitle>
            <span className="font-mono text-[10px] text-subtle">{data?.version}</span>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-3 text-xs">
            {data && Object.entries(data.thresholds).map(([k, v]) => (
              <div key={k} className="rounded-lg bg-panel-2 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wider text-subtle">{k.replaceAll("_", " ")}</div>
                <div className="mt-0.5 font-mono text-sm font-semibold text-foreground">{v}</div>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>LangGraph incident agent</CardTitle>
          <span className="text-[10px] uppercase tracking-wider text-subtle">{graph?.engine}</span>
        </CardHeader>
        <CardContent>
          <AgentGraph engine={graph?.engine} />
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-5">
        <Card className="lg:col-span-3 overflow-hidden">
          <CardHeader>
            <CardTitle>Policy rules</CardTitle>
            <span className="font-mono text-[10px] text-subtle">version {data?.version}</span>
          </CardHeader>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Rule</TableHead>
                <TableHead>Effect</TableHead>
                <TableHead>What it enforces</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data?.rules.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="whitespace-nowrap">
                    <div className="font-mono text-[11px] font-semibold text-brand">{r.id}</div>
                    <div className="text-xs text-foreground">{r.name}</div>
                  </TableCell>
                  <TableCell>
                    <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-semibold", EFFECT_TONE[r.effect])}>
                      {r.effect.replaceAll("_", " ")}
                    </span>
                  </TableCell>
                  <TableCell className="text-xs text-muted">{r.description}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
        <div className="space-y-5 lg:col-span-2">
          <Simulator transactions={transactions} />
          <Card className="overflow-hidden">
            <CardHeader><CardTitle>Remediation playbook</CardTitle></CardHeader>
            <Table>
              <TableBody>
                {data && Object.entries(data.playbook).map(([type, actions]) => (
                  <TableRow key={type}>
                    <TableCell className="font-mono text-[11px]">{type}</TableCell>
                    <TableCell className="text-right">
                      {actions.map((a, i) => (
                        <span key={a} className={cn("ml-1 inline-block rounded px-1.5 py-px font-mono text-[10px]",
                          i === 0 ? "bg-navy text-white" : "bg-panel-2 text-muted")}>{a}</span>
                      ))}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        </div>
      </div>
    </div>
  );
}
