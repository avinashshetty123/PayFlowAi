"use client";

import { ArrowLeft, ExternalLink, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { ErrorBanner } from "@/components/app-shell";
import { useLiveRefresh } from "@/components/event-stream";
import { FailureInjectionPanel } from "@/components/failure-injection-panel";
import { SeverityBadge, StatusBadge } from "@/components/status";
import { SystemGrid } from "@/components/system-grid";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { dateTime, inrEquivalent, money, providerLabel, time } from "@/lib/format";

export default function PaymentDetailPage() {
  const { transactionId } = useParams<{ transactionId: string }>();
  const { data, error, refresh } = useApi(() => api.payment(transactionId), [transactionId], 15000);
  useLiveRefresh(refresh, (e) => e.transactionId === transactionId);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  if (error && !data) return <ErrorBanner message={error} />;
  if (!data) return <Skeleton className="h-96" />;
  const p = data.payment;
  const paypal = p.provider === "PAYPAL_SANDBOX";

  async function run(fn: () => Promise<{ message?: string }>) {
    setBusy(true);
    setMessage(null);
    try {
      setMessage((await fn()).message ?? "Done");
      await refresh();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <Link href="/payments" className="inline-flex items-center gap-1 text-xs text-muted hover:text-foreground">
        <ArrowLeft className="size-3.5" /> Payments
      </Link>
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4 px-5 py-4">
          <div>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
              <Badge tone={paypal ? "info" : "neutral"}>{providerLabel(p.provider)}</Badge>
              {p.scenario && <Badge>{p.scenario}</Badge>}
              {p.payer?.email && <span>payer {p.payer.email}</span>}
            </div>
            <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
              <span className="font-mono text-2xl font-semibold">{p.transaction_id}</span>
              <span className="text-2xl font-semibold">{money(p.amount, p.currency)}</span>
              {inrEquivalent(p.amount, p.currency) && <span className="text-xs text-subtle">{inrEquivalent(p.amount, p.currency)}</span>}
            </div>
            <div className="mt-1 text-xs text-subtle">
              Created {dateTime(p.created_at)}
              {p.provider_order_id && ` · PayPal order ${p.provider_order_id}`}
              {p.provider_capture_id && ` · capture ${p.provider_capture_id}`}
              {p.provider_status && ` · PayPal status ${p.provider_status}`}
            </div>
          </div>
          <div className="flex flex-col items-end gap-2">
            <StatusBadge status={p.overall_status} className="px-2.5 py-1 text-sm" />
            <span className="text-[11px] text-muted">reconciliation: {p.reconciliation_status}</span>
            <div className="flex flex-wrap justify-end gap-2">
              {data.approve_url && (
                <>
                  <Button asChild size="sm">
                    <a href={data.approve_url} target="paypal-sandbox-checkout" rel="noreferrer">
                      OPEN PAYPAL SANDBOX CHECKOUT <ExternalLink />
                    </a>
                  </Button>
                  <Button size="sm" variant="outline" disabled={busy}
                    onClick={() => run(() => api.capture({ transaction_id: p.transaction_id }))}>
                    Capture
                  </Button>
                </>
              )}
              {paypal && p.provider_capture_id && p.gateway_status === "SUCCESS" && p.merchant_status !== "REFUND_REQUESTED" && (
                <Button size="sm" variant="outline" disabled={busy}
                  onClick={() => run(() => api.refundRequest(p.transaction_id, "Customer requested a refund"))}>
                  <RotateCcw /> Request refund
                </Button>
              )}
            </div>
            {message && <span className="max-w-sm text-right text-[11px] text-muted">{message}</span>}
          </div>
        </div>
        <CardContent>
          <SystemGrid current={{ gateway: p.gateway_status, bank: p.bank_status, merchant: p.merchant_status, ledger: p.ledger_status, webhook: p.webhook_status }} />
        </CardContent>
      </Card>

      {data.incidents.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Incidents</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            {data.incidents.map((i) => (
              <Link key={i.id} href={`/incidents/${i.id}`} className="flex items-center gap-3 rounded-md border border-border px-3 py-2 hover:bg-panel-2">
                <span className="font-mono text-xs">{i.incident_number}</span>
                <span className="font-mono text-xs text-muted">{i.type}</span>
                <SeverityBadge severity={i.severity} />
                <span className="ml-auto"><StatusBadge status={i.status} /></span>
              </Link>
            ))}
          </CardContent>
        </Card>
      )}

      {paypal && <FailureInjectionPanel transactionId={p.transaction_id} />}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Event stream</CardTitle></CardHeader>
          <Table>
            <TableBody>
              {data.events.map((e) => (
                <TableRow key={e.id}>
                  <TableCell className="font-mono text-xs text-muted">{time(e.created_at)}</TableCell>
                  <TableCell className="font-mono text-[11px] text-subtle">{e.source}</TableCell>
                  <TableCell>
                    <div className="text-[13px]">{e.payload.label ?? e.event_type}</div>
                    {e.payload.detail && <div className="text-[11px] text-muted">{String(e.payload.detail)}</div>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
        <div className="space-y-4">
          {data.provider_transactions.length > 0 && (
            <Card>
              <CardHeader><CardTitle>PayPal Sandbox API calls</CardTitle></CardHeader>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Call</TableHead>
                    <TableHead>Reference</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Idempotency key</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.provider_transactions.map((t) => (
                    <TableRow key={t.id}>
                      <TableCell className="font-mono text-xs">{t.kind}</TableCell>
                      <TableCell className="font-mono text-[11px] text-muted">{t.provider_reference ?? "—"}</TableCell>
                      <TableCell><StatusBadge status={t.status} /></TableCell>
                      <TableCell className="font-mono text-[10px] text-subtle" title={t.error ?? t.debug_id ?? ""}>{t.idempotency_key ?? "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>
          )}
          {paypal && (
            <Card>
              <CardHeader><CardTitle>PayPal webhooks</CardTitle></CardHeader>
              {data.webhook_events.length ? (
                <Table>
                  <TableBody>
                    {data.webhook_events.map((w) => (
                      <TableRow key={w.id}>
                        <TableCell className="font-mono text-[11px]">{w.event_type}</TableCell>
                        <TableCell>
                          <StatusBadge status={w.signature_verified ? "PASSED" : w.signature_verified === false ? "FAILED" : "PENDING"}
                            label={w.signature_verified ? "verified" : w.signature_verified === false ? "rejected" : "verifying"} />
                        </TableCell>
                        <TableCell className="font-mono text-[11px] text-muted">{w.processing_status}</TableCell>
                        <TableCell className="text-[11px] text-subtle">×{w.delivery_count}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <CardContent className="text-xs text-muted">
                  No webhooks received{p.webhook_status === "NOT_CONFIGURED" ? " (PAYPAL_WEBHOOK_ID not configured; API responses are authoritative)" : " yet"}.
                </CardContent>
              )}
            </Card>
          )}
          <Card>
            <CardHeader><CardTitle>Ledger entries</CardTitle></CardHeader>
            <Table>
              <TableBody>
                {data.ledger_entries.map((l) => (
                  <TableRow key={l.id}>
                    <TableCell className="font-mono text-xs">{l.entry_type}</TableCell>
                    <TableCell className="text-right font-mono text-[13px]">{money(l.amount, p.currency)}</TableCell>
                    <TableCell><StatusBadge status={l.status} /></TableCell>
                    <TableCell className="text-xs text-muted">{time(l.updated_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
          <Card>
            <CardHeader><CardTitle>{paypal ? "Settlement (PayPal capture)" : "Bank settlement"}</CardTitle></CardHeader>
            {data.bank_transactions.length ? (
              <Table>
                <TableBody>
                  {data.bank_transactions.map((b) => (
                    <TableRow key={b.id}>
                      <TableCell className="font-mono text-xs">{b.bank_reference}</TableCell>
                      <TableCell className="text-right font-mono text-[13px]">{money(b.amount, p.currency)}</TableCell>
                      <TableCell><StatusBadge status={b.status} /></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <CardContent className="text-xs text-muted">No settlement yet.</CardContent>
            )}
          </Card>
          <Card>
            <CardHeader><CardTitle>Merchant order</CardTitle></CardHeader>
            <Table>
              <TableBody>
                {data.merchant_transactions.map((m) => (
                  <TableRow key={m.id}>
                    <TableCell className="font-mono text-xs">{m.order_id}</TableCell>
                    <TableCell className="text-right font-mono text-[13px]">{money(m.amount, p.currency)}</TableCell>
                    <TableCell><StatusBadge status={m.status} /></TableCell>
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
