"use client";

import { ArrowRight, CheckCircle2, CircleDashed, ExternalLink, FlaskConical, Radio, ShieldAlert, Sparkles, Wallet } from "lucide-react";
import Link from "next/link";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { EVENT_LABELS, useEventStream } from "@/components/event-stream";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input, Label, Select } from "@/components/ui/input";
import { useApi } from "@/hooks/use-api";
import { api } from "@/lib/api";
import { inrEquivalent, money, time } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { CreatedOrder, LiveEvent } from "@/types/api";

const DEMOS = [
  {
    value: "LEDGER_MISMATCH",
    label: "Real-time ledger mismatch",
    icon: Sparkles,
    hint: "Real PayPal Sandbox payment. PayFlow then injects a ledger write failure into its own ledger and recovers autonomously.",
    defaultFailure: "LEDGER_WRITE_FAILURE",
  },
  {
    value: "REFUND_REQUIRES_APPROVAL",
    label: "Refund requires approval",
    icon: ShieldAlert,
    hint: "Real PayPal Sandbox payment, then the merchant cancels. Refunds above $25 need a human; approval calls the PayPal Sandbox refund API.",
    defaultFailure: "NONE",
  },
  {
    value: "PAYMENT_ONLY",
    label: "Plain sandbox payment",
    icon: Wallet,
    hint: "Real PayPal Sandbox payment with no injected failure. Use the failure-injection panel afterwards.",
    defaultFailure: "NONE",
  },
];

const NEGATIVE_CODES = ["INSTRUMENT_DECLINED", "TRANSACTION_REFUSED", "INSUFFICIENT_FUNDS", "INTERNAL_SERVER_ERROR"];

type Step = { key: string; label: string; events: string[]; optional?: boolean };

const STEPS: Step[] = [
  { key: "created", label: "Payment created", events: ["PAYMENT_CREATED"] },
  { key: "order", label: "PayPal order created", events: ["PAYMENT_APPROVAL_STARTED"] },
  { key: "approved", label: "Buyer approved (PayPal)", events: ["PAYMENT_APPROVED"] },
  { key: "capture", label: "Capture completed (PayPal)", events: ["PAYMENT_CAPTURE_COMPLETED", "PAYMENT_CAPTURE_FAILED"] },
  { key: "webhook", label: "Webhook received & verified", events: ["WEBHOOK_VERIFIED", "WEBHOOK_DROPPED", "WEBHOOK_DELAYED"], optional: true },
  { key: "downstream", label: "Merchant + ledger updated (injection point)", events: ["DOWNSTREAM_UPDATED"], optional: true },
  { key: "recon", label: "Reconciliation", events: ["RECONCILIATION_COMPLETED"] },
  { key: "incident", label: "Incident created", events: ["INCIDENT_CREATED"], optional: true },
  { key: "ai", label: "AI investigation", events: ["INVESTIGATION_COMPLETED"], optional: true },
  { key: "policy", label: "Policy decision", events: ["POLICY_EVALUATED"], optional: true },
  { key: "action", label: "Action executed", events: ["ACTION_EXECUTED", "ACTION_AWAITING_APPROVAL"], optional: true },
  { key: "verify", label: "Verification", events: ["VERIFICATION_COMPLETED"], optional: true },
  { key: "done", label: "Resolved", events: ["INCIDENT_RESOLVED", "INCIDENT_ESCALATED"], optional: true },
];

type Ctx = { open: (preset?: { demo?: string }) => void; activeTransaction: string | null };
const LiveDemoContext = createContext<Ctx>({ open: () => undefined, activeTransaction: null });

export function useLiveDemo() {
  return useContext(LiveDemoContext);
}

function Progress({ order, events }: { order: CreatedOrder; events: LiveEvent[] }) {
  const txn = order.payment.transaction_id;
  const mine = events.filter((e) => e.transactionId === txn);
  const seen = new Map<string, LiveEvent>();
  for (const event of mine) if (!seen.has(event.event)) seen.set(event.event, event);
  const incident = mine.find((e) => e.event === "INCIDENT_CREATED" && e.incidentId);
  const awaiting = seen.has("ACTION_AWAITING_APPROVAL");
  const resolved = seen.get("INCIDENT_RESOLVED");
  const firstPending = STEPS.findIndex((s) => !s.events.some((e) => seen.has(e)) && !s.optional);

  return (
    <div className="space-y-3">
      <ol className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
        {STEPS.map((step, index) => {
          const hit = step.events.map((e) => seen.get(e)).find(Boolean);
          const running = !hit && index === firstPending;
          if (!hit && step.optional && !running && step.key !== "incident") {
            return (
              <li key={step.key} className="flex items-center gap-2 text-xs text-subtle">
                <CircleDashed className="size-3.5" /> {step.label}
              </li>
            );
          }
          return (
            <li key={step.key} className={cn("flex items-center gap-2 text-xs", hit ? "text-foreground" : "text-subtle")}>
              {hit ? (
                <CheckCircle2 className={cn("size-3.5", hit.event.includes("FAILED") || hit.event.includes("DROPPED") ? "text-critical" : "text-good")} />
              ) : (
                <CircleDashed className={cn("size-3.5", running && "animate-spin text-[#8fb0f5]")} />
              )}
              <span>{step.label}</span>
              {hit && <span className="ml-auto font-mono text-[10px] text-subtle">{time(hit.timestamp)}</span>}
            </li>
          );
        })}
      </ol>
      {incident && (
        <div className={cn("flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2",
          resolved ? "border-good/40 bg-good/5" : awaiting ? "border-warning/40 bg-warning/5" : "border-info/40 bg-info/5")}>
          <span className="text-xs text-foreground">
            {resolved ? "Incident resolved autonomously" : awaiting ? "Waiting for human approval" : "PayFlow is handling an incident"}
          </span>
          <Button asChild size="sm">
            <Link href={awaiting ? "/approvals" : `/incidents/${incident.incidentId}`}>
              {awaiting ? "OPEN APPROVAL" : "OPEN INCIDENT"} <ArrowRight />
            </Link>
          </Button>
        </div>
      )}
    </div>
  );
}

