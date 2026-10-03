"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import Link from "next/link";
import { Fragment, useState } from "react";

import { ErrorBanner, PageHeader } from "@/components/app-shell";
import { AuditIntegrityBanner } from "@/components/recon-insights";
import { useLiveRefresh } from "@/components/event-stream";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";

const EVENTS = [
  "PAYMENT_CREATED", "PAYMENT_EVENT_RECEIVED", "MISMATCH_DETECTED", "INCIDENT_CREATED", "INVESTIGATION_STARTED",
  "HISTORICAL_MATCH_FOUND", "AI_RECOMMENDATION_CREATED", "POLICY_EVALUATED", "APPROVAL_REQUESTED", "ACTION_APPROVED",
  "ACTION_REJECTED", "ACTION_EXECUTED", "ACTION_FAILED", "ACTION_DEDUPLICATED", "VERIFICATION_PASSED",
  "VERIFICATION_FAILED", "INCIDENT_RESOLVED", "INCIDENT_ESCALATED", "JOB_FAILED", "NOTIFICATION_SENT",
  "PAYMENT_APPROVAL_STARTED", "PAYMENT_APPROVED", "PAYMENT_CAPTURE_STARTED", "PAYMENT_CAPTURE_COMPLETED",
  "PAYMENT_CAPTURE_FAILED", "PROVIDER_CALL_FAILED", "WEBHOOK_RECEIVED", "WEBHOOK_VERIFIED", "WEBHOOK_REJECTED",
  "WEBHOOK_DUPLICATE", "WEBHOOK_DELAYED", "WEBHOOK_DROPPED", "FAILURE_INJECTED", "DOWNSTREAM_UPDATED",
  "REFUND_REQUESTED", "RECONCILIATION_STARTED", "RECONCILIATION_COMPLETED", "ACTION_CREATED",
  "VERIFICATION_STARTED", "VERIFICATION_RETRY",
];

export default function AuditPage() {
  const [event, setEvent] = useState("");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const { data, error, refresh } = useApi(() => api.audit({ limit: 200, event, search }), [event, search], 20000);
  useLiveRefresh(refresh);

  return (
    <div>
      <PageHeader
        title="Audit log"
        description="Append-only record of every important operation: actor, reason, evidence, result, timestamp."
      />
      <AuditIntegrityBanner />
      {error && <ErrorBanner message={error} />}
      <div className="mb-3 flex flex-wrap gap-2">
        <Input placeholder="Search transaction, actor, reason…" value={search} onChange={(e) => setSearch(e.target.value)} className="max-w-xs" />
        <Select value={event} onChange={(e) => setEvent(e.target.value)} className="w-64 font-mono text-xs">
          <option value="">All events</option>
          {EVENTS.map((e) => (
            <option key={e} value={e}>
              {e}
            </option>
          ))}
        </Select>
        {data && <span className="ml-auto self-center text-xs text-subtle">{data.total} records</span>}
      </div>
      <Card>
        {!data ? (
          <Skeleton className="m-5 h-64" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-6" />
                <TableHead>Time</TableHead>
                <TableHead>Transaction</TableHead>
                <TableHead>Event</TableHead>
                <TableHead>Actor</TableHead>
                <TableHead>Reason</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.items.map((log) => {
                const open = expanded === log.id;
                return (
                  <Fragment key={log.id}>
                    <TableRow className="cursor-pointer hover:bg-panel-2/60" onClick={() => setExpanded(open ? null : log.id)}>
                      <TableCell>
                        {open ? <ChevronDown className="size-3.5 text-muted" /> : <ChevronRight className="size-3.5 text-subtle" />}
                      </TableCell>
                      <TableCell className="whitespace-nowrap font-mono text-xs text-muted">{dateTime(log.created_at)}</TableCell>
                      <TableCell className="font-mono text-xs">
                        {log.incident_id ? (
                          <Link href={`/incidents/${log.incident_id}`} onClick={(e) => e.stopPropagation()} className="hover:text-primary">
                            {log.transaction_id}
                          </Link>
                        ) : (
                          log.transaction_id
                        )}
                      </TableCell>
                      <TableCell className="font-mono text-[11px]">{log.event}</TableCell>
                      <TableCell className="whitespace-nowrap font-mono text-[11px] text-muted">{log.actor}</TableCell>
                      <TableCell className="max-w-md truncate text-xs text-muted">{log.reason}</TableCell>
                    </TableRow>
                    {open && (
                      <TableRow>
                        <TableCell colSpan={6} className="bg-background/60">
                          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                            {(["evidence", "result"] as const).map((k) => (
                              <div key={k}>
                                <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-subtle">{k}</div>
                                <pre className="max-h-72 overflow-auto rounded border border-border bg-panel p-2 font-mono text-[11px] text-muted">
                                  {JSON.stringify(log[k], null, 2)}
                                </pre>
                              </div>
                            ))}
                          </div>
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                );
              })}
            </TableBody>
          </Table>
        )}
      </Card>
    </div>
  );
}
