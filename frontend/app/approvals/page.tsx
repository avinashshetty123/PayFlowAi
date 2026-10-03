"use client";

import { ArrowRight, Bot, CheckCircle2, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ErrorBanner, PageHeader } from "@/components/app-shell";
import { ApprovalActions } from "@/components/approval-actions";
import { useLiveRefresh } from "@/components/event-stream";
import { StatusBadge } from "@/components/status";
import { SystemGrid } from "@/components/system-grid";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { dateTime, money, pct, providerLabel } from "@/lib/format";
import type { ActionDecision, ApprovalItem } from "@/types/api";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border py-2 last:border-0">
      <span className="text-[11px] font-medium uppercase tracking-wider text-subtle">{label}</span>
      <span className="text-right">{children}</span>
    </div>
  );
}

function ApprovalCard({ item, onDone }: { item: ApprovalItem; onDone: (r: ActionDecision) => void }) {
  const reasons = item.action.policy.reasons ?? [];
  return (
    <Card className="border-warning/50">
      <div className="flex items-center gap-2 border-b border-warning/30 bg-warning/5 px-5 py-3 text-sm font-semibold tracking-wide text-warning">
        <ShieldAlert className="size-4" />
        HUMAN APPROVAL REQUIRED
        <span className="ml-auto font-mono text-xs font-normal text-muted">{item.incident_number}</span>
      </div>
      <CardContent className="grid grid-cols-1 gap-6 pt-4 lg:grid-cols-2">
        <div>
          <Row label="Transaction">
            <Link href={`/incidents/${item.incident_id}`} className="font-mono text-sm font-semibold hover:text-primary">
              {item.transaction_id}
            </Link>
          </Row>
          <Row label="Amount">
            <span className="font-mono text-lg font-semibold">{money(item.amount, item.currency)}</span>
          </Row>
          <Row label="Provider">
            <span className="text-xs">{providerLabel(item.provider)}</span>
          </Row>
          <Row label="Incident">
            <span className="font-mono text-xs">{item.incident_type}</span>
          </Row>
          <Row label="AI recommendation">
            <span className="font-mono text-sm font-semibold">{item.action.action_type}</span>
          </Row>
          <Row label="Confidence">
            <span className="font-mono text-sm">{pct(item.confidence)}</span>
          </Row>
          <Row label="Risk">
            <StatusBadge status={item.risk} />
          </Row>
          <Row label="Policy">
            <StatusBadge status="HUMAN_APPROVAL_REQUIRED" label="Human approval required" />
          </Row>
        </div>
        <div className="space-y-4">
          <div>
            <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-subtle">
              <Bot className="size-3.5" /> AI root cause
            </div>
            <p className="text-sm text-foreground">{item.root_cause}.</p>
            {item.ai_summary && <p className="mt-1 text-xs text-muted">{item.ai_summary}</p>}
          </div>
          <div>
            <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-subtle">Why a human is needed</div>
            <ul className="space-y-1 text-xs text-muted">
              {reasons.map((r) => (
                <li key={r}>• {r}</li>
              ))}
            </ul>
          </div>
          <SystemGrid current={item.snapshot} compact />
          <div className="border-t border-border pt-4">
            <ApprovalActions actionId={item.action.id} onDone={onDone} size="lg" />
            <p className="mt-2 text-[11px] text-subtle">
              Approval triggers a policy re-check, then execution, verification and an audit record. Idempotency key{" "}
              <span className="font-mono">{item.action.idempotency_key}</span> prevents double execution.
              {item.provider === "PAYPAL_SANDBOX" && item.action.action_type === "REFUND" && (
                <>
                  {" "}Approving calls the <b>PayPal Sandbox refund API</b> for capture{" "}
                  <span className="font-mono">{item.provider_capture_id}</span> with PayPal-Request-Id{" "}
                  <span className="font-mono">paypal:refund:{item.provider_capture_id}</span>.
                </>
              )}
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export default function ApprovalsPage() {
  const pending = useApi(() => api.approvals("pending"), [], 20000);
  const escalated = useApi(() => api.incidents({ status: "ESCALATED", limit: 50 }), [], 20000);
  useLiveRefresh(escalated.refresh, (e) => e.event.startsWith("INCIDENT"));
  const decided = useApi(() => api.approvals("decided"), [], 30000);
  useLiveRefresh(() => {
    pending.refresh();
    decided.refresh();
  }, (e) => e.event.startsWith("ACTION") || e.event.startsWith("INCIDENT"));
  const [last, setLast] = useState<ActionDecision | null>(null);

  function done(result: ActionDecision) {
    setLast(result);
    pending.refresh();
    decided.refresh();
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Approvals"
        description="Actions the policy engine will not run automatically. The AI cannot override this queue."
      />
      {pending.error && <ErrorBanner message={pending.error} />}

      {last && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-good/40 bg-good/5 px-4 py-3 text-sm">
          <CheckCircle2 className="size-4 text-good" />
          <span className="text-foreground">
            {last.action.action_type} {last.action.status.toLowerCase()} · {last.message}
          </span>
          {last.verification && <StatusBadge status={last.verification.status} label={`Verification ${last.verification.status}`} />}
          <StatusBadge status={last.incident_status} />
          <Link href={`/incidents/${last.action.incident_id}`} className="ml-auto flex items-center gap-1 text-xs text-muted hover:text-foreground">
            Open incident <ArrowRight className="size-3" />
          </Link>
        </div>
      )}

      {!!escalated.data?.items.length && (
        <Card className="border-critical/40 overflow-hidden">
          <CardHeader>
            <CardTitle className="text-critical">Needs human resolution · {escalated.data.items.length}</CardTitle>
            <span className="text-[11px] text-muted">automation stopped; open an incident to retry, resolve or close it</span>
          </CardHeader>
          <Table>
            <TableBody>
              {escalated.data.items.map((i) => (
                <TableRow key={i.id}>
                  <TableCell>
                    <Link href={`/incidents/${i.id}`} className="font-mono text-[13px] font-semibold hover:text-info">{i.incident_number}</Link>
                    <div className="text-[11px] text-subtle">{i.transaction_id} · {i.type}</div>
                  </TableCell>
                  <TableCell className="text-right font-mono text-[13px]">{money(i.amount, i.currency)}</TableCell>
                  <TableCell className="text-xs text-muted">{i.acknowledged_by ? `owner ${i.acknowledged_by}` : "unowned"}</TableCell>
                  <TableCell className="text-right">
                    <Link href={`/incidents/${i.id}`} className="inline-flex items-center gap-1 rounded-full bg-navy px-3 py-1 text-[11px] font-medium text-white">
                      Resolve <ArrowRight className="size-3" />
                    </Link>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      {pending.data && pending.data.length === 0 && (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted">
            No actions awaiting approval. Start the &quot;Refund requires approval&quot; live demo to create one.
          </CardContent>
        </Card>
      )}
      {pending.data?.map((item) => (
        <ApprovalCard key={item.action.id} item={item} onDone={done} />
      ))}

      <Card>
        <CardHeader>
          <CardTitle>Decision history</CardTitle>
        </CardHeader>
        {decided.data && decided.data.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Transaction</TableHead>
                <TableHead className="text-right">Amount</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Decision</TableHead>
                <TableHead>By</TableHead>
                <TableHead>When</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {decided.data.map((item) => (
                <TableRow key={item.action.id}>
                  <TableCell>
                    <Link href={`/incidents/${item.incident_id}`} className="font-mono text-[13px] hover:text-primary">
                      {item.transaction_id}
                    </Link>
                  </TableCell>
                  <TableCell className="text-right font-mono text-[13px]">{money(item.amount, item.currency)}</TableCell>
                  <TableCell className="font-mono text-xs">{item.action.action_type}</TableCell>
                  <TableCell>
                    <StatusBadge status={item.action.status} />
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted">{item.action.approved_by}</TableCell>
                  <TableCell className="text-xs text-muted">{dateTime(item.action.completed_at ?? item.action.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <CardContent className="text-sm text-muted">No human decisions yet.</CardContent>
        )}
      </Card>
    </div>
  );
}