export function LiveDemoProvider({ children }: { children: React.ReactNode }) {
  const [isOpen, setOpen] = useState(false);
  const [demo, setDemo] = useState("LEDGER_MISMATCH");
  const [amount, setAmount] = useState("50.00");
  const [failure, setFailure] = useState("LEDGER_WRITE_FAILURE");
  const [verificationTimeout, setVerificationTimeout] = useState(false);
  const [negative, setNegative] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [order, setOrder] = useState<CreatedOrder | null>(null);
  const [captureMessage, setCaptureMessage] = useState<string | null>(null);
  const { events, connected } = useEventStream();
  const { data: catalog } = useApi(api.failureScenarios, [], undefined);
  const { data: health } = useApi(api.health, [], undefined);

  const open = useCallback((preset?: { demo?: string }) => {
    const chosen = DEMOS.find((d) => d.value === preset?.demo) ?? null;
    if (chosen) {
      setDemo(chosen.value);
      setFailure(chosen.defaultFailure);
    }
    setError(null);
    setOpen(true);
  }, []);
  const close = useCallback(() => setOpen(false), []);

  // The PayPal return page (popup) tells us when the buyer approved and capture ran.
  useEffect(() => {
    const onMessage = (msg: MessageEvent) => {
      if (msg.data?.type === "payflow:capture") setCaptureMessage(msg.data.message);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const info = DEMOS.find((d) => d.value === demo)!;
  const amountValue = Number(amount);

  async function start() {
    setBusy(true);
    setError(null);
    setOrder(null);
    setCaptureMessage(null);
    try {
      const created = await api.liveDemo({
        demo, amount: amountValue, failure_scenario: failure === "NONE" ? null : failure,
        verification_timeout: verificationTimeout, negative_test: negative || null,
      });
      setOrder(created);
      if (created.approve_url) window.open(created.approve_url, "paypal-sandbox-checkout", "width=520,height=760");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function captureNow() {
    if (!order) return;
    setBusy(true);
    try {
      const result = await api.capture({ transaction_id: order.payment.transaction_id });
      setCaptureMessage(result.message);
    } catch (err) {
      setCaptureMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const scenarioOptions = useMemo(() => (catalog?.scenarios ?? []).filter((s) => s.available), [catalog]);
  const mine = order ? events.filter((e) => e.transactionId === order.payment.transaction_id) : [];
  const captured = mine.some((e) => e.event === "PAYMENT_CAPTURE_COMPLETED" || e.event === "PAYMENT_CAPTURE_FAILED");

  return (
    <LiveDemoContext.Provider value={{ open, activeTransaction: order?.payment.transaction_id ?? null }}>
      {children}
      <Dialog
        open={isOpen}
        onClose={close}
        title="Start live demo"
        description="Creates a real PayPal Sandbox order (no real money). You approve it as the sandbox buyer; PayFlow takes it from there."
        className="max-w-3xl"
      >
        {!order ? (
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              {DEMOS.map((d) => (
                <button
                  key={d.value}
                  onClick={() => {
                    setDemo(d.value);
                    setFailure(d.defaultFailure);
                  }}
                  className={cn("rounded-md border px-3 py-2.5 text-left transition-colors",
                    demo === d.value ? "border-primary/60 bg-primary/10" : "border-border-strong hover:border-primary/40")}
                >
                  <d.icon className="mb-1 size-4 text-primary" />
                  <div className="text-xs font-medium text-foreground">{d.label}</div>
                </button>
              ))}
            </div>
            <p className="text-xs text-muted">{info.hint}</p>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-[150px_1fr]">
              <div>
                <Label htmlFor="demo-amount">Amount (USD)</Label>
                <Input id="demo-amount" type="number" min={1} step="0.01" value={amount}
                  onChange={(e) => setAmount(e.target.value)} className="font-mono" />
                {amountValue > 0 && <p className="mt-1 text-[11px] text-subtle">{inrEquivalent(amountValue, "USD")}</p>}
              </div>
              <div>
                <Label htmlFor="demo-failure">
                  <span className="rounded bg-warning/15 px-1 py-px text-[10px] font-semibold tracking-wider text-warning">DEMO FAILURE INJECTION</span>{" "}
                  PayFlow infrastructure only
                </Label>
                <Select id="demo-failure" value={failure} onChange={(e) => setFailure(e.target.value)} className="font-mono text-xs">
                  {scenarioOptions.map((s) => (
                    <option key={s.scenario} value={s.scenario}>
                      {s.label}
                    </option>
                  ))}
                </Select>
                <label className="mt-2 flex items-center gap-2 text-xs text-muted">
                  <input type="checkbox" checked={verificationTimeout} onChange={(e) => setVerificationTimeout(e.target.checked)} />
                  Also time out the first verification attempt
                </label>
                {catalog && !catalog.webhooks_configured && (
                  <p className="mt-1 text-[11px] text-subtle">Webhook scenarios need PAYPAL_WEBHOOK_ID and a public tunnel.</p>
                )}
              </div>
            </div>

            {health?.negative_testing && (
              <div>
                <Label htmlFor="demo-negative">
                  <span className="rounded bg-critical/15 px-1 py-px text-[10px] font-semibold tracking-wider text-[#f87171]">PAYPAL NEGATIVE TESTING</span>{" "}
                  PayPal itself returns this error at capture
                </Label>
                <Select id="demo-negative" value={negative} onChange={(e) => setNegative(e.target.value)} className="font-mono text-xs">
                  <option value="">None (real success)</option>
                  {NEGATIVE_CODES.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </Select>
              </div>
            )}

            {error && <p className="rounded-md border border-critical/40 bg-critical/10 px-3 py-2 text-xs text-[#f87171]">{error}</p>}
            <Button className="w-full" size="lg" onClick={start} disabled={busy || !(amountValue > 0)}>
              <Radio />
              {busy ? "Creating PayPal Sandbox order…" : "START LIVE DEMO"}
            </Button>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="font-mono text-base font-semibold">{order.payment.transaction_id}</div>
                <div className="text-xs text-muted">
                  {money(order.payment.amount, "USD")} · {inrEquivalent(order.payment.amount, "USD")} · PayPal order{" "}
                  <span className="font-mono">{order.order_id}</span>
                </div>
                {order.armed_failures.length > 0 && (
                  <div className="mt-1.5 flex items-center gap-1.5 text-[11px] text-warning">
                    <FlaskConical className="size-3.5" /> Armed in PayFlow: {order.armed_failures.join(", ")}
                  </div>
                )}
              </div>
              <span className={cn("flex items-center gap-1.5 text-[11px]", connected ? "text-good" : "text-warning")}>
                <span className={cn("size-1.5 rounded-full", connected ? "bg-good pulse-ring" : "bg-warning")} />
                {connected ? "Live" : "Reconnecting"}
              </span>
            </div>

            {!captured && order.approve_url && (
              <div className="rounded-md border border-primary/40 bg-primary/5 p-3">
                <div className="mb-2 text-xs text-foreground">
                  Approve the payment as your <b>sandbox buyer</b>. PayFlow captures automatically when you return.
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button asChild size="sm">
                    <a href={order.approve_url} target="paypal-sandbox-checkout" rel="noreferrer">
                      OPEN PAYPAL SANDBOX CHECKOUT <ExternalLink />
                    </a>
                  </Button>
                  <Button variant="outline" size="sm" onClick={captureNow} disabled={busy}>
                    I approved: capture now
                  </Button>
                </div>
              </div>
            )}
            {captureMessage && <p className="text-xs text-muted">{captureMessage}</p>}

            <Progress order={order} events={events} />

            <div className="max-h-40 overflow-y-auto rounded-md border border-border bg-background/60 p-2 font-mono text-[11px]">
              {mine.length === 0 && <div className="text-subtle">Waiting for events…</div>}
              {mine.map((e) => (
                <div key={e.id} className="flex gap-2">
                  <span className="text-subtle">{time(e.timestamp)}</span>
                  <span className="text-foreground">{EVENT_LABELS[e.event] ?? e.event}</span>
                  {e.data.reason && <span className="truncate text-muted">{String(e.data.reason)}</span>}
                </div>
              ))}
            </div>
            <div className="flex justify-between">
              <Button variant="ghost" size="sm" onClick={() => setOrder(null)}>New demo</Button>
              <Button asChild variant="outline" size="sm" onClick={close}>
                <Link href={`/payments/${order.payment.transaction_id}`}>Open payment</Link>
              </Button>
            </div>
          </div>
        )}
      </Dialog>
    </LiveDemoContext.Provider>
  );
}
