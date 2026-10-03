"use client";

import { Bot, ChevronRight } from "lucide-react";
import { useRouter } from "next/navigation";

import { SeverityBadge, StatusBadge } from "@/components/status";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { money, providerLabel, relative } from "@/lib/format";
import type { IncidentSummary } from "@/types/api";

const AI_LABEL: Record<string, string> = {
  ANALYZED: "Groq analysed",
  ANALYZED_FALLBACK: "Analysed",
  ANALYZING: "Analysing",
  QUEUED: "Queued",
};

export function IncidentTable({ incidents, empty }: { incidents: IncidentSummary[]; empty?: string }) {
  const router = useRouter();
  if (!incidents.length) {
    return <p className="px-5 py-10 text-center text-sm text-muted">{empty ?? "No incidents."}</p>;
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Transaction</TableHead>
          <TableHead className="text-right">Amount</TableHead>
          <TableHead>Incident</TableHead>
          <TableHead>Severity</TableHead>
          <TableHead>AI status</TableHead>
          <TableHead>Action</TableHead>
          <TableHead>Status</TableHead>
          <TableHead className="w-6" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {incidents.map((i) => (
          <TableRow
            key={i.id}
            className="cursor-pointer hover:bg-panel-2/60"
            onClick={() => router.push(`/incidents/${i.id}`)}
          >
            <TableCell>
              <div className="font-mono text-[13px] text-foreground">{i.transaction_id}</div>
              <div className="text-[11px] text-subtle">
                {i.incident_number} · {providerLabel(i.provider)} · {relative(i.created_at)}
              </div>
              {i.injected_scenario && (
                <span className="mt-0.5 inline-block rounded bg-warning/10 px-1 text-[10px] text-warning">
                  injected: {i.injected_scenario}
                </span>
              )}
              {i.failure_source === "PAYPAL_PROVIDER_FAILURE" && (
                <span className="mt-0.5 inline-block rounded bg-critical/10 px-1 text-[10px] text-critical">PayPal provider failure</span>
              )}
            </TableCell>
            <TableCell className="text-right font-mono text-[13px]">{money(i.amount, i.currency)}</TableCell>
            <TableCell className="font-mono text-[12px]">{i.type}</TableCell>
            <TableCell>
              <SeverityBadge severity={i.severity} />
            </TableCell>
            <TableCell>
              <span className="flex items-center gap-1.5 text-xs text-muted">
                <Bot className="size-3.5" />
                {AI_LABEL[i.ai_status] ?? i.ai_status}
                {i.confidence !== null && <span className="font-mono text-foreground">{Math.round(i.confidence * 100)}%</span>}
              </span>
            </TableCell>
            <TableCell>
              {i.recommended_action ? (
                <div className="flex flex-col gap-0.5">
                  <span className="font-mono text-[12px] text-foreground">{i.recommended_action}</span>
                  {i.action_status && <span className="text-[10px] uppercase tracking-wide text-subtle">{i.action_status.replaceAll("_", " ")}</span>}
                </div>
              ) : (
                <span className="text-subtle">—</span>
              )}
            </TableCell>
            <TableCell>
              <StatusBadge status={i.status} />
            </TableCell>
            <TableCell>
              <ChevronRight className="size-4 text-subtle" />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
