"use client";

import {
  ArrowLeft,
  ArrowRight,
  Bot,
  CheckCircle2,
  CircleDashed,
  FlaskConical,
  Hand,
  History,
  KeyRound,
  Play,
  RotateCcw,
  Scale,
  ShieldCheck,
  UserCheck,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { AgentGraph } from "@/components/agent-graph";
import { ErrorBanner } from "@/components/app-shell";
import { ApprovalActions } from "@/components/approval-actions";
import { useLiveRefresh } from "@/components/event-stream";
import { LifecycleStepper } from "@/components/lifecycle";
import { RiskMeter } from "@/components/risk-meter";
import { SeverityBadge, StatusBadge, ToneIcon, toneFor } from "@/components/status";
import { ChangeList } from "@/components/system-grid";
import { Timeline } from "@/components/timeline";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { dateTime, inrEquivalent, money, pct, providerLabel, time } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Action, AuditLog, IncidentDetail, Snapshot, TimelineItem } from "@/types/api";

const LIVE_STATUSES = new Set(["OPEN", "INVESTIGATING", "REMEDIATING"]);
const POLICY_LABEL: Record<string, string> = {
  ALLOW: "AUTOMATICALLY APPROVED",
  HUMAN_APPROVAL_REQUIRED: "HUMAN APPROVAL REQUIRED",
  DENY: "DENIED → ESCALATED",
};

type Bundle = { incident: IncidentDetail; timeline: TimelineItem[]; audit: AuditLog[] };

function Field({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={className}>
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.1em] text-subtle">{label}</div>
      {children}
    </div>
  );
}

function StateText({ value }: { value: string | null | undefined }) {
  const tone = toneFor(value);
  return (
    <span className={cn("inline-flex items-center gap-1 font-mono text-[12px] font-semibold",
      tone === "good" ? "text-good" : tone === "critical" ? "text-critical" : tone === "warning" ? "text-warning" : "text-muted")}>
      <ToneIcon tone={tone} /> {value ?? "—"}
    </span>
  );
}

// ---- PayPal result vs PayFlow detection vs PayFlow response --------------------------------------

function Distinction({ incident }: { incident: IncidentDetail }) {
  const paypal = incident.provider === "PAYPAL_SANDBOX";
  const providerFailure = incident.failure_source === "PAYPAL_PROVIDER_FAILURE";
  const action = incident.actions.at(-1);
  const providerStatus = incident.payment.provider_status ?? incident.initial_snapshot.gateway;
  const providerOk = !providerFailure && ["COMPLETED", "SUCCESS", "REFUNDED", "APPROVED"].includes(providerStatus);
  const response = action
    ? action.status === "COMPLETED"
      ? `${action.action_type.replaceAll("_", " ")} ✓`
      : action.status === "PENDING_APPROVAL" ? "AWAITING HUMAN APPROVAL" : action.status.replaceAll("_", " ")
    : incident.status === "INVESTIGATING" ? "INVESTIGATING…" : "QUEUED";

  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
      <div className={cn("rounded-lg border px-4 py-3", providerOk ? "border-good/40 bg-good/5" : "border-critical/40 bg-critical/5")}>
        <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-subtle">
          {paypal ? "PayPal result" : "Provider result"}
        </div>
        <div className={cn("mt-1 flex items-center gap-1.5 font-mono text-lg font-semibold", providerOk ? "text-good" : "text-critical")}>
          {providerOk ? <CheckCircle2 className="size-4" /> : <XCircle className="size-4" />} {providerStatus}
        </div>
        <div className="mt-0.5 text-[11px] text-muted">
          {paypal ? "Source: PAYPAL SANDBOX" : `Source: ${providerLabel(incident.provider)}`}
          {providerFailure && " · PAYPAL_PROVIDER_FAILURE"}
        </div>
      </div>
      <div className={cn("rounded-lg border px-4 py-3",
        incident.injected_scenario ? "border-warning/50 bg-warning/5" : "border-critical/40 bg-critical/5")}>
        <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-subtle">PayFlow detected</div>
        <div className="mt-1 flex items-center gap-1.5 font-mono text-lg font-semibold text-critical">
          <XCircle className="size-4" /> {incident.injected_scenario ?? incident.type}
        </div>
        <div className="mt-0.5 text-[11px] text-muted">
          {incident.injected_scenario ? (
            <span className="text-warning">DEMO FAILURE INJECTION · PayFlow infrastructure → {incident.type}</span>
          ) : providerFailure ? (
            "Provider-side failure reported by PayPal"
          ) : (
            `${incident.type} · deterministic reconciliation`
          )}
        </div>
      </div>
      <div className={cn("rounded-lg border px-4 py-3",
        incident.status === "RESOLVED" ? "border-good/40 bg-good/5" : incident.status === "AWAITING_APPROVAL" ? "border-warning/50 bg-warning/5" : "border-border")}>
        <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-subtle">PayFlow response</div>
        <div className={cn("mt-1 font-mono text-lg font-semibold",
          incident.status === "RESOLVED" ? "text-good" : incident.status === "AWAITING_APPROVAL" ? "text-warning" : "text-foreground")}>
          {response}
        </div>
        <div className="mt-0.5 text-[11px] text-muted">
          {incident.policy_decision ? POLICY_LABEL[incident.policy_decision] ?? incident.policy_decision : "policy pending"}
          {incident.status === "RESOLVED" && " · verified · reconciled"}
        </div>
      </div>
    </div>
  );
}

