"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { ErrorBanner, PageHeader } from "@/components/app-shell";
import { useLiveRefresh } from "@/components/event-stream";
import { StatusBadge, ToneIcon, toneFor } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { dateTime, money, providerLabel } from "@/lib/format";

const STATUSES = ["", "CREATED", "PROCESSING", "PENDING", "SUCCESS", "SETTLED", "FAILED", "UNKNOWN", "REFUND_PENDING", "REFUNDED"];
const PAGE = 50;

function SystemCell({ value }: { value: string }) {
  const tone = toneFor(value);
  return (
    <span className="inline-flex items-center gap-1 font-mono text-[11px] text-muted" title={value}>
      <ToneIcon tone={tone} className={tone === "good" ? "text-good" : tone === "critical" ? "text-critical" : tone === "warning" ? "text-warning" : "text-subtle"} />
      {value}
    </span>
  );
}

export default function PaymentsPage() {
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(0);
  const { data, error, refresh } = useApi(
    () => api.payments({ limit: PAGE, offset: page * PAGE, status, search }),
    [search, status, page],
    20000,
  );

  useLiveRefresh(refresh, (e) => e.event.startsWith("PAYMENT") || e.event === "DOWNSTREAM_UPDATED");

  return (
    <div>
      <PageHeader title="Payments" description="Live PayPal Sandbox payments and historical PayFlow records, with the state reported by every system." />
      {error && <ErrorBanner message={error} />}
      <div className="mb-3 flex flex-wrap gap-2">
        <Input
          placeholder="Search transaction or customer…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(0);
          }}
          className="max-w-xs"
        />
        <Select
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(0);
          }}
          className="w-44"
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s || "All statuses"}
            </option>
          ))}
        </Select>
        {data && <span className="ml-auto self-center text-xs text-subtle">{data.total} payments</span>}
      </div>
      <Card>
        {!data ? (
          <Skeleton className="m-5 h-64" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Transaction</TableHead>
                <TableHead className="text-right">Amount</TableHead>
                <TableHead>Gateway</TableHead>
                <TableHead>Bank</TableHead>
                <TableHead>Merchant</TableHead>
                <TableHead>Ledger</TableHead>
                <TableHead>Webhook</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.items.map((p) => (
                <TableRow
                  key={p.id}
                  className="cursor-pointer hover:bg-panel-2/60"
                  onClick={() => router.push(`/payments/${p.transaction_id}`)}
                >
                  <TableCell>
                    <div className="font-mono text-[13px]">{p.transaction_id}</div>
                    <div className="text-[11px] text-subtle">
                      {providerLabel(p.provider)}
                      {p.provider_status && p.provider === "PAYPAL_SANDBOX" ? ` · PayPal ${p.provider_status}` : ""}
                    </div>
                  </TableCell>
                  <TableCell className="text-right font-mono text-[13px]">{money(p.amount, p.currency)}</TableCell>
                  <TableCell><SystemCell value={p.gateway_status} /></TableCell>
                  <TableCell><SystemCell value={p.bank_status} /></TableCell>
                  <TableCell><SystemCell value={p.merchant_status} /></TableCell>
                  <TableCell><SystemCell value={p.ledger_status} /></TableCell>
                  <TableCell><SystemCell value={p.webhook_status} /></TableCell>
                  <TableCell><StatusBadge status={p.overall_status} /></TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted">{dateTime(p.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
      {data && data.total > PAGE && (
        <div className="mt-3 flex items-center justify-end gap-2 text-xs text-muted">
          <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
            Previous
          </Button>
          <span>
            Page {page + 1} of {Math.ceil(data.total / PAGE)}
          </span>
          <Button variant="outline" size="sm" disabled={(page + 1) * PAGE >= data.total} onClick={() => setPage(page + 1)}>
            Next
          </Button>
        </div>
      )}
    </div>
  );
}
