"use client";

import { GitCompareArrows } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ErrorBanner, PageHeader } from "@/components/app-shell";
import { useLiveRefresh } from "@/components/event-stream";
import { StatusBadge, ToneIcon, toneFor } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { cn } from "@/lib/utils";

const SYSTEMS = ["gateway", "bank", "merchant", "ledger", "webhook"] as const;

function Cell({ value }: { value: string }) {
  const tone = toneFor(value);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 font-mono text-[11px]",
        tone === "good" ? "text-good" : tone === "critical" ? "text-[#f87171]" : tone === "warning" ? "text-warning" : "text-muted",
      )}
    >
      <ToneIcon tone={tone} />
      {value}
    </span>
  );
}

export default function ReconciliationPage() {
  const { data, error, refresh } = useApi(api.reconciliation, [], 20000);
  useLiveRefresh(refresh, (e) => e.event.startsWith("RECONCILIATION") || e.event.startsWith("INCIDENT"));
  const [running, setRunning] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function run() {
    setRunning(true);
    try {
      const result = await api.runReconciliation();
      setMessage(
        `Checked ${result.checked} payments · ${result.new_incidents.length} incident(s) opened or re-queued` +
          (result.new_incidents.length ? `: ${result.new_incidents.map((i) => i.incident_number).join(", ")}` : ""),
      );
      await refresh();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  }

  const s = data?.summary;
  return (
    <div>
      <PageHeader
        title="Reconciliation"
        description="Deterministic five-way comparison. No AI is involved in detection: rules compare gateway, bank, merchant, ledger and webhook."
        actions={
          <Button size="sm" onClick={run} disabled={running}>
            <GitCompareArrows /> {running ? "Reconciling…" : "Run reconciliation"}
          </Button>
        }
      />
      {error && <ErrorBanner message={error} />}
      {message && <p className="mb-3 text-xs text-muted">{message}</p>}

      {s && (
        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          {[
            { label: "Checked", value: s.checked },
            { label: "Consistent", value: s.consistent, accent: "text-good" },
            { label: "Mismatched now", value: s.mismatched, accent: s.mismatched ? "text-[#f87171]" : "" },
            { label: "Remediated", value: s.remediated, accent: "text-good" },
          ].map((stat) => (
            <Card key={stat.label} className="px-4 py-3">
              <div className="text-[11px] uppercase tracking-wider text-subtle">{stat.label}</div>
              <div className={cn("mt-1 text-2xl font-semibold", stat.accent)}>{stat.value}</div>
            </Card>
          ))}
        </div>
      )}

      <Card>
        {!data ? (
          <Skeleton className="m-5 h-64" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Transaction</TableHead>
                <TableHead className="text-right">Amount</TableHead>
                {SYSTEMS.map((sys) => (
                  <TableHead key={sys}>{sys}</TableHead>
                ))}
                <TableHead>Result</TableHead>
                <TableHead>Incident</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.rows.map((row) => (
                <TableRow key={row.transaction_id} className={cn(!row.consistent && "bg-critical/5")}>
                  <TableCell>
                    <Link href={`/payments/${row.transaction_id}`} className="font-mono text-[13px] hover:text-primary">
                      {row.transaction_id}
                    </Link>
                  </TableCell>
                  <TableCell className="text-right font-mono text-[13px]">{money(row.amount, row.currency)}</TableCell>
                  {SYSTEMS.map((sys) => (
                    <TableCell key={sys}>
                      <Cell value={row.snapshot[sys]} />
                    </TableCell>
                  ))}
                  <TableCell>
                    {row.consistent ? (
                      <StatusBadge status="PASSED" label="CONSISTENT" />
                    ) : (
                      <StatusBadge status="FAILED" label={row.mismatch_type ?? "MISMATCH"} />
                    )}
                  </TableCell>
                  <TableCell>
                    {row.incident_id ? (
                      <Link href={`/incidents/${row.incident_id}`} className="flex items-center gap-2 text-xs hover:text-primary">
                        <span className="font-mono">{row.incident_number}</span>
                        <StatusBadge status={row.incident_status} />
                      </Link>
                    ) : (
                      <span className="text-subtle">—</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </div>
  );
}