// ---- system state comparison table ----------------------------------------------------------------

const SYSTEM_ROWS: { key: keyof Snapshot; label: string; source: (paypal: boolean) => string }[] = [
  { key: "gateway", label: "Payment provider", source: (p) => (p ? "PayPal Sandbox API" : "Gateway") },
  { key: "bank", label: "Settlement", source: (p) => (p ? "PayPal capture (seller balance)" : "Bank") },
  { key: "webhook", label: "Webhook", source: (p) => (p ? "PayPal webhook → PayFlow intake" : "Gateway webhook") },
  { key: "merchant", label: "Merchant order", source: () => "PayFlow merchant service" },
  { key: "ledger", label: "Ledger", source: () => "PayFlow internal ledger" },
  { key: "overall", label: "PayFlow canonical", source: () => "Payment state machine" },
];

function SystemComparison({ incident }: { incident: IncidentDetail }) {
  const paypal = incident.provider === "PAYPAL_SANDBOX";
  const final = incident.final_snapshot ?? incident.current_snapshot;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>System</TableHead>
          <TableHead>Source of truth</TableHead>
          <TableHead>At detection</TableHead>
          <TableHead>{incident.status === "RESOLVED" ? "Final (verified)" : "Now"}</TableHead>
          <TableHead />
        </TableRow>
      </TableHeader>
      <TableBody>
        {SYSTEM_ROWS.map((row) => {
          const before = incident.initial_snapshot[row.key];
          const after = final[row.key];
          const providerRow = row.key === "gateway" && paypal;
          return (
            <TableRow key={row.key}>
              <TableCell className="text-xs text-foreground">{row.label}</TableCell>
              <TableCell className="text-[11px] text-muted">{row.source(paypal)}</TableCell>
              <TableCell><StateText value={providerRow ? `${before} (${incident.payment.provider_status ?? ""})` : before} /></TableCell>
              <TableCell><StateText value={after} /></TableCell>
              <TableCell className="text-[11px]">
                {before && after && before !== after ? <span className="text-good">changed by PayFlow</span> : null}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

// ---- AI, RAG, policy, action ---------------------------------------------------------------------

function AIPanel({ incident }: { incident: IncidentDetail }) {
  const inv = incident.investigation;
  if (!inv) {
    return (
      <Card>
        <CardHeader><CardTitle className="flex items-center gap-1.5"><Bot className="size-3.5" /> AI investigation</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          <p className="text-sm text-muted">
            {incident.status === "INVESTIGATING" ? "Collecting evidence from PayPal, settlement, merchant, ledger, webhooks and historical incidents…" : "Queued for investigation."}
          </p>
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </CardContent>
      </Card>
    );
  }
  const rec = inv.recommendation;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5"><Bot className="size-3.5" /> AI investigation</CardTitle>
        <Badge tone={inv.used_fallback ? "neutral" : "info"} title={inv.evidence.fallback_reason ?? undefined}>
          {inv.used_fallback ? "Deterministic fallback" : inv.model.replace("groq:", "Groq · ")}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field label="Root cause">
          <p className="text-[15px] font-medium leading-snug text-foreground">{rec.rootCause.replace(/\.$/, "")}.</p>
          <p className="mt-1 text-xs leading-relaxed text-muted">{rec.summary}</p>
        </Field>
        <div className="grid grid-cols-3 gap-3">
          <Field label="Confidence">
            <div className="font-mono text-xl font-semibold text-foreground">{pct(rec.confidence)}</div>
            <div className="mt-1 h-1 rounded-full bg-panel-2">
              <div className="h-1 rounded-full bg-primary" style={{ width: `${rec.confidence * 100}%` }} />
            </div>
          </Field>
          <Field label="Recommended action">
            <div className="font-mono text-[13px] font-semibold text-foreground">{rec.recommendedAction}</div>
          </Field>
          <Field label="Risk"><StatusBadge status={rec.risk} /></Field>
        </div>
        <Field label="Evidence">
          <ul className="space-y-1">
            {rec.evidence.map((e) => (
              <li key={e} className="flex items-start gap-2 font-mono text-xs text-foreground">
                <CheckCircle2 className="mt-px size-3.5 shrink-0 text-good" /> {e}
              </li>
            ))}
          </ul>
        </Field>
        {rec.confidenceRationale && (
          <Field label="Why this confidence"><p className="text-xs text-muted">{rec.confidenceRationale}</p></Field>
        )}
        {rec.impact && (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {[
              ["Customer impact", rec.impact.customerImpact],
              ["Financial exposure", rec.impact.financialExposure],
              ["Blast radius", rec.impact.blastRadius],
              ["Urgency", rec.impact.urgency],
            ].filter(([, v]) => v).map(([label, value]) => (
              <div key={label} className="rounded-lg border border-border bg-panel-2 px-3 py-2">
                <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-subtle">{label}</div>
                <div className="mt-0.5 text-xs text-foreground">{value}</div>
              </div>
            ))}
          </div>
        )}
        {!!rec.remediationPlan?.length && (
          <Field label="Remediation plan">
            <ol className="space-y-1.5">
              {rec.remediationPlan.map((step, i) => (
                <li key={step} className="flex gap-2 text-xs text-foreground">
                  <span className="flex size-4 shrink-0 items-center justify-center rounded-full bg-navy text-[10px] font-bold text-white">{i + 1}</span>
                  {step}
                </li>
              ))}
            </ol>
          </Field>
        )}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {!!rec.contributingFactors?.length && (
            <Field label="Contributing factors">
              <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted">{rec.contributingFactors.map((f) => <li key={f}>{f}</li>)}</ul>
            </Field>
          )}
          {!!rec.preventiveMeasures?.length && (
            <Field label="Prevent recurrence">
              <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted">{rec.preventiveMeasures.map((f) => <li key={f}>{f}</li>)}</ul>
            </Field>
          )}
        </div>
        {!!rec.anomalies?.length && (
          <Field label="Anomalies spotted">
            <ul className="space-y-0.5 text-xs text-warning">{rec.anomalies.map((a) => <li key={a}>⚠ {a}</li>)}</ul>
          </Field>
        )}
        <p className="border-t border-border pt-3 text-[11px] leading-relaxed text-subtle">
          The AI only recommends; it cannot move money or change state. {inv.evidence.tool_calls?.length ?? 0} read-only
          tools{inv.latency_ms !== null && ` · ${inv.latency_ms}ms`}. Stored: summary, evidence and decision only. No
          chain-of-thought.
        </p>
      </CardContent>
    </Card>
  );
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "";
  if (seconds < 120) return `${seconds} sec`;
  return `${Math.round(seconds / 60)} min`;
}

function SimilarIncidents({ incident }: { incident: IncidentDetail }) {
  const matches = incident.investigation?.historical_matches ?? [];
  if (!matches.length) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5"><History className="size-3.5" /> Similar historical incidents</CardTitle>
        <span className="text-[10px] uppercase tracking-wider text-subtle">Historical PayFlow · {matches[0].retrieval}</span>
      </CardHeader>
      <CardContent className="space-y-2">
        {matches.map((m) => (
          <div key={m.id} className="flex items-start justify-between gap-3 rounded-md border border-border bg-background/60 px-3 py-2">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-mono text-[11px] text-subtle">{m.reference}</span>
                <span className="truncate text-sm text-foreground">{m.title}</span>
              </div>
              <div className="text-[11px] text-muted">
                {m.resolution_mode === "AUTOMATIC"
                  ? `Resolved automatically in ${formatDuration(m.resolved_in_seconds)}`
                  : `Required human intervention (${formatDuration(m.resolved_in_seconds)})`}{" "}
                · {m.resolution}
              </div>
            </div>
            <span className="font-mono text-sm font-semibold text-foreground">{pct(m.similarity)}</span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function PolicyPanel({ incident, action }: { incident: IncidentDetail; action: Action }) {
  const policy = action.policy.approval_recheck ?? action.policy;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5"><Scale className="size-3.5" /> Policy decision</CardTitle>
        {incident.policy_decision && <StatusBadge status={incident.policy_decision} label={POLICY_LABEL[incident.policy_decision]} />}
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Action"><div className="font-mono text-[13px] font-semibold">{action.action_type}</div></Field>
          <Field label="Decided by">
            <div className="text-xs text-foreground">Deterministic policy engine{action.approved_by ? ` + ${action.approved_by}` : ""}</div>
          </Field>
        </div>
        <ul className="space-y-1.5">
          {(policy.checks ?? []).map((c) => (
            <li key={c.name} className="flex items-start gap-2 text-xs">
              {c.passed ? <CheckCircle2 className="mt-px size-3.5 shrink-0 text-good" /> : <XCircle className="mt-px size-3.5 shrink-0 text-critical" />}
              <span className="font-mono text-foreground">{c.name}</span>
              <span className="ml-auto font-mono text-subtle">{c.detail}</span>
            </li>
          ))}
        </ul>
        {!!policy.reasons?.length && <Field label="Reason"><p className="text-xs text-muted">{policy.reasons.join(" · ")}</p></Field>}
        {action.policy.overridden_ai_action && (
          <p className="rounded-lg border border-warning/40 bg-warning/5 px-3 py-2 text-xs text-warning">
            Guardrail PB-001: the AI recommended <b className="font-mono">{action.policy.overridden_ai_action}</b>, which is not a
            valid fix for {incident.type}. Policy replaced it with the playbook action <b className="font-mono">{action.action_type}</b>.
          </p>
        )}
        {(!!action.policy.fired_rules?.length || action.policy.policy_version) && (
          <div className="flex flex-wrap items-center gap-1">
            {action.policy.fired_rules?.map((r) => (
              <span key={r} className="rounded bg-navy px-1.5 py-px font-mono text-[10px] text-white">{r}</span>
            ))}
            {action.policy.policy_version && <span className="ml-auto font-mono text-[10px] text-subtle">policy {action.policy.policy_version}</span>}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ActionPanel({ action, onDecided }: { action: Action; onDecided: () => void }) {
  const verification = action.result?.verification;
  return (
    <>
      <Card className={cn(action.status === "PENDING_APPROVAL" && "border-warning/50")}>
        <CardHeader>
          <CardTitle className="flex items-center gap-1.5"><Play className="size-3.5" /> Action execution</CardTitle>
          <StatusBadge status={action.status} />
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Idempotency key">
              <div className="flex items-center gap-1 font-mono text-xs text-muted"><KeyRound className="size-3" /> {action.idempotency_key}</div>
            </Field>
            <Field label="Approved by">
              <div className="flex items-center gap-1 font-mono text-xs">
                {action.approved_by ? (<><UserCheck className="size-3 text-good" /> {action.approved_by}</>) : "policy (automatic)"}
              </div>
            </Field>
          </div>
          {action.status === "PENDING_APPROVAL" && (
            <div className="rounded-md border border-warning/40 bg-warning/5 p-3">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-warning">Human approval required</div>
              <ApprovalActions actionId={action.id} onDone={onDecided} />
            </div>
          )}
          {action.result?.summary && <p className="text-sm text-foreground">{action.result.summary}</p>}
          {action.result?.changes && <Field label="State changes"><ChangeList changes={action.result.changes} /></Field>}
          {action.result?.error && <p className="text-sm text-critical">{action.result.error}</p>}
        </CardContent>
      </Card>
      {verification && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-1.5"><ShieldCheck className="size-3.5" /> Verification</CardTitle>
            <StatusBadge status={verification.status} label={`VERIFICATION ${verification.status.replace("_", " ")}`} />
          </CardHeader>
          <CardContent>
            <ul className="space-y-1">
              {verification.checks.map((c) => (
                <li key={c.name} className="flex items-center gap-2 font-mono text-xs">
                  {c.passed ? <CheckCircle2 className="size-3.5 text-good" /> : <XCircle className="size-3.5 text-critical" />}
                  <span className={cn(c.name.startsWith("PayPal") ? "text-info" : "text-muted")}>{c.name}</span>
                  <span className="ml-auto text-foreground">{c.actual}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[11px] text-subtle">Blue checks are re-read from the PayPal Sandbox API, not PayFlow&apos;s database.</p>
          </CardContent>
        </Card>
      )}
    </>
  );
}

const ACTIVE = new Set(["ESCALATED", "OPEN", "AWAITING_APPROVAL", "INVESTIGATING", "REMEDIATING"]);
const RESOLUTION_LABEL: Record<string, string> = {
  AUTOMATED: "Resolved autonomously", HUMAN_APPROVED: "Resolved after human approval", MANUAL: "Resolved manually",
  ACCEPTED_RISK: "Resolved with accepted risk", FALSE_POSITIVE: "Closed as false positive",
};

function HumanResolution({ incident, onDone }: { incident: IncidentDetail; onDone: () => void }) {
  const [by, setBy] = useState("ops.manager");
  const [note, setNote] = useState("");
  const [acceptRisk, setAcceptRisk] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  if (!ACTIVE.has(incident.status)) {
    return (
      <Card className="border-good/40">
        <CardHeader>
          <CardTitle className="flex items-center gap-1.5"><CheckCircle2 className="size-3.5 text-good" /> Outcome</CardTitle>
          <StatusBadge status={incident.status} />
        </CardHeader>
        <CardContent className="space-y-1 text-xs text-muted">
          <div className="text-sm font-semibold text-foreground">{RESOLUTION_LABEL[incident.resolution ?? ""] ?? incident.status}</div>
          {incident.resolution_note && <p>“{incident.resolution_note}”</p>}
          {incident.acknowledged_by && <p>Owner: {incident.acknowledged_by}</p>}
        </CardContent>
      </Card>
    );
  }

  async function run(kind: string, fn: () => Promise<{ message?: string; incident_status?: string }>) {
    setBusy(kind);
    setMessage(null);
    try {
      const r = await fn();
      setMessage({ ok: true, text: r.message ?? `Done: incident is now ${r.incident_status ?? "updated"}` });
      onDone();
    } catch (err) {
      setMessage({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  }

  const escalated = incident.status === "ESCALATED";
  return (
    <Card className={cn(escalated ? "border-critical/50" : "border-warning/40")}>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5"><Hand className="size-3.5" /> Human resolution</CardTitle>
        {incident.acknowledged_by ? <Badge tone="info">owner · {incident.acknowledged_by}</Badge> : <Badge tone="warning">unowned</Badge>}
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted">
          {escalated
            ? "Automation stopped and handed this incident to a human. Choose how to close it; every option is policy-checked and audited."
            : incident.status === "AWAITING_APPROVAL"
              ? "Approve or reject the proposed action below, or take over and resolve the incident yourself."
              : "The agent is still working. You can take ownership or override it."}
        </p>
        <div className="grid grid-cols-2 gap-2">
          <Input aria-label="Operator" value={by} onChange={(e) => setBy(e.target.value)} className="font-mono text-xs" />
          {!incident.acknowledged_by && (
            <Button variant="outline" size="sm" disabled={!!busy || by.length < 2}
              onClick={() => run("ack", async () => { await api.acknowledgeIncident(incident.id, by); return { message: `Acknowledged by ${by}` }; })}>
              <UserCheck /> {busy === "ack" ? "…" : "Take ownership"}
            </Button>
          )}
        </div>
        {escalated && (
          <Button size="sm" className="w-full" disabled={!!busy || by.length < 2}
            onClick={() => run("retry", () => api.retryIncident(incident.id, by, note || undefined))}>
            <RotateCcw /> {busy === "retry" ? "Running playbook…" : "Approve & retry automated fix"}
          </Button>
        )}
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2}
          placeholder="Resolution note (what you checked / why)…"
          className="w-full rounded-md border border-border-strong bg-panel px-3 py-2 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40" />
        <label className="flex items-start gap-2 text-[11px] text-muted">
          <input type="checkbox" checked={acceptRisk} onChange={(e) => setAcceptRisk(e.target.checked)} className="mt-0.5" />
          Accept residual risk if systems still disagree (recorded as ACCEPTED_RISK)
        </label>
        <div className="grid grid-cols-2 gap-2">
          <Button variant="success" size="sm" disabled={!!busy || note.length < 3 || by.length < 2}
            onClick={() => run("resolve", () => api.resolveIncident(incident.id, by, note, acceptRisk))}>
            <CheckCircle2 /> {busy === "resolve" ? "…" : "Resolve"}
          </Button>
          <Button variant="outline" size="sm" disabled={!!busy || by.length < 2}
            onClick={() => run("close", () => api.closeIncident(incident.id, by, note || "False positive"))}>
            <XCircle /> {busy === "close" ? "…" : "Close · false positive"}
          </Button>
        </div>
        {message && <p className={cn("rounded-md px-2.5 py-1.5 text-xs", message.ok ? "bg-good/10 text-good" : "bg-critical/5 text-critical")}>{message.text}</p>}
      </CardContent>
    </Card>
  );
}

export default function IncidentPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [investigating, setInvestigating] = useState(false);
  const { data, error, refresh } = useApi<Bundle>(
    async () => {
      const [incident, timeline, audit] = await Promise.all([api.incident(id), api.timeline(id), api.incidentAudit(id)]);
      return { incident, timeline, audit };
    },
    [id],
    (d) => (!d || LIVE_STATUSES.has(d.incident.status) ? 2500 : 15000),
  );
  useLiveRefresh(refresh, (e) => !!data && (e.incidentId === data.incident.id || e.transactionId === data.incident.transaction_id));

  if (error && !data) return <ErrorBanner message={error} />;
  if (!data) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24" />
        <Skeleton className="h-40" />
        <Skeleton className="h-96" />
      </div>
    );
  }

  const { incident, timeline, audit } = data;
  const action = incident.actions.at(-1);
  const live = LIVE_STATUSES.has(incident.status);
  const snap = incident.current_snapshot;

  async function investigate() {
    setInvestigating(true);
    try {
      await api.investigate(incident.id);
      await refresh();
    } finally {
      setInvestigating(false);
    }
  }

  return (
    <div className="space-y-4">
      <Link href="/incidents" className="inline-flex items-center gap-1 text-xs text-muted hover:text-foreground">
        <ArrowLeft className="size-3.5" /> Incidents
      </Link>

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4 px-5 py-4">
          <div>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
              <span className="font-mono font-semibold text-foreground">INCIDENT #{incident.incident_number}</span>
              <span className="font-mono">{incident.type}</span>
              <SeverityBadge severity={incident.severity} />
              <Badge tone={incident.provider === "PAYPAL_SANDBOX" ? "info" : "neutral"}>{providerLabel(incident.provider)}</Badge>
              {incident.injected_scenario && (
                <Badge tone="warning"><FlaskConical /> demo injection</Badge>
              )}
            </div>
            <div className="mt-3 grid grid-cols-2 gap-x-8 gap-y-2 sm:grid-cols-3 lg:grid-cols-6">
              <Field label="Transaction">
                <Link href={`/payments/${incident.transaction_id}`} className="font-mono text-lg font-semibold hover:text-primary">
                  {incident.transaction_id}
                </Link>
              </Field>
              <Field label="Amount">
                <div className="text-lg font-semibold">{money(incident.amount, incident.currency)}</div>
                {inrEquivalent(incident.amount, incident.currency) && (
                  <div className="text-[10px] text-subtle">{inrEquivalent(incident.amount, incident.currency)}</div>
                )}
              </Field>
              <Field label="Provider"><div className="text-sm">{providerLabel(incident.provider)}</div></Field>
              <Field label="Provider status"><StateText value={incident.payment.provider_status ?? snap.gateway} /></Field>
              <Field label="Merchant status"><StateText value={snap.merchant} /></Field>
              <Field label="Ledger status"><StateText value={snap.ledger} /></Field>
            </div>
            <div className="mt-2 text-xs text-subtle">
              Detected {dateTime(incident.created_at)}
              {incident.resolved_at && ` · resolved ${time(incident.resolved_at)}`}
              {incident.payment.provider_order_id && ` · PayPal order ${incident.payment.provider_order_id}`}
              {incident.payment.provider_capture_id && ` · capture ${incident.payment.provider_capture_id}`}
            </div>
          </div>
          <div className="flex flex-col items-end gap-2">
            <StatusBadge status={incident.status} className="px-2.5 py-1 text-sm [&_svg]:size-4" />
            {incident.status === "OPEN" && !incident.investigation && (
              <Button size="sm" onClick={investigate} disabled={investigating}>
                <Play /> {investigating ? "Queuing…" : "Investigate"}
              </Button>
            )}
            {incident.status === "AWAITING_APPROVAL" && (
              <Button asChild size="sm" variant="outline"><Link href="/approvals">Approval queue <ArrowRight /></Link></Button>
            )}
            {live && (
              <span className="flex items-center gap-1.5 text-[11px] text-info">
                <CircleDashed className="size-3 animate-spin" /> pipeline running
              </span>
            )}
          </div>
        </div>
        <div className="border-t border-border px-5 py-4"><LifecycleStepper incident={incident} /></div>
      </Card>

      <Distinction incident={incident} />

      <Card>
        <CardHeader>
          <CardTitle>Agent path</CardTitle>
          <span className="text-[10px] uppercase tracking-wider text-subtle">LangGraph · {incident.agent_trace.length} steps</span>
        </CardHeader>
        <CardContent><AgentGraph trace={incident.agent_trace} engine="langgraph" /></CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>System state comparison</CardTitle></CardHeader>
        <SystemComparison incident={incident} />
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        <div className="space-y-4 lg:col-span-3">
          <AIPanel incident={incident} />
          <SimilarIncidents incident={incident} />
          {action && <PolicyPanel incident={incident} action={action} />}
          {action && <ActionPanel action={action} onDecided={() => refresh()} />}
        </div>
        <div className="space-y-4 lg:col-span-2">
          <HumanResolution incident={incident} onDone={() => refresh()} />
          {incident.risk_factors && (
            <Card>
              <CardHeader><CardTitle>Decision risk</CardTitle></CardHeader>
              <CardContent><RiskMeter risk={incident.risk_factors} /></CardContent>
            </Card>
          )}
          <Card>
            <CardHeader>
              <CardTitle>Timeline</CardTitle>
              <span className="text-[11px] text-subtle">{timeline.length} events · live</span>
            </CardHeader>
            <CardContent><Timeline items={timeline} live={live} /></CardContent>
          </Card>
          {incident.failure_injections.length > 0 && (
            <Card className="border-warning/40">
              <CardHeader>
                <CardTitle className="flex items-center gap-1.5 text-warning"><FlaskConical className="size-3.5" /> Demo failure injection</CardTitle>
              </CardHeader>
              <CardContent className="space-y-1.5">
                {incident.failure_injections.map((f) => (
                  <div key={f.id} className="flex items-center justify-between text-xs">
                    <span className="font-mono text-foreground">{f.scenario}</span>
                    <span className="text-muted">{f.injected_at ? `triggered ${time(f.injected_at)}` : "armed"}</span>
                  </div>
                ))}
                <p className="pt-1 text-[11px] text-subtle">Injected by PayFlow into its own systems. PayPal was not affected.</p>
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Audit trail</CardTitle>
          <span className="text-[11px] text-subtle">append-only · {audit.length} records</span>
        </CardHeader>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Time</TableHead>
              <TableHead>Event</TableHead>
              <TableHead>Actor</TableHead>
              <TableHead>Reason</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {audit.map((log) => (
              <TableRow key={log.id}>
                <TableCell className="whitespace-nowrap font-mono text-xs text-muted">{time(log.created_at)}</TableCell>
                <TableCell className="font-mono text-[11px] text-foreground">{log.event}</TableCell>
                <TableCell className="whitespace-nowrap font-mono text-[11px] text-muted">{log.actor}</TableCell>
                <TableCell className="text-xs text-muted">{log.reason}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
